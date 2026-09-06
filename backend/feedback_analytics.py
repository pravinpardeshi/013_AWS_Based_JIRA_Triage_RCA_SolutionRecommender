"""
Feedback Analytics and Prompt Improvement Engine

Collects star ratings from triage tickets, analyzes patterns,
and generates feedback-aware system prompts to improve triage quality over time.
"""

import time
import json
import threading
import boto3
from boto3.dynamodb.conditions import Key
from config import (
    AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_DEFAULT_REGION,
    DYNAMODB_FEEDBACK_TABLE, DYNAMODB_PROMPT_IMPROVEMENTS_TABLE,
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


def init_feedback_tables():
    client = _get_dynamodb_client()
    existing = client.list_tables()["TableNames"]

    # Feedback analytics table — stores aggregated feedback per ticket
    if DYNAMODB_FEEDBACK_TABLE not in existing:
        client.create_table(
            TableName=DYNAMODB_FEEDBACK_TABLE,
            KeySchema=[{"AttributeName": "ticket_ref", "KeyType": "HASH"}],
            AttributeDefinitions=[
                {"AttributeName": "ticket_ref", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        waiter = client.get_waiter("table_exists")
        waiter.wait(TableName=DYNAMODB_FEEDBACK_TABLE)

    # Prompt improvements table — stores LLM-suggested prompt adjustments
    if DYNAMODB_PROMPT_IMPROVEMENTS_TABLE not in existing:
        client.create_table(
            TableName=DYNAMODB_PROMPT_IMPROVEMENTS_TABLE,
            KeySchema=[{"AttributeName": "id", "KeyType": "HASH"}],
            AttributeDefinitions=[
                {"AttributeName": "id", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        waiter = client.get_waiter("table_exists")
        waiter.wait(TableName=DYNAMODB_PROMPT_IMPROVEMENTS_TABLE)


# ---------------------------------------------------------------------------
# Feedback Recording
# ---------------------------------------------------------------------------

def record_feedback(ticket_ref: str, rating: int, comment: str = None,
                    component: str = "", technology: str = "",
                    description: str = "", analysis: str = ""):
    """Store feedback for analytics. Called after user submits star rating."""
    from dynamodb import get_triage_ticket
    from datetime import datetime, timezone

    dynamodb = _get_dynamodb_resource()
    table = dynamodb.Table(DYNAMODB_FEEDBACK_TABLE)

    now = datetime.now(timezone.utc).isoformat()

    item = {
        "ticket_ref": ticket_ref,
        "rating": rating,
        "comment": comment or "",
        "component": component,
        "technology": technology,
        "description": description[:500],  # Truncate for storage
        "analysis_snippet": analysis[:1000],  # First 1000 chars of analysis
        "created_at": now,
    }
    table.put_item(Item=item)
    return item


# ---------------------------------------------------------------------------
# Feedback Analytics
# ---------------------------------------------------------------------------

def get_feedback_stats() -> dict:
    """Aggregate feedback statistics across all rated tickets."""
    dynamodb = _get_dynamodb_resource()
    table = dynamodb.Table(DYNAMODB_FEEDBACK_TABLE)

    result = table.scan()
    items = result.get("Items", [])

    if not items:
        return {
            "total_feedback": 0,
            "average_rating": 0,
            "rating_distribution": {1: 0, 2: 0, 3: 0, 4: 0, 5: 0},
            "low_rated_count": 0,
            "high_rated_count": 0,
            "recent_feedback": [],
            "by_component": {},
            "by_technology": {},
            "improvement_needed": [],
        }

    total = len(items)
    total_rating = sum(int(i.get("rating", 0)) for i in items)
    avg_rating = round(total_rating / total, 2) if total else 0

    # Rating distribution
    distribution = {1: 0, 2: 0, 3: 0, 4: 0, 5: 0}
    for item in items:
        r = int(item.get("rating", 0))
        if r in distribution:
            distribution[r] += 1

    # Low rated (1-2 stars) and high rated (4-5 stars)
    low_rated = [i for i in items if int(i.get("rating", 0)) <= 2]
    high_rated = [i for i in items if int(i.get("rating", 0)) >= 4]

    # Group by component
    by_component = {}
    for item in items:
        comp = item.get("component") or "Unknown"
        if comp not in by_component:
            by_component[comp] = {"count": 0, "total_rating": 0, "ratings": []}
        by_component[comp]["count"] += 1
        by_component[comp]["total_rating"] += int(item.get("rating", 0))
        by_component[comp]["ratings"].append(int(item.get("rating", 0)))

    for comp in by_component:
        r = by_component[comp]
        r["avg_rating"] = round(r["total_rating"] / r["count"], 2) if r["count"] else 0
        del r["ratings"]

    # Group by technology
    by_technology = {}
    for item in items:
        tech = item.get("technology") or "Unknown"
        if tech not in by_technology:
            by_technology[tech] = {"count": 0, "total_rating": 0, "ratings": []}
        by_technology[tech]["count"] += 1
        by_technology[tech]["total_rating"] += int(item.get("rating", 0))
        by_technology[tech]["ratings"].append(int(item.get("rating", 0)))

    for tech in by_technology:
        r = by_technology[tech]
        r["avg_rating"] = round(r["total_rating"] / r["count"], 2) if r["count"] else 0
        del r["ratings"]

    # Recent feedback (last 10)
    sorted_items = sorted(items, key=lambda x: x.get("created_at", ""), reverse=True)
    recent = []
    for item in sorted_items[:10]:
        recent.append({
            "ticket_ref": item.get("ticket_ref"),
            "rating": int(item.get("rating", 0)),
            "comment": item.get("comment", ""),
            "component": item.get("component", ""),
            "created_at": item.get("created_at"),
        })

    # Components/technologies needing improvement (avg < 3.0)
    improvement_needed = []
    for comp, stats in by_component.items():
        if stats["avg_rating"] < 3.0 and stats["count"] >= 2:
            improvement_needed.append({
                "type": "component",
                "name": comp,
                "avg_rating": stats["avg_rating"],
                "count": stats["count"],
            })
    for tech, stats in by_technology.items():
        if stats["avg_rating"] < 3.0 and stats["count"] >= 2:
            improvement_needed.append({
                "type": "technology",
                "name": tech,
                "avg_rating": stats["avg_rating"],
                "count": stats["count"],
            })

    return {
        "total_feedback": total,
        "average_rating": avg_rating,
        "rating_distribution": distribution,
        "low_rated_count": len(low_rated),
        "high_rated_count": len(high_rated),
        "recent_feedback": recent,
        "by_component": by_component,
        "by_technology": by_technology,
        "improvement_needed": improvement_needed,
    }


def get_low_rated_tickets() -> list[dict]:
    """Return tickets with low ratings (1-2 stars) for prompt review."""
    dynamodb = _get_dynamodb_resource()
    table = dynamodb.Table(DYNAMODB_FEEDBACK_TABLE)

    result = table.scan()
    items = result.get("Items", [])

    low_rated = []
    for item in items:
        if int(item.get("rating", 0)) <= 2:
            low_rated.append({
                "ticket_ref": item.get("ticket_ref"),
                "rating": int(item.get("rating", 0)),
                "comment": item.get("comment", ""),
                "component": item.get("component", ""),
                "technology": item.get("technology", ""),
                "description": item.get("description", ""),
                "analysis_snippet": item.get("analysis_snippet", ""),
                "created_at": item.get("created_at"),
            })

    return sorted(low_rated, key=lambda x: x.get("rating", 5))


# ---------------------------------------------------------------------------
# Prompt Improvement Engine
# ---------------------------------------------------------------------------

def get_current_prompt_adjustments() -> dict:
    """Retrieve any active prompt adjustments from previous feedback analysis."""
    dynamodb = _get_dynamodb_resource()
    table = dynamodb.Table(DYNAMODB_PROMPT_IMPROVEMENTS_TABLE)

    result = table.scan()
    items = result.get("Items", [])

    # Only return active, non-expired adjustments
    active = []
    now = time.time()
    for item in items:
        if item.get("active", True):
            expires = float(item.get("expires_at", now + 86400))
            if expires > now:
                active.append(item)

    return {
        "adjustments": active,
        "count": len(active),
    }


def save_prompt_adjustment(adjustment_type: str, description: str,
                           prompt_addition: str, triggered_by: str):
    """Store a prompt adjustment suggested by feedback analysis."""
    from datetime import datetime, timezone
    import hashlib

    dynamodb = _get_dynamodb_resource()
    table = dynamodb.Table(DYNAMODB_PROMPT_IMPROVEMENTS_TABLE)

    now = datetime.now(timezone.utc).isoformat()
    item_id = hashlib.sha256(f"{adjustment_type}:{description}:{now}".encode()).hexdigest()[:16]

    item = {
        "id": item_id,
        "type": adjustment_type,
        "description": description,
        "prompt_addition": prompt_addition,
        "triggered_by": triggered_by,
        "active": True,
        "created_at": now,
        "expires_at": str(time.time() + 604800),  # 7 days
    }
    table.put_item(Item=item)
    return item


def build_feedback_aware_triage_prompt(base_prompt: str) -> str:
    """Augment the base triage prompt with feedback-based improvements."""
    adjustments = get_current_prompt_adjustments()

    if not adjustments["adjustments"]:
        return base_prompt

    additions = []
    for adj in adjustments["adjustments"]:
        additions.append(adj.get("prompt_addition", ""))

    if additions:
        feedback_section = "\n\n".join(additions)
        return f"{base_prompt}\n\n## Quality Improvements Based on Feedback\n\n{feedback_section}"

    return base_prompt


def deactivate_adjustment(adjustment_id: str):
    """Deactivate a prompt adjustment."""
    dynamodb = _get_dynamodb_resource()
    table = dynamodb.Table(DYNAMODB_PROMPT_IMPROVEMENTS_TABLE)

    table.update_item(
        Key={"id": adjustment_id},
        UpdateExpression="SET active = :a",
        ExpressionAttributeValues={":a": False},
    )


def get_all_adjustments() -> list[dict]:
    """Get all prompt adjustments (active and inactive)."""
    dynamodb = _get_dynamodb_resource()
    table = dynamodb.Table(DYNAMODB_PROMPT_IMPROVEMENTS_TABLE)

    result = table.scan()
    return result.get("Items", [])
