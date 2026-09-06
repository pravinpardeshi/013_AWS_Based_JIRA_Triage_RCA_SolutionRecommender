import time
import json
import boto3
from boto3.dynamodb.conditions import Key
from datetime import datetime, timezone
from config import (
    AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_DEFAULT_REGION,
    DYNAMODB_SESSIONS_TABLE, DYNAMODB_MESSAGES_TABLE, DYNAMODB_TRIAGE_TABLE,
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


def _timed(operation: str):
    def decorator(func):
        def wrapper(*args, **kwargs):
            start = time.perf_counter()
            status = "ok"
            try:
                result = func(*args, **kwargs)
                return result
            except Exception:
                status = "error"
                raise
            finally:
                elapsed = time.perf_counter() - start
                metrics.db_query_duration.observe(elapsed, operation=operation)
                metrics.db_query_total.inc(operation=operation, status=status)
        return wrapper
    return decorator


def init_dynamodb_tables():
    client = _get_dynamodb_client()
    existing = client.list_tables()["TableNames"]

    tables_to_create = [
        {
            "TableName": DYNAMODB_SESSIONS_TABLE,
            "KeySchema": [{"AttributeName": "id", "KeyType": "HASH"}],
            "AttributeDefinitions": [
                {"AttributeName": "id", "AttributeType": "N"},
            ],
            "BillingMode": "PAY_PER_REQUEST",
        },
        {
            "TableName": DYNAMODB_MESSAGES_TABLE,
            "KeySchema": [
                {"AttributeName": "session_id", "KeyType": "HASH"},
                {"AttributeName": "id", "KeyType": "RANGE"},
            ],
            "AttributeDefinitions": [
                {"AttributeName": "session_id", "AttributeType": "N"},
                {"AttributeName": "id", "AttributeType": "N"},
            ],
            "BillingMode": "PAY_PER_REQUEST",
        },
        {
            "TableName": DYNAMODB_TRIAGE_TABLE,
            "KeySchema": [{"AttributeName": "id", "KeyType": "HASH"}],
            "AttributeDefinitions": [
                {"AttributeName": "id", "AttributeType": "N"},
            ],
            "BillingMode": "PAY_PER_REQUEST",
        },
    ]

    for table_def in tables_to_create:
        if table_def["TableName"] not in existing:
            client.create_table(**table_def)
            waiter = client.get_waiter("table_exists")
            waiter.wait(TableName=table_def["TableName"])


# ---------------------------------------------------------------------------
# Chat session persistence
# ---------------------------------------------------------------------------

@_timed(operation="create_session")
def create_session(title: str = "Untitled Chat") -> dict:
    dynamodb = _get_dynamodb_resource()
    table = dynamodb.Table(DYNAMODB_SESSIONS_TABLE)

    now = datetime.now(timezone.utc).isoformat()
    result = table.scan(Select="COUNT")
    count = result["Count"]

    item = {
        "id": count + 1,
        "title": title,
        "created_at": now,
        "updated_at": now,
    }
    table.put_item(Item=item)
    return item


@_timed(operation="list_sessions")
def list_sessions() -> list[dict]:
    dynamodb = _get_dynamodb_resource()
    sessions_table = dynamodb.Table(DYNAMODB_SESSIONS_TABLE)
    messages_table = dynamodb.Table(DYNAMODB_MESSAGES_TABLE)

    result = sessions_table.scan()
    sessions = result.get("Items", [])

    for session in sessions:
        msg_result = messages_table.query(
            KeyConditionExpression=Key("session_id").eq(int(session["id"]))
        )
        session["message_count"] = msg_result["Count"]

    sessions.sort(key=lambda x: x.get("updated_at", ""), reverse=True)
    return sessions


@_timed(operation="get_session")
def get_session(session_id: int) -> dict | None:
    dynamodb = _get_dynamodb_resource()
    table = dynamodb.Table(DYNAMODB_SESSIONS_TABLE)

    response = table.get_item(Key={"id": session_id})
    return response.get("Item")


@_timed(operation="rename_session")
def rename_session(session_id: int, title: str) -> dict | None:
    dynamodb = _get_dynamodb_resource()
    table = dynamodb.Table(DYNAMODB_SESSIONS_TABLE)

    now = datetime.now(timezone.utc).isoformat()
    response = table.update_item(
        Key={"id": session_id},
        UpdateExpression="SET title = :t, updated_at = :u",
        ExpressionAttributeValues={":t": title, ":u": now},
        ReturnValues="ALL_NEW",
    )
    return response.get("Attributes")


@_timed(operation="delete_session")
def delete_session(session_id: int) -> bool:
    dynamodb = _get_dynamodb_resource()
    sessions_table = dynamodb.Table(DYNAMODB_SESSIONS_TABLE)
    messages_table = dynamodb.Table(DYNAMODB_MESSAGES_TABLE)

    response = sessions_table.delete_item(Key={"id": session_id})
    deleted = response.get("ResponseMetadata", {}).get("HTTPStatusCode") == 200

    msg_result = messages_table.query(
        KeyConditionExpression=Key("session_id").eq(session_id)
    )
    with messages_table.batch_writer() as batch:
        for item in msg_result.get("Items", []):
            batch.delete_item(Key={"session_id": item["session_id"], "id": item["id"]})

    return deleted


@_timed(operation="save_message")
def save_message(session_id: int, role: str, content: str, sources: list | None = None) -> dict:
    dynamodb = _get_dynamodb_resource()
    sessions_table = dynamodb.Table(DYNAMODB_SESSIONS_TABLE)
    messages_table = dynamodb.Table(DYNAMODB_MESSAGES_TABLE)

    now = datetime.now(timezone.utc).isoformat()

    msg_result = messages_table.query(
        KeyConditionExpression=Key("session_id").eq(session_id),
        Select="COUNT",
    )
    msg_count = msg_result["Count"]

    item = {
        "session_id": session_id,
        "id": msg_count + 1,
        "role": role,
        "content": content,
        "sources": sources or [],
        "created_at": now,
    }
    messages_table.put_item(Item=item)

    sessions_table.update_item(
        Key={"id": session_id},
        UpdateExpression="SET updated_at = :u",
        ExpressionAttributeValues={":u": now},
    )

    return item


@_timed(operation="get_messages")
def get_messages(session_id: int) -> list[dict]:
    dynamodb = _get_dynamodb_resource()
    table = dynamodb.Table(DYNAMODB_MESSAGES_TABLE)

    result = table.query(
        KeyConditionExpression=Key("session_id").eq(session_id),
        ScanIndexForward=True,
    )
    return result.get("Items", [])


# ---------------------------------------------------------------------------
# Current triage tickets (tracked issues from Triage & RCA)
# ---------------------------------------------------------------------------

@_timed(operation="create_triage_ticket")
def create_triage_ticket(description: str, component: str, technology: str,
                         environment: str, analysis: str, similar_tickets: list,
                         metrics_data: dict) -> dict:
    dynamodb = _get_dynamodb_resource()
    table = dynamodb.Table(DYNAMODB_TRIAGE_TABLE)

    now = datetime.now(timezone.utc).isoformat()

    scan_result = table.scan(Select="COUNT")
    count = scan_result["Count"]
    ticket_ref = f"TRG-{count + 1:04d}"

    item = {
        "id": count + 1,
        "ticket_ref": ticket_ref,
        "description": description,
        "component": component or "",
        "technology": technology or "",
        "environment": environment or "",
        "analysis": analysis,
        "similar_tickets": similar_tickets or [],
        "metrics": metrics_data or {},
        "status": "open",
        "created_at": now,
        "updated_at": now,
    }
    table.put_item(Item=item)
    return item


@_timed(operation="list_triage_tickets")
def list_triage_tickets(status_filter: str = None, search: str = None) -> list[dict]:
    dynamodb = _get_dynamodb_resource()
    table = dynamodb.Table(DYNAMODB_TRIAGE_TABLE)

    result = table.scan()
    items = result.get("Items", [])

    if status_filter and status_filter != "all":
        items = [i for i in items if i.get("status") == status_filter]

    if search:
        search_lower = search.lower()
        items = [
            i for i in items
            if search_lower in (i.get("description", "")).lower()
            or search_lower in (i.get("component", "")).lower()
            or search_lower in (i.get("technology", "")).lower()
        ]

    items.sort(key=lambda x: x.get("created_at", ""), reverse=True)

    summary_keys = ["id", "ticket_ref", "description", "component", "technology",
                    "environment", "status", "feedback_rating", "created_at",
                    "updated_at", "closed_at"]
    return [{k: i.get(k) for k in summary_keys} for i in items]


@_timed(operation="get_triage_ticket")
def get_triage_ticket(ticket_id: int) -> dict | None:
    dynamodb = _get_dynamodb_resource()
    table = dynamodb.Table(DYNAMODB_TRIAGE_TABLE)

    response = table.get_item(Key={"id": ticket_id})
    return response.get("Item")


@_timed(operation="update_triage_ticket_status")
def update_triage_ticket_status(ticket_id: int, status: str, resolution_notes: str = None) -> dict | None:
    dynamodb = _get_dynamodb_resource()
    table = dynamodb.Table(DYNAMODB_TRIAGE_TABLE)

    now = datetime.now(timezone.utc).isoformat()
    update_expr = "SET #s = :s, updated_at = :u"
    expr_names = {"#s": "status"}
    expr_vals = {":s": status, ":u": now}

    if resolution_notes:
        update_expr += ", resolution_notes = :rn"
        expr_vals[":rn"] = resolution_notes

    if status in ("resolved", "closed"):
        update_expr += ", closed_at = :c"
        expr_vals[":c"] = now

    response = table.update_item(
        Key={"id": ticket_id},
        UpdateExpression=update_expr,
        ExpressionAttributeNames=expr_names,
        ExpressionAttributeValues=expr_vals,
        ReturnValues="ALL_NEW",
    )
    return response.get("Attributes")


@_timed(operation="save_triage_feedback")
def save_triage_feedback(ticket_id: int, rating: int, comment: str = None) -> dict | None:
    dynamodb = _get_dynamodb_resource()
    table = dynamodb.Table(DYNAMODB_TRIAGE_TABLE)

    now = datetime.now(timezone.utc).isoformat()
    update_expr = "SET feedback_rating = :r, updated_at = :u"
    expr_vals = {":r": rating, ":u": now}

    if comment:
        update_expr += ", feedback_comment = :c"
        expr_vals[":c"] = comment

    response = table.update_item(
        Key={"id": ticket_id},
        UpdateExpression=update_expr,
        ExpressionAttributeValues=expr_vals,
        ReturnValues="ALL_NEW",
    )
    return response.get("Attributes")


@_timed(operation="update_triage_ticket")
def update_triage_ticket(ticket_id: int, **fields) -> dict | None:
    dynamodb = _get_dynamodb_resource()
    table = dynamodb.Table(DYNAMODB_TRIAGE_TABLE)

    allowed = {"description", "component", "technology", "environment"}
    updates = {k: v for k, v in fields.items() if k in allowed and v is not None}
    if not updates:
        return None

    now = datetime.now(timezone.utc).isoformat()
    updates["updated_at"] = now

    update_parts = []
    expr_vals = {}
    for k, v in updates.items():
        update_parts.append(f"{k} = :{k}")
        expr_vals[f":{k}"] = v

    response = table.update_item(
        Key={"id": ticket_id},
        UpdateExpression="SET " + ", ".join(update_parts),
        ExpressionAttributeValues=expr_vals,
        ReturnValues="ALL_NEW",
    )
    return response.get("Attributes")
