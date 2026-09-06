import time
import psycopg2
from psycopg2.extras import RealDictCursor
from pgvector.psycopg2 import register_vector
from config import DATABASE_URL
from metrics import metrics


def get_db():
    conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
    register_vector(conn)
    return conn


def _timed(operation: str):
    """Decorator that times a DB function and records metrics."""
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


@_timed(operation="init_db")
def init_db():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS jira_tickets (
            id SERIAL PRIMARY KEY,
            ticket_id VARCHAR(50) UNIQUE NOT NULL,
            application VARCHAR(200),
            issue_type VARCHAR(50),
            priority VARCHAR(10),
            environment VARCHAR(100),
            component VARCHAR(200),
            summary TEXT,
            created_date TIMESTAMP,
            resolved_date TIMESTAMP,
            status VARCHAR(50),
            technology VARCHAR(200),
            version VARCHAR(50),
            region VARCHAR(100),
            issue_description TEXT,
            root_cause TEXT,
            solution TEXT,
            tags TEXT,
            service VARCHAR(200),
            dependency VARCHAR(200),
            estimated_resolution_time VARCHAR(50),
            business_impact TEXT,
            key_metric VARCHAR(200),
            root_cause_category VARCHAR(200),
            embedding vector(1024),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    cur.close()
    conn.close()


# ---------------------------------------------------------------------------
# Export DynamoDB functions for backward compatibility
# Chat sessions, messages, and triage tickets are now in DynamoDB
# ---------------------------------------------------------------------------

from dynamodb import (
    create_session, list_sessions, get_session, rename_session,
    delete_session, save_message, get_messages,
    create_triage_ticket, list_triage_tickets, get_triage_ticket,
    update_triage_ticket_status, save_triage_feedback, update_triage_ticket,
)
