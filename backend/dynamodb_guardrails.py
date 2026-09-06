"""
DynamoDB-backed rate limiter and circuit breaker.

Provides shared state across multiple instances/workers.
Uses DynamoDB atomic counters for thread-safety without explicit locks.
"""

import time
import threading
import boto3
from boto3.dynamodb.conditions import Key
from config import (
    AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_DEFAULT_REGION,
    DYNAMODB_RATE_LIMIT_TABLE, DYNAMODB_CIRCUIT_BREAKER_TABLE,
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

def init_guardrails_tables():
    client = _get_dynamodb_client()
    existing = client.list_tables()["TableNames"]

    # Rate limiter table
    if DYNAMODB_RATE_LIMIT_TABLE not in existing:
        client.create_table(
            TableName=DYNAMODB_RATE_LIMIT_TABLE,
            KeySchema=[{"AttributeName": "client_ip", "KeyType": "HASH"}],
            AttributeDefinitions=[
                {"AttributeName": "client_ip", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        waiter = client.get_waiter("table_exists")
        waiter.wait(TableName=DYNAMODB_RATE_LIMIT_TABLE)
        client.update_time_to_live(
            TableName=DYNAMODB_RATE_LIMIT_TABLE,
            TimeToLiveSpecification={"Enabled": True, "AttributeName": "ttl"},
        )

    # Circuit breaker table
    if DYNAMODB_CIRCUIT_BREAKER_TABLE not in existing:
        client.create_table(
            TableName=DYNAMODB_CIRCUIT_BREAKER_TABLE,
            KeySchema=[{"AttributeName": "service_name", "KeyType": "HASH"}],
            AttributeDefinitions=[
                {"AttributeName": "service_name", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        waiter = client.get_waiter("table_exists")
        waiter.wait(TableName=DYNAMODB_CIRCUIT_BREAKER_TABLE)


# ---------------------------------------------------------------------------
# DynamoDB Rate Limiter
# ---------------------------------------------------------------------------

class DynamoDBRateLimiter:
    """
    Per-IP rate limiter backed by DynamoDB.

    Stores timestamps of recent requests per IP.
    Uses DynamoDB TTL for automatic cleanup of expired entries.
    """

    def __init__(self, max_requests: int = 30, window: int = 60):
        self.max_requests = max_requests
        self.window = window
        self._local_cache: dict[str, list[float]] = {}  # L1 cache for hot IPs
        self._local_max = 200
        self._lock = threading.Lock()

    def _get_table(self):
        return _get_dynamodb_resource().Table(DYNAMODB_RATE_LIMIT_TABLE)

    def is_allowed(self, key: str) -> bool:
        now = time.time()
        cutoff = now - self.window

        # L1 cache check for fast path
        with self._lock:
            if key in self._local_cache:
                timestamps = [t for t in self._local_cache[key] if t > cutoff]
                self._local_cache[key] = timestamps
                if len(timestamps) >= self.max_requests:
                    metrics.rate_limit_rejected.inc()
                    return False
                timestamps.append(now)
                metrics.rate_limit_allowed.inc()
                # Write through to DynamoDB asynchronously
                self._write_timestamps(key, timestamps, now)
                return True

        # DynamoDB lookup
        try:
            table = self._get_table()
            response = table.get_item(Key={"client_ip": key})
            item = response.get("Item")

            if item:
                timestamps = [float(t) for t in item.get("timestamps", [])]
                timestamps = [t for t in timestamps if t > cutoff]
            else:
                timestamps = []

            if len(timestamps) >= self.max_requests:
                metrics.rate_limit_rejected.inc()
                return False

            timestamps.append(now)

            # Update L1 cache
            with self._lock:
                if len(self._local_cache) >= self._local_max:
                    oldest_key = min(
                        self._local_cache,
                        key=lambda k: self._local_cache[k][0] if self._local_cache[k] else 0,
                    )
                    del self._local_cache[oldest_key]
                self._local_cache[key] = timestamps

            # Write to DynamoDB
            self._write_timestamps(key, timestamps, now)

            metrics.rate_limit_allowed.inc()
            return True

        except Exception:
            # On DynamoDB failure, allow the request (fail open)
            metrics.rate_limit_allowed.inc()
            return True

    def _write_timestamps(self, key: str, timestamps: list[float], now: float):
        try:
            table = self._get_table()
            table.put_item(Item={
                "client_ip": key,
                "timestamps": [str(t) for t in timestamps[-self.max_requests:]],
                "ttl": int(now + self.window + 60),  # Extra 60s buffer
            })
        except Exception:
            pass  # Write failure is non-fatal

    def remaining(self, key: str) -> int:
        now = time.time()
        cutoff = now - self.window

        with self._lock:
            if key in self._local_cache:
                timestamps = [t for t in self._local_cache[key] if t > cutoff]
                return max(0, self.max_requests - len(timestamps))

        try:
            table = self._get_table()
            response = table.get_item(Key={"client_ip": key})
            item = response.get("Item")
            if item:
                timestamps = [float(t) for t in item.get("timestamps", [])]
                timestamps = [t for t in timestamps if t > cutoff]
                return max(0, self.max_requests - len(timestamps))
        except Exception:
            pass

        return self.max_requests

    def reset(self, key: str):
        with self._lock:
            self._local_cache.pop(key, None)
        try:
            table = self._get_table()
            table.delete_item(Key={"client_ip": key})
        except Exception:
            pass


# ---------------------------------------------------------------------------
# DynamoDB Circuit Breaker
# ---------------------------------------------------------------------------

class DynamoDBCircuitBreaker:
    """
    Circuit breaker backed by DynamoDB.

    State is shared across all instances.
    Uses DynamoDB atomic counters for failure counting.
    """

    def __init__(self, failure_threshold: int = 3, recovery_timeout: float = 30):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self._local_state: dict[str, dict] = {}  # L1 cache
        self._lock = threading.Lock()

    def _get_table(self):
        return _get_dynamodb_resource().Table(DYNAMODB_CIRCUIT_BREAKER_TABLE)

    def _get_service_state(self, service: str = "bedrock") -> dict:
        # Check L1 cache
        with self._lock:
            if service in self._local_state:
                state = self._local_state[service]
                if state.get("last_check", 0) > time.time() - 2:
                    return state

        # DynamoDB lookup
        try:
            table = self._get_table()
            response = table.get_item(Key={"service_name": service})
            item = response.get("Item")
            if item:
                state = {
                    "failures": int(item.get("failures", 0)),
                    "state": item.get("state", "closed"),
                    "last_failure_time": float(item.get("last_failure_time", 0)),
                    "last_check": time.time(),
                }
            else:
                state = {
                    "failures": 0,
                    "state": "closed",
                    "last_failure_time": 0,
                    "last_check": time.time(),
                }

            # Update L1 cache
            with self._lock:
                self._local_state[service] = state

            return state

        except Exception:
            # On DynamoDB failure, use local state or default to closed
            with self._lock:
                if service in self._local_state:
                    return self._local_state[service]
            return {"failures": 0, "state": "closed", "last_failure_time": 0, "last_check": time.time()}

    @property
    def state(self) -> str:
        service_state = self._get_service_state()
        current_state = service_state["state"]

        if current_state == "open":
            if time.time() - service_state["last_failure_time"] > self.recovery_timeout:
                # Transition to half_open
                self._update_state("bedrock", "half_open", service_state["failures"])
                return "half_open"

        return current_state

    def record_success(self):
        service_state = self._get_service_state()
        old_state = service_state["state"]

        self._update_state("bedrock", "closed", 0)

        if old_state != "closed":
            metrics.circuit_breaker_events.inc(transition=f"{old_state}->closed")
        metrics.circuit_breaker_state.set(0)

    def record_failure(self):
        service_state = self._get_service_state()
        new_failures = service_state["failures"] + 1
        now = time.time()

        if new_failures >= self.failure_threshold:
            new_state = "open"
        else:
            new_state = service_state["state"]

        self._update_state("bedrock", new_state, new_failures, now)

        old_state = service_state["state"]
        if new_state != old_state:
            metrics.circuit_breaker_events.inc(transition=f"{old_state}->{new_state}")

        state_map = {"closed": 0, "open": 1, "half_open": 2}
        metrics.circuit_breaker_state.set(state_map.get(new_state, 0))

    def allow_request(self) -> bool:
        current_state = self.state
        if current_state in ("closed", "half_open"):
            return True
        return False

    def _update_state(self, service: str, state: str, failures: int,
                      last_failure_time: float = None):
        now = time.time()
        update_data = {
            "failures": failures,
            "state": state,
            "last_failure_time": last_failure_time or now,
            "last_check": now,
        }

        # Update L1 cache
        with self._lock:
            self._local_state[service] = update_data

        # Write to DynamoDB
        try:
            table = self._get_table()
            table.put_item(Item={
                "service_name": service,
                "failures": failures,
                "state": state,
                "last_failure_time": str(last_failure_time or now),
            })
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Global instances
# ---------------------------------------------------------------------------

rate_limiter = DynamoDBRateLimiter()
circuit_breaker = DynamoDBCircuitBreaker()
