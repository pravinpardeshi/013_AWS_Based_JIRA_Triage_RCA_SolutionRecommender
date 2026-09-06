import time
from database import get_db
from bedrock_client import get_embedding
from metrics import metrics


async def search_similar_tickets(query: str, top_k: int = 5) -> tuple[list[dict], dict]:
    emb_result = await get_embedding(query)
    embedding_str = "[" + ",".join(str(x) for x in emb_result.embedding) + "]"

    search_start = time.perf_counter()
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT ticket_id, summary, issue_description, issue_type, priority,
               component, technology, root_cause, solution, tags, service,
               dependency, root_cause_category, business_impact,
               1 - (embedding <=> %s::vector) AS similarity
        FROM jira_tickets
        ORDER BY embedding <=> %s::vector
        LIMIT %s
    """, (embedding_str, embedding_str, top_k))
    results = cur.fetchall()
    cur.close()
    conn.close()
    search_elapsed = time.perf_counter() - search_start

    metrics.vector_search_duration.observe(search_elapsed)

    result_metrics = {"embedding_ms": emb_result.duration_ms, "search_ms": round(search_elapsed * 1000, 1)}
    return results, result_metrics


def get_ticket_by_id(ticket_id: str) -> dict | None:
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT ticket_id, application, issue_type, priority, environment,
               component, summary, created_date, resolved_date, status,
               technology, version, region, issue_description, root_cause,
               solution, tags, service, dependency, estimated_resolution_time,
               business_impact, key_metric, root_cause_category
        FROM jira_tickets WHERE ticket_id = %s
    """, (ticket_id,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    return row


def format_tickets_context(tickets: list[dict]) -> str:
    if not tickets:
        return "No relevant historical tickets found."

    parts = []
    for t in tickets:
        sim = round(t.get("similarity", 0) * 100, 1)
        parts.append(
            f"--- Ticket: {t['ticket_id']} (Similarity: {sim}%) ---\n"
            f"Summary: {t['summary']}\n"
            f"Root Cause: {t['root_cause']}\n"
            f"Solution: {t['solution']}\n"
            f"Root Cause Category: {t['root_cause_category']}"
        )
    return "\n\n".join(parts)
