"""
DynamoDB-backed cache with TTL and LRU eviction.

Replaces the in-memory dict caches for embeddings and chat completions.
Cache persists across restarts and is shared across multiple instances/workers.
"""

import json
import time
import threading
import boto3
from botocore.exceptions import ClientError
from config import (
    AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_DEFAULT_REGION,
    DYNAMODB_CACHE_TABLE,
)
from metrics import metrics


def _get_dynamodb_resource():
    session = boto3.Session(
        aws_access_key_id=AWS_ACCESS_KEY_ID or None,
        aws_secret_access_key=AWS_SECRET_ACCESS_KEY or None,
        region_name=AWS_DEFAULT_REGION,
    )
    return session.resource("dynamodb", region_name=AWS_DEFAULT_REGION)


def _get_dynamodb_client():
    session = boto3.Session(
        aws_access_key_id=AWS_ACCESS_KEY_ID or None,
        aws_secret_access_key=AWS_SECRET_ACCESS_KEY or None,
        region_name=AWS_DEFAULT_REGION,
    )
    return session.client("dynamodb", region_name=AWS_DEFAULT_REGION)


# ---------------------------------------------------------------------------
# Table init
# ---------------------------------------------------------------------------

def init_cache_table():
    client = _get_dynamodb_client()
    existing = client.list_tables()["TableNames"]

    if DYNAMODB_CACHE_TABLE not in existing:
        client.create_table(
            TableName=DYNAMODB_CACHE_TABLE,
            KeySchema=[{"AttributeName": "cache_key", "KeyType": "HASH"}],
            AttributeDefinitions=[
                {"AttributeName": "cache_key", "AttributeType": "S"},
                {"AttributeName": "category", "AttributeType": "S"},
            ],
            GlobalSecondaryIndexes=[
                {
                    "IndexName": "category-index",
                    "KeySchema": [{"AttributeName": "category", "KeyType": "HASH"}],
                    "Projection": {"ProjectionType": "KEYS_ONLY"},
                }
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        waiter = client.get_waiter("table_exists")
        waiter.wait(TableName=DYNAMODB_CACHE_TABLE)

    # Enable TTL
    try:
        client.update_time_to_live(
            TableName=DYNAMODB_CACHE_TABLE,
            TimeToLiveSpecification={"Enabled": True, "AttributeName": "ttl"},
        )
    except ClientError:
        pass  # TTL already enabled or attribute doesn't exist yet


# ---------------------------------------------------------------------------
# DynamoDBCache
# ---------------------------------------------------------------------------

class DynamoDBCache:
    """
    DynamoDB-backed LRU cache with TTL.

    - Embeddings: TTL = 24 hours (rarely change)
    - Chat completions: TTL = 1 hour (query-specific)
    - LRU eviction: when items exceed max_size, oldest accessed items are deleted
    """

    def __init__(self, category: str, ttl_seconds: int = 3600, max_size: int = 500):
        self.category = category
        self.ttl_seconds = ttl_seconds
        self.max_size = max_size
        self._local_cache: dict[str, dict] = {}  # L1 cache for hot keys
        self._local_max = 100  # Keep 100 items in local memory
        self._lock = threading.Lock()
        self._eviction_counter = 0

    def _get_table(self):
        return _get_dynamodb_resource().Table(DYNAMODB_CACHE_TABLE)

    def get(self, key: str) -> dict | None:
        """Get a cached value. Returns None on miss."""
        # L1 local cache check
        with self._lock:
            if key in self._local_cache:
                item = self._local_cache[key]
                if item.get("expires_at", 0) > time.time():
                    metrics.record_cache_hit()
                    return item
                else:
                    del self._local_cache[key]

        # DynamoDB lookup
        try:
            table = self._get_table()
            response = table.get_item(Key={"cache_key": key})
            item = response.get("Item")
            if not item:
                metrics.record_cache_miss()
                return None

            # Check TTL
            if item.get("ttl", 0) < time.time():
                # Expired — delete lazily
                try:
                    table.delete_item(Key={"cache_key": key})
                except Exception:
                    pass
                metrics.record_cache_miss()
                return None

            # Deserialize value
            value = json.loads(item["value"])

            # Populate L1 cache
            with self._lock:
                if len(self._local_cache) >= self._local_max:
                    # Evict oldest from L1
                    oldest_key = min(
                        self._local_cache,
                        key=lambda k: self._local_cache[k].get("accessed_at", 0),
                    )
                    del self._local_cache[oldest_key]
                self._local_cache[key] = value

            metrics.record_cache_hit()
            return value

        except ClientError:
            metrics.record_cache_miss()
            return None

    def put(self, key: str, value: dict):
        """Store a value in the cache."""
        now = time.time()
        item = {
            "cache_key": key,
            "category": self.category,
            "value": json.dumps(value),
            "ttl": int(now + self.ttl_seconds),
            "created_at": int(now),
            "accessed_at": int(now),
        }

        # Update L1 cache
        with self._lock:
            if len(self._local_cache) >= self._local_max:
                oldest_key = min(
                    self._local_cache,
                    key=lambda k: self._local_cache[k].get("accessed_at", 0),
                )
                del self._local_cache[oldest_key]
            self._local_cache[key] = item

        # Write to DynamoDB
        try:
            table = self._get_table()
            table.put_item(Item=item)
        except ClientError:
            pass  # Cache write failure is non-fatal

        # Background eviction if over size
        self._eviction_counter += 1
        if self._eviction_counter % 50 == 0:
            self._evict_oldest()

    def delete(self, key: str):
        """Remove a cached value."""
        with self._lock:
            self._local_cache.pop(key, None)

        try:
            table = self._get_table()
            table.delete_item(Key={"cache_key": key})
        except ClientError:
            pass

    def _evict_oldest(self):
        """Delete oldest items when cache exceeds max_size."""
        try:
            table = self._get_table()
            response = table.query(
                IndexName="category-index",
                KeyConditionExpression="category = :c",
                ExpressionAttributeValues={":c": self.category},
                Limit=100,
            )
            items = response.get("Items", [])
            if len(items) > self._size():
                # Sort by created_at and delete oldest
                items.sort(key=lambda x: int(x.get("created_at", 0)))
                to_delete = items[: len(items) - self._size() + 50]  # Keep some headroom
                with table.batch_writer() as batch:
                    for item in to_delete:
                        batch.delete_item(Key={"cache_key": item["cache_key"]})
                        metrics.record_cache_evict()
        except ClientError:
            pass

    def _size(self) -> int:
        """Estimate cache size for this category."""
        try:
            table = self._get_table()
            response = table.query(
                IndexName="category-index",
                KeyConditionExpression="category = :c",
                ExpressionAttributeValues={":c": self.category},
                Select="COUNT",
            )
            return response.get("Count", 0)
        except ClientError:
            return 0

    def clear(self):
        """Clear all items in this category."""
        try:
            table = self._get_table()
            response = table.query(
                IndexName="category-index",
                KeyConditionExpression="category = :c",
                ExpressionAttributeValues={":c": self.category},
            )
            with table.batch_writer() as batch:
                for item in response.get("Items", []):
                    batch.delete_item(Key={"cache_key": item["cache_key"]})
        except ClientError:
            pass

        with self._lock:
            self._local_cache.clear()


# ---------------------------------------------------------------------------
# Pre-configured cache instances
# ---------------------------------------------------------------------------

embedding_cache = DynamoDBCache(
    category="embedding",
    ttl_seconds=86400,   # 24 hours — embeddings rarely change
    max_size=2000,
)

chat_cache = DynamoDBCache(
    category="chat",
    ttl_seconds=3600,    # 1 hour — query-specific
    max_size=500,
)


def cache_stats() -> dict:
    return {
        "embedding_cache_size": embedding_cache._size(),
        "chat_cache_size": chat_cache._size(),
    }
