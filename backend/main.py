from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import StreamingResponse, Response
from pydantic import BaseModel
from typing import Optional
import os
import time
import json

from database import init_db
from database import (
    create_session, list_sessions, get_session, rename_session,
    delete_session, save_message, get_messages,
    create_triage_ticket, list_triage_tickets, get_triage_ticket,
    update_triage_ticket_status, save_triage_feedback, update_triage_ticket,
)
from retriever import search_similar_tickets, format_tickets_context, get_ticket_by_id
from bedrock_client import (
    chat_completion, cache_stats, chat_completion_stream_sse,
    cache_chat_result, get_cached_chat, _hash_key,
    BedrockTimeoutError, BedrockUnavailableError, BedrockError,
)
from guardrails import (
    validate_chat_request, validate_sources, check_hallucination,
    build_refusal_response, truncate_response, rate_limiter, circuit_breaker,
    MIN_SIMILARITY_THRESHOLD,
)
from metrics import metrics

app = FastAPI(title="JIRA Triage & RCA Agent", version="2.0.0-aws")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Observability middleware — request counting, timing, error tracking
# ---------------------------------------------------------------------------

@app.middleware("http")
async def metrics_middleware(request: Request, call_next):
    if request.url.path == "/metrics":
        return await call_next(request)

    start = time.perf_counter()
    status_code = 500
    try:
        response = await call_next(request)
        status_code = response.status_code
        return response
    except Exception:
        status_code = 500
        raise
    finally:
        elapsed = time.perf_counter() - start
        endpoint = _normalize_endpoint(request.url.path)
        method = request.method
        status_label = "ok" if status_code < 400 else f"{status_code}"

        metrics.http_requests.inc(method=method, endpoint=endpoint, status=str(status_code))
        metrics.http_request_duration.observe(elapsed, method=method, endpoint=endpoint)

        if status_code >= 400:
            error_type = "4xx" if status_code < 500 else "5xx"
            metrics.http_request_errors.inc(endpoint=endpoint, error_type=error_type)


def _normalize_endpoint(path: str) -> str:
    parts = path.strip("/").split("/")
    if len(parts) >= 3 and parts[0] == "api":
        resource = parts[1]
        if resource in ("sessions", "ticket") and len(parts) >= 3:
            return f"/api/{resource}/:id"
        if resource == "sessions" and len(parts) >= 4 and parts[3] == "messages":
            return f"/api/sessions/:id/messages"
    return path


class ChatRequest(BaseModel):
    message: str
    top_k: Optional[int] = 5
    session_id: Optional[int] = None


class TriageRequest(BaseModel):
    description: str
    component: Optional[str] = ""
    technology: Optional[str] = ""
    environment: Optional[str] = ""


class SessionCreate(BaseModel):
    title: Optional[str] = "Untitled Chat"


class SessionRename(BaseModel):
    title: str


class TriageTicketCreate(BaseModel):
    description: str
    component: Optional[str] = ""
    technology: Optional[str] = ""
    environment: Optional[str] = ""
    analysis: str
    similar_tickets: Optional[list] = []
    metrics: Optional[dict] = {}


class TriageTicketStatusUpdate(BaseModel):
    status: str
    resolution_notes: Optional[str] = None


class TriageTicketFeedback(BaseModel):
    rating: int
    comment: Optional[str] = None


class TriageTicketUpdate(BaseModel):
    description: Optional[str] = None
    component: Optional[str] = None
    technology: Optional[str] = None
    environment: Optional[str] = None


# ---------------------------------------------------------------------------
# Chat session persistence
# ---------------------------------------------------------------------------

@app.get("/api/sessions")
def api_list_sessions():
    return list_sessions()


@app.post("/api/sessions")
def api_create_session(body: SessionCreate):
    return create_session(body.title)


@app.get("/api/sessions/{session_id}")
def api_get_session(session_id: int):
    session = get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    messages = get_messages(session_id)
    return {"session": session, "messages": messages}


@app.put("/api/sessions/{session_id}")
def api_rename_session(session_id: int, body: SessionRename):
    result = rename_session(session_id, body.title)
    if not result:
        raise HTTPException(status_code=404, detail="Session not found")
    return result


@app.delete("/api/sessions/{session_id}")
def api_delete_session(session_id: int):
    if not delete_session(session_id):
        raise HTTPException(status_code=404, detail="Session not found")
    return {"ok": True}


@app.post("/api/sessions/{session_id}/messages")
def api_save_message(session_id: int, role: str, content: str, sources: Optional[str] = "[]"):
    return save_message(session_id, role, content, json.loads(sources))


@app.on_event("startup")
def startup():
    init_db()
    from dynamodb import init_dynamodb_tables
    from dynamodb_cache import init_cache_table
    from dynamodb_guardrails import init_guardrails_tables
    from feedback_analytics import init_feedback_tables
    init_dynamodb_tables()
    init_cache_table()
    init_guardrails_tables()
    init_feedback_tables()


@app.get("/api/health")
def health_check():
    import psycopg2
    from config import DATABASE_URL

    status = {"status": "ok", "db": "ok", "bedrock": "ok", "tickets": 0, "sessions": 0}
    errors = []

    # Check PostgreSQL
    try:
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM jira_tickets")
        status["tickets"] = cur.fetchone()[0]
        cur.close()
        conn.close()
    except Exception as e:
        status["db"] = f"error: {str(e)[:100]}"
        errors.append("db")

    # Check DynamoDB sessions
    try:
        from dynamodb import list_sessions
        sessions = list_sessions()
        status["sessions"] = len(sessions)
    except Exception as e:
        status["dynamodb"] = f"error: {str(e)[:100]}"
        errors.append("dynamodb")

    # Check Bedrock
    try:
        import boto3
        from config import BEDROCK_REGION, AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY
        session = boto3.Session(
            aws_access_key_id=AWS_ACCESS_KEY_ID or None,
            aws_secret_access_key=AWS_SECRET_ACCESS_KEY or None,
            region_name=BEDROCK_REGION,
        )
        bedrock = session.client("bedrock", region_name=BEDROCK_REGION)
        bedrock.list.FoundationModels()
        status["bedrock_models"] = "accessible"
    except Exception as e:
        status["bedrock"] = f"error: {str(e)[:100]}"
        errors.append("bedrock")

    # Check circuit breaker
    from guardrails import circuit_breaker
    status["circuit_breaker"] = circuit_breaker.state
    status["rate_limit_remaining"] = "check per-IP"

    state_map = {"closed": 0, "open": 1, "half_open": 2}
    metrics.circuit_breaker_state.set(state_map.get(circuit_breaker.state, 0))

    if errors:
        status["status"] = "degraded"

    metrics.total_tickets.set(status["tickets"])
    metrics.active_sessions.set(status["sessions"])

    return status


@app.get("/metrics")
def metrics_endpoint():
    content = metrics.render_prometheus()
    return Response(content=content, media_type="text/plain; version=0.0.4; charset=utf-8")


@app.get("/api/metrics/summary")
def metrics_summary():
    from metrics import metrics as m

    def _hist_summary(histogram):
        result = {}
        for key, values in histogram._data.items():
            if not values:
                continue
            label = dict(key) if key else {}
            vals = sorted(values)
            count = len(vals)
            total = sum(vals)
            result[label.get("operation", label.get("endpoint", label.get("model", "_default")))] = {
                "count": count,
                "sum": round(total, 4),
                "avg": round(total / count, 4) if count else 0,
                "p50": round(vals[count // 2], 4) if count else 0,
                "p95": round(vals[int(count * 0.95)] if count > 1 else vals[-1], 4),
                "p99": round(vals[int(count * 0.99)] if count > 1 else vals[-1], 4),
                "min": round(vals[0], 4),
                "max": round(vals[-1], 4),
            }
        return result

    def _counter_values(counter):
        result = {}
        for key, value in counter._values.items():
            label = dict(key) if key else {}
            flat_key = "_".join(f"{v}" for v in label.values()) if label else "total"
            result[flat_key] = value
        return result

    return {
        "http": {
            "requests": _counter_values(m.http_requests),
            "errors": _counter_values(m.http_request_errors),
            "duration": _hist_summary(m.http_request_duration),
        },
        "llm": {
            "requests": _counter_values(m.llm_requests),
            "tokens": _counter_values(m.llm_tokens),
            "duration": _hist_summary(m.llm_duration),
        },
        "embedding": {
            "requests": _counter_values(m.embedding_requests),
            "duration": _hist_summary(m.embedding_duration),
        },
        "database": {
            "queries": _counter_values(m.db_query_total),
            "duration": _hist_summary(m.db_query_duration),
        },
        "vector_search": {
            "duration": _hist_summary(m.vector_search_duration),
        },
        "cache": {
            "operations": _counter_values(m.cache_operations),
            "hit_rate": m.cache_hit_rate.get(),
            "sizes": {
                "embedding": m.cache_size.get(cache="embedding"),
                "chat": m.cache_size.get(cache="chat"),
            },
        },
        "circuit_breaker": {
            "state": m.circuit_breaker_state.get(),
            "transitions": _counter_values(m.circuit_breaker_events),
        },
        "rate_limiter": {
            "allowed": m.rate_limit_allowed.get(),
            "rejected": m.rate_limit_rejected.get(),
        },
        "guardrails": {
            "blocked": _counter_values(m.guardrail_blocked),
            "duration": _hist_summary(m.guardrail_duration),
        },
        "gauges": {
            "active_sessions": m.active_sessions.get(),
            "total_tickets": m.total_tickets.get(),
            "uptime_seconds": round(m.uptime_seconds.get(), 1),
        },
    }


@app.get("/api/ticket/{ticket_id}")
def get_ticket(ticket_id: str):
    row = get_ticket_by_id(ticket_id)
    if not row:
        raise HTTPException(status_code=404, detail=f"Ticket {ticket_id} not found")
    return dict(row)


# ---------------------------------------------------------------------------
# Non-streaming endpoints (kept as fallbacks)
# ---------------------------------------------------------------------------

@app.post("/api/chat")
async def chat(req: ChatRequest):
    t_total = time.perf_counter()
    try:
        tickets, emb_metrics = await search_similar_tickets(req.message, top_k=req.top_k)
        context = format_tickets_context(tickets)

        messages = [
            {
                "role": "system",
                "content": (
                    "You are a JIRA Knowledge Base Assistant. You help engineers understand "
                    "past incidents, bugs, and their resolutions. Answer based on the historical "
                    "ticket data provided below. Be specific, reference ticket IDs, and provide "
                    "actionable insights. If no relevant data is found, say so honestly."
                )
            },
            {
                "role": "user",
                "content": f"Historical tickets for reference:\n\n{context}\n\n---\n\nUser question: {req.message}"
            }
        ]

        chat_result = await chat_completion(messages)
        total_ms = round((time.perf_counter() - t_total) * 1000, 1)

        return {
            "answer": chat_result.content,
            "sources": [
                {"ticket_id": t["ticket_id"], "summary": t["summary"],
                 "similarity": round(t.get("similarity", 0) * 100, 1)}
                for t in tickets
            ],
            "metrics": {
                "total_ms": total_ms,
                "embedding_ms": emb_metrics.get("embedding_ms", 0),
                "llm_ms": chat_result.duration_ms,
                "prompt_tokens": chat_result.prompt_tokens,
                "completion_tokens": chat_result.completion_tokens,
                "total_tokens": chat_result.total_tokens,
                "cached": chat_result.cached,
            }
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/triage")
async def triage(req: TriageRequest):
    t_total = time.perf_counter()
    try:
        search_query = f"{req.description} {req.component} {req.technology} {req.environment}"
        tickets, emb_metrics = await search_similar_tickets(search_query, top_k=8)
        context = format_tickets_context(tickets)

        messages = [
            {
                "role": "system",
                "content": (
                    "You are an expert JIRA ticket triage and Root Cause Analysis (RCA) system. "
                    "Given a problem description and historical ticket data, perform the following:\n\n"
                    "1. **TRIAGE**: Propose priority (P1-P4), issue type, component, and assign to the "
                    "most appropriate team based on historical patterns.\n"
                    "2. **ROOT CAUSE ANALYSIS**: Based on similar historical tickets, identify the most "
                    "likely root cause category and explain the probable cause.\n"
                    "3. **SOLUTION**: Recommend a concrete solution based on what worked for similar past tickets.\n"
                    "4. **IMPACT ASSESSMENT**: Estimate business impact and resolution time.\n\n"
                    "Format your response as a structured analysis with clear sections."
                )
            },
            {
                "role": "user",
                "content": (
                    f"Historical tickets for reference:\n\n{context}\n\n---\n\n"
                    f"PROBLEM DESCRIPTION: {req.description}\n"
                    f"Component: {req.component or 'Not specified'}\n"
                    f"Technology: {req.technology or 'Not specified'}\n"
                    f"Environment: {req.environment or 'Not specified'}\n\n"
                    "Please perform triage, RCA, and propose a solution."
                )
            }
        ]

        chat_result = await chat_completion(messages, temperature=0.2)
        total_ms = round((time.perf_counter() - t_total) * 1000, 1)

        return {
            "analysis": chat_result.content,
            "similar_tickets": [
                {"ticket_id": t["ticket_id"], "summary": t["summary"],
                 "similarity": round(t.get("similarity", 0) * 100, 1)}
                for t in tickets
            ],
            "metrics": {
                "total_ms": total_ms,
                "embedding_ms": emb_metrics.get("embedding_ms", 0),
                "llm_ms": chat_result.duration_ms,
                "prompt_tokens": chat_result.prompt_tokens,
                "completion_tokens": chat_result.completion_tokens,
                "total_tokens": chat_result.total_tokens,
                "cached": chat_result.cached,
            }
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# Streaming endpoints (SSE) — using LangGraph agent workflows
# ---------------------------------------------------------------------------

def _format_sources(tickets: list[dict]) -> list[dict]:
    return [
        {"ticket_id": t["ticket_id"], "summary": t["summary"],
         "similarity": round(t.get("similarity", 0) * 100, 1)}
        for t in tickets
    ]


@app.post("/api/chat/stream")
async def chat_stream(req: ChatRequest, request: Request):
    t_total = time.perf_counter()
    client_ip = request.client.host if request.client else "unknown"
    try:
        req.message = validate_chat_request(req.message, client_ip)

        # Save user message immediately
        if req.session_id:
            try:
                save_message(req.session_id, "user", req.message, [])
            except Exception:
                pass

        from langgraph_agent import run_chat_agent_stream

        full_content = ""

        async def stream_gen():
            nonlocal full_content
            async for sse_event in run_chat_agent_stream(req.message, top_k=req.top_k):
                if sse_event.startswith("event: done"):
                    data_line = sse_event.split("data: ", 1)[1].strip()
                    done_data = json.loads(data_line)
                    # Save assistant message
                    if req.session_id:
                        try:
                            sources = json.loads(
                                sse_event.split("event: init\ndata: ", 1)[-1].split("\n\n")[0]
                            ).get("sources", []) if "event: init" in full_content else []
                        except Exception:
                            sources = []
                        try:
                            save_message(req.session_id, "assistant", full_content, sources)
                        except Exception:
                            pass
                    yield sse_event
                elif sse_event.startswith("event: init"):
                    yield sse_event
                elif sse_event.startswith("event: error"):
                    yield sse_event
                else:
                    token_line = sse_event.split("data: ", 1)[1].strip()
                    token_data = json.loads(token_line)
                    full_content += token_data.get("content", "")
                    yield sse_event

        return StreamingResponse(stream_gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/triage/stream")
async def triage_stream(req: TriageRequest, request: Request):
    t_total = time.perf_counter()
    client_ip = request.client.host if request.client else "unknown"
    try:
        req.description = validate_chat_request(req.description, client_ip)

        from langgraph_agent import run_triage_agent_stream

        return StreamingResponse(
            run_triage_agent_stream(req.description, req.component,
                                    req.technology, req.environment),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# RCA (Root Cause Analysis) endpoints
# ---------------------------------------------------------------------------

@app.post("/api/rca")
async def rca(req: TriageRequest):
    t_total = time.perf_counter()
    try:
        search_query = f"{req.description} {req.component} {req.technology} {req.environment}"
        tickets, emb_metrics = await search_similar_tickets(search_query, top_k=5)
        context = format_tickets_context(tickets)

        rca_prompt = (
            "You are an expert Root Cause Analysis (RCA) specialist for JIRA incidents. "
            "Given a problem description and historical ticket data, perform a deep root cause analysis:\n\n"
            "1. **ROOT CAUSE CATEGORY**: Identify the most likely category "
            "(e.g., Configuration, Code Defect, Infrastructure, Dependency, Resource, Security, Data).\n"
            "2. **PROBABLE CAUSE**: Explain the most likely specific root cause based on historical patterns.\n"
            "3. **CONTRIBUTING FACTORS**: List any secondary factors that may have contributed.\n"
            "4. **EVIDENCE**: Reference specific similar tickets that support your analysis.\n"
            "5. **VERIFICATION STEPS**: Provide concrete steps to confirm the root cause.\n\n"
            "Be specific and evidence-based. Reference ticket IDs from the historical data."
        )

        messages = [
            {"role": "system", "content": rca_prompt},
            {
                "role": "user",
                "content": (
                    f"Historical tickets for reference:\n\n{context}\n\n---\n\n"
                    f"PROBLEM DESCRIPTION: {req.description}\n"
                    f"Component: {req.component or 'Not specified'}\n"
                    f"Technology: {req.technology or 'Not specified'}\n"
                    f"Environment: {req.environment or 'Not specified'}\n\n"
                    "Perform root cause analysis."
                )
            }
        ]

        chat_result = await chat_completion(messages, temperature=0.2)
        total_ms = round((time.perf_counter() - t_total) * 1000, 1)

        return {
            "analysis": chat_result.content,
            "similar_tickets": _format_sources(tickets),
            "metrics": {
                "total_ms": total_ms,
                "embedding_ms": emb_metrics.get("embedding_ms", 0),
                "llm_ms": chat_result.duration_ms,
                "prompt_tokens": chat_result.prompt_tokens,
                "completion_tokens": chat_result.completion_tokens,
                "total_tokens": chat_result.total_tokens,
                "cached": chat_result.cached,
            }
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/rca/stream")
async def rca_stream(req: TriageRequest, request: Request):
    t_total = time.perf_counter()
    client_ip = request.client.host if request.client else "unknown"
    try:
        req.description = validate_chat_request(req.description, client_ip)

        from langgraph_agent import run_rca_agent_stream

        return StreamingResponse(
            run_rca_agent_stream(req.description, req.component,
                                 req.technology, req.environment),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# Solution Recommendation endpoints
# ---------------------------------------------------------------------------

class SolutionRequest(BaseModel):
    description: str
    component: str = ""
    technology: str = ""
    environment: str = ""
    root_cause: str = ""


@app.post("/api/solution")
async def solution(req: SolutionRequest):
    t_total = time.perf_counter()
    try:
        search_query = f"{req.description} {req.component} {req.technology} {req.environment}"
        tickets, emb_metrics = await search_similar_tickets(search_query, top_k=5)
        context = format_tickets_context(tickets)

        solution_prompt = (
            "You are an expert Solution Architect for JIRA incidents. "
            "Given a problem description, optional root cause analysis, and historical ticket data, "
            "recommend concrete solutions:\n\n"
            "1. **IMMEDIATE MITIGATION**: Quick steps to reduce impact right now.\n"
            "2. **PERMANENT FIX**: The recommended long-term solution based on what worked for similar past tickets.\n"
            "3. **IMPLEMENTATION STEPS**: Step-by-step instructions for the fix.\n"
            "4. **VERIFICATION**: How to verify the fix works.\n"
            "5. **PREVENTION**: How to prevent this issue from recurring.\n"
            "6. **RELATED TICKETS**: Reference specific past tickets where similar solutions were applied.\n\n"
            "Be specific, actionable, and reference ticket IDs from the historical data."
        )

        user_content = (
            f"Historical tickets for reference:\n\n{context}\n\n---\n\n"
            f"PROBLEM DESCRIPTION: {req.description}\n"
            f"Component: {req.component or 'Not specified'}\n"
            f"Technology: {req.technology or 'Not specified'}\n"
            f"Environment: {req.environment or 'Not specified'}\n"
        )
        if req.root_cause:
            user_content += f"\nROOT CAUSE ANALYSIS:\n{req.root_cause}\n"
        user_content += "\nRecommend solutions."

        messages = [
            {"role": "system", "content": solution_prompt},
            {"role": "user", "content": user_content}
        ]

        chat_result = await chat_completion(messages, temperature=0.2)
        total_ms = round((time.perf_counter() - t_total) * 1000, 1)

        return {
            "analysis": chat_result.content,
            "similar_tickets": _format_sources(tickets),
            "metrics": {
                "total_ms": total_ms,
                "embedding_ms": emb_metrics.get("embedding_ms", 0),
                "llm_ms": chat_result.duration_ms,
                "prompt_tokens": chat_result.prompt_tokens,
                "completion_tokens": chat_result.completion_tokens,
                "total_tokens": chat_result.total_tokens,
                "cached": chat_result.cached,
            }
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/solution/stream")
async def solution_stream(req: SolutionRequest, request: Request):
    t_total = time.perf_counter()
    client_ip = request.client.host if request.client else "unknown"
    try:
        req.description = validate_chat_request(req.description, client_ip)

        from langgraph_agent import run_solution_agent_stream

        return StreamingResponse(
            run_solution_agent_stream(req.description, req.component,
                                      req.technology, req.environment,
                                      req.root_cause),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/stats")
async def stats():
    from database import get_db
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) as total FROM jira_tickets")
    total = cur.fetchone()["total"]
    cur.execute("SELECT root_cause_category, COUNT(*) as cnt FROM jira_tickets GROUP BY root_cause_category ORDER BY cnt DESC")
    categories = cur.fetchall()
    cur.execute("SELECT priority, COUNT(*) as cnt FROM jira_tickets GROUP BY priority ORDER BY cnt DESC")
    priorities = cur.fetchall()
    cur.close()
    conn.close()
    return {
        "total_tickets": total,
        "root_cause_categories": categories,
        "priority_distribution": priorities,
        "cache": cache_stats(),
    }


# ---------------------------------------------------------------------------
# Current triage tickets (tracked issues)
# ---------------------------------------------------------------------------

@app.post("/api/triage/tickets")
def api_create_triage_ticket(body: TriageTicketCreate):
    try:
        ticket = create_triage_ticket(
            description=body.description,
            component=body.component,
            technology=body.technology,
            environment=body.environment,
            analysis=body.analysis,
            similar_tickets=body.similar_tickets,
            metrics_data=body.metrics,
        )
        metrics.triage_tickets_created.inc()
        return ticket
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/triage/tickets")
def api_list_triage_tickets(status: Optional[str] = None, search: Optional[str] = None):
    return list_triage_tickets(status_filter=status, search=search)


@app.get("/api/triage/tickets/{ticket_id}")
def api_get_triage_ticket(ticket_id: int):
    ticket = get_triage_ticket(ticket_id)
    if not ticket:
        raise HTTPException(status_code=404, detail=f"Ticket {ticket_id} not found")
    return ticket


@app.patch("/api/triage/tickets/{ticket_id}")
def api_update_triage_ticket(ticket_id: int, body: TriageTicketUpdate):
    result = update_triage_ticket(
        ticket_id,
        description=body.description,
        component=body.component,
        technology=body.technology,
        environment=body.environment,
    )
    if not result:
        raise HTTPException(status_code=404, detail=f"Ticket {ticket_id} not found")
    return result


@app.patch("/api/triage/tickets/{ticket_id}/status")
def api_update_triage_ticket_status(ticket_id: int, body: TriageTicketStatusUpdate):
    if body.status not in ('open', 'in_progress', 'resolved', 'closed'):
        raise HTTPException(status_code=400, detail="Invalid status. Must be: open, in_progress, resolved, closed")
    result = update_triage_ticket_status(ticket_id, body.status, body.resolution_notes)
    if not result:
        raise HTTPException(status_code=404, detail=f"Ticket {ticket_id} not found")
    if body.status in ('resolved', 'closed'):
        metrics.triage_tickets_closed.inc()
    return result


@app.post("/api/triage/tickets/{ticket_id}/feedback")
def api_save_triage_feedback(ticket_id: int, body: TriageTicketFeedback):
    if body.rating < 1 or body.rating > 5:
        raise HTTPException(status_code=400, detail="Rating must be between 1 and 5")
    result = save_triage_feedback(ticket_id, body.rating, body.comment)
    if not result:
        raise HTTPException(status_code=404, detail=f"Ticket {ticket_id} not found")
    metrics.triage_feedback_submitted.inc()
    metrics.triage_feedback_rating.observe(body.rating)

    # Record feedback for analytics loop
    try:
        from feedback_analytics import record_feedback
        ticket = get_triage_ticket(ticket_id)
        if ticket:
            record_feedback(
                ticket_ref=result.get("ticket_ref", ""),
                rating=body.rating,
                comment=body.comment,
                component=ticket.get("component", ""),
                technology=ticket.get("technology", ""),
                description=ticket.get("description", ""),
                analysis=ticket.get("analysis", ""),
            )
    except Exception:
        pass  # Analytics recording is non-critical

    return result


# ---------------------------------------------------------------------------
# Feedback Analytics & Prompt Improvement
# ---------------------------------------------------------------------------

@app.get("/api/feedback/stats")
def api_feedback_stats():
    from feedback_analytics import get_feedback_stats
    return get_feedback_stats()


@app.get("/api/feedback/low-rated")
def api_feedback_low_rated():
    from feedback_analytics import get_low_rated_tickets
    return get_low_rated_tickets()


@app.get("/api/feedback/adjustments")
def api_feedback_adjustments():
    from feedback_analytics import get_current_prompt_adjustments, get_all_adjustments
    return {
        "active": get_current_prompt_adjustments(),
        "all": get_all_adjustments(),
    }


@app.post("/api/feedback/analyze")
async def api_feedback_analyze():
    from feedback_analytics import (
        get_feedback_stats, get_low_rated_tickets,
        save_prompt_adjustment,
    )
    from bedrock_client import chat_completion

    stats = get_feedback_stats()
    low_rated = get_low_rated_tickets()

    if stats["total_feedback"] < 3:
        return {
            "status": "insufficient_data",
            "message": "Need at least 3 feedback entries to generate improvement suggestions.",
            "current_stats": stats,
        }

    # Build analysis prompt for Bedrock
    feedback_summary = json.dumps({
        "total_feedback": stats["total_feedback"],
        "average_rating": stats["average_rating"],
        "rating_distribution": stats["rating_distribution"],
        "improvement_needed": stats["improvement_needed"],
        "low_rated_examples": [
            {
                "ticket_ref": t["ticket_ref"],
                "rating": t["rating"],
                "comment": t["comment"],
                "description": t["description"][:200],
                "analysis_snippet": t["analysis_snippet"][:200],
            }
            for t in low_rated[:5]
        ],
    }, indent=2)

    messages = [
        {
            "role": "system",
            "content": (
                "You are an expert at improving AI triage system prompts based on user feedback.\n\n"
                "Analyze the feedback data below and generate up to 3 specific, actionable prompt "
                "improvements. For each improvement, provide:\n"
                "1. A short description of the issue\n"
                "2. The exact text to add to the triage system prompt\n"
                "3. The type: 'priority_clarification', 'root_cause_focus', 'solution_quality', or 'context_handling'\n\n"
                "Focus on:\n"
                "- Low-rated tickets (1-2 stars) — what went wrong in the analysis\n"
                "- Common patterns in negative feedback comments\n"
                "- Components or technologies with consistently low ratings\n\n"
                "Return your response as a JSON array with objects containing: "
                "'description', 'prompt_addition', 'type'."
            )
        },
        {
            "role": "user",
            "content": f"Feedback data:\n\n{feedback_summary}"
        }
    ]

    try:
        result = await chat_completion(messages, temperature=0.2)
        # Parse the JSON response
        content = result.content
        # Extract JSON from response
        import re
        json_match = re.search(r'\[.*\]', content, re.DOTALL)
        if json_match:
            improvements = json.loads(json_match.group())
        else:
            improvements = []

        # Save each improvement
        saved = []
        for imp in improvements[:3]:
            saved.append(save_prompt_adjustment(
                adjustment_type=imp.get("type", "general"),
                description=imp.get("description", ""),
                prompt_addition=imp.get("prompt_addition", ""),
                triggered_by=f"feedback_analysis_{stats['total_feedback']}_entries",
            ))

        return {
            "status": "success",
            "current_stats": stats,
            "improvements_generated": len(saved),
            "improvements": saved,
            "llm_response": content,
        }
    except Exception as e:
        return {
            "status": "error",
            "message": str(e),
            "current_stats": stats,
        }


@app.post("/api/feedback/adjustments/{adjustment_id}/deactivate")
def api_deactivate_adjustment(adjustment_id: str):
    from feedback_analytics import deactivate_adjustment
    deactivate_adjustment(adjustment_id)
    return {"ok": True}


# --- JIRA Upstream Update Endpoint ---

class JiraUpdateRequest(BaseModel):
    jira_issue_key: str
    comment: Optional[str] = None
    update_status: Optional[str] = None  # e.g. "In Progress", "Resolved"
    resolution_note: Optional[str] = None


@app.post("/api/ticket/{ticket_id}/jira-update")
async def api_push_to_jira(ticket_id: int, body: JiraUpdateRequest):
    """Push AI-recommended solution from a triage ticket to upstream JIRA via REST API."""
    from config import JIRA_BASE_URL, JIRA_API_TOKEN, JIRA_USER_EMAIL
    import httpx

    if not JIRA_BASE_URL or not JIRA_API_TOKEN or not JIRA_USER_EMAIL:
        raise HTTPException(
            status_code=500,
            detail="JIRA integration not configured. Set JIRA_BASE_URL, JIRA_API_TOKEN, and JIRA_USER_EMAIL in .env"
        )

    # Fetch the triage ticket from DynamoDB
    from database import get_triage_ticket
    ticket = get_triage_ticket(ticket_id)
    if not ticket:
        raise HTTPException(status_code=404, detail=f"Triage ticket {ticket_id} not found")

    # Build the comment body from AI analysis
    comment_parts = []
    if body.comment:
        comment_parts.append(body.comment)
    else:
        # Auto-generate comment from ticket data
        comment_parts.append("## AI Triage Recommendation\n")
        if ticket.get("root_cause_category"):
            comment_parts.append(f"**Root Cause Category:** {ticket['root_cause_category']}")
        if ticket.get("root_cause"):
            comment_parts.append(f"\n**Root Cause Analysis:**\n{ticket['root_cause']}")
        if ticket.get("solution"):
            comment_parts.append(f"\n**Recommended Solution:**\n{ticket['solution']}")
        if ticket.get("estimated_resolution_time"):
            comment_parts.append(f"\n**Est. Resolution Time:** {ticket['estimated_resolution_time']}")
        if ticket.get("similar_tickets"):
            try:
                sim = json.loads(ticket["similar_tickets"]) if isinstance(ticket["similar_tickets"], str) else ticket["similar_tickets"]
                if sim:
                    ids = [s.get("ticket_id", "") for s in sim[:5]]
                    comment_parts.append(f"\n**Related Tickets:** {', '.join(ids)}")
            except (json.JSONDecodeError, TypeError):
                pass

    comment_body = "\n".join(comment_parts)
    base = JIRA_BASE_URL.rstrip("/")
    auth = (JIRA_USER_EMAIL, JIRA_API_TOKEN)

    async with httpx.AsyncClient(auth=auth) as client:
        # 1. Add comment
        comment_url = f"{base}/rest/api/2/issue/{body.jira_issue_key}/comment"
        resp = await client.post(comment_url, json={"body": comment_body})
        if resp.status_code not in (200, 201):
            raise HTTPException(status_code=resp.status_code, detail=f"JIRA comment failed: {resp.text}")

        # 2. Update status if requested
        if body.update_status:
            transition_url = f"{base}/rest/api/2/issue/{body.jira_issue_key}/transitions"
            resp = await client.get(transition_url)
            if resp.status_code == 200:
                transitions = resp.json()
                target_transition = None
                for t in transitions.get("transitions", []):
                    if t["name"].lower() == body.update_status.lower():
                        target_transition = t["id"]
                        break
                if target_transition:
                    resp2 = await client.post(transition_url, json={"transition": {"id": target_transition}})
                    if resp2.status_code not in (200, 204):
                        return {"ok": True, "warning": f"Comment added but status update failed: {resp2.text}"}

        # 3. Add resolution note if provided
        if body.resolution_note:
            update_url = f"{base}/rest/api/2/issue/{body.jira_issue_key}"
            await client.put(update_url, json={"fields": {"resolution": {"name": "Fixed"}}})

    return {"ok": True, "message": f"Pushed recommendation to {body.jira_issue_key}"}


@app.get("/api/jira/issue/{issue_key}")
async def api_get_jira_issue(issue_key: str):
    """Fetch issue details from upstream JIRA."""
    from config import JIRA_BASE_URL, JIRA_API_TOKEN, JIRA_USER_EMAIL
    import httpx

    if not JIRA_BASE_URL or not JIRA_API_TOKEN or not JIRA_USER_EMAIL:
        raise HTTPException(status_code=500, detail="JIRA integration not configured")

    auth = (JIRA_USER_EMAIL, JIRA_API_TOKEN)
    url = f"{JIRA_BASE_URL.rstrip('/')}/rest/api/2/issue/{issue_key}"

    async with httpx.AsyncClient(auth=auth) as client:
        resp = await client.get(url)
        if resp.status_code != 200:
            raise HTTPException(status_code=resp.status_code, detail=f"JIRA fetch failed: {resp.text}")
        data = resp.json()
        fields = data.get("fields", {})
        return {
            "key": data.get("key"),
            "summary": fields.get("summary"),
            "status": fields.get("status", {}).get("name"),
            "assignee": (fields.get("assignee") or {}).get("displayName"),
            "priority": (fields.get("priority") or {}).get("name"),
            "description": fields.get("description"),
            "comments": [
                {"author": c.get("author", {}).get("displayName"), "body": c.get("body"), "created": c.get("created")}
                for c in fields.get("comment", {}).get("comments", [])
            ],
        }


# Serve AngularJS frontend
frontend_path = os.path.join(os.path.dirname(__file__), "..", "frontend")
if os.path.exists(frontend_path):
    app.mount("/", StaticFiles(directory=frontend_path, html=True), name="frontend")
