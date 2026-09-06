"""
LangGraph Agent Workflows for JIRA Triage & RCA
=================================================
Implements agentic AI workflows using LangGraph for:
1. Chat Agent — RAG-based knowledge base Q&A
2. Triage Agent — Multi-step triage, RCA, and solution recommendation

Streaming endpoints use async LangGraph-compatible workflows.
"""

import json
import time
from typing import TypedDict
from langgraph.graph import StateGraph, END
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_aws import ChatBedrockConverse
from config import (
    BEDROCK_CHAT_MODEL, BEDROCK_EMBEDDING_MODEL, BEDROCK_REGION,
    AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_DEFAULT_REGION,
)
from retriever import search_similar_tickets, format_tickets_context
from guardrails import (
    validate_sources, check_hallucination, build_refusal_response,
    truncate_response, MIN_SIMILARITY_THRESHOLD,
)


# ---------------------------------------------------------------------------
# Shared LLM via LangChain-AWS
# ---------------------------------------------------------------------------

def _get_llm(temperature: float = 0.3):
    return ChatBedrockConverse(
        model=BEDROCK_CHAT_MODEL,
        region_name=BEDROCK_REGION or AWS_DEFAULT_REGION,
        aws_access_key_id=AWS_ACCESS_KEY_ID or None,
        aws_secret_access_key=AWS_SECRET_ACCESS_KEY or None,
        temperature=temperature,
        max_tokens=4096,
    )


# ---------------------------------------------------------------------------
# Chat Agent State Graph (LangGraph)
# ---------------------------------------------------------------------------

class ChatState(TypedDict):
    user_message: str
    top_k: int
    sources: list[dict]
    context: str
    answer: str
    metrics: dict
    weak_context: bool


def _chat_generate(state: ChatState) -> ChatState:
    """Generate response using LLM with retrieved context (used by graph)."""
    if state.get("weak_context"):
        return {**state, "answer": build_refusal_response()}

    system_prompt = (
        "You are a JIRA Knowledge Base Assistant. You help engineers understand "
        "past incidents, bugs, and their resolutions. Answer based on the historical "
        "ticket data provided below. Be specific, reference ticket IDs, and provide "
        "actionable insights. If no relevant data is found, say so honestly."
    )
    user_prompt = (
        f"Historical tickets for reference:\n\n{state['context']}\n\n---\n\n"
        f"User question: {state['user_message']}"
    )

    llm = _get_llm(temperature=0.3)
    response = llm.invoke([
        SystemMessage(content=system_prompt),
        HumanMessage(content=user_prompt),
    ])

    answer = response.content
    if check_hallucination(answer, state["sources"]):
        answer = build_refusal_response()
    answer = truncate_response(answer)

    usage = response.usage_metadata if hasattr(response, "usage_metadata") else {}
    prompt_tokens = usage.get("input_tokens", 0) if usage else 0
    completion_tokens = usage.get("output_tokens", 0) if usage else 0

    return {
        **state,
        "answer": answer,
        "metrics": {
            **state.get("metrics", {}),
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
    }


# Build graph (synchronous generation node — used for non-streaming)
chat_graph = StateGraph(ChatState)
chat_graph.add_node("generate", _chat_generate)
chat_graph.set_entry_point("generate")
chat_graph.add_edge("generate", END)
chat_agent = chat_graph.compile()


# ---------------------------------------------------------------------------
# Triage Agent State Graph (LangGraph)
# ---------------------------------------------------------------------------

class TriageState(TypedDict):
    description: str
    component: str
    technology: str
    environment: str
    sources: list[dict]
    context: str
    analysis: str
    metrics: dict
    weak_context: bool


def _triage_analyze(state: TriageState) -> TriageState:
    """Run triage and RCA analysis using the LLM (used by graph)."""
    if state.get("weak_context"):
        return {**state, "analysis": build_refusal_response()}

    from feedback_analytics import build_feedback_aware_triage_prompt

    base_prompt = (
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
    system_prompt = build_feedback_aware_triage_prompt(base_prompt)
    user_prompt = (
        f"Historical tickets for reference:\n\n{state['context']}\n\n---\n\n"
        f"PROBLEM DESCRIPTION: {state['description']}\n"
        f"Component: {state['component'] or 'Not specified'}\n"
        f"Technology: {state['technology'] or 'Not specified'}\n"
        f"Environment: {state['environment'] or 'Not specified'}\n\n"
        "Please perform triage, RCA, and propose a solution."
    )

    llm = _get_llm(temperature=0.2)
    response = llm.invoke([
        SystemMessage(content=system_prompt),
        HumanMessage(content=user_prompt),
    ])

    analysis = response.content
    if check_hallucination(analysis, state["sources"]):
        analysis = build_refusal_response()
    analysis = truncate_response(analysis)

    usage = response.usage_metadata if hasattr(response, "usage_metadata") else {}
    prompt_tokens = usage.get("input_tokens", 0) if usage else 0
    completion_tokens = usage.get("output_tokens", 0) if usage else 0

    return {
        **state,
        "analysis": analysis,
        "metrics": {
            **state.get("metrics", {}),
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
    }


# Build graph (synchronous analysis node — used for non-streaming)
triage_graph = StateGraph(TriageState)
triage_graph.add_node("analyze", _triage_analyze)
triage_graph.set_entry_point("analyze")
triage_graph.add_edge("analyze", END)
triage_agent = triage_graph.compile()


# ---------------------------------------------------------------------------
# RCA Agent State Graph (non-streaming)
# ---------------------------------------------------------------------------

class RCAState(TypedDict):
    description: str
    component: str
    technology: str
    environment: str
    sources: list[dict]
    context: str
    analysis: str
    metrics: dict
    weak_context: bool


def _rca_analyze(state: RCAState) -> RCAState:
    """Run RCA analysis using the LLM (used by graph)."""
    if state.get("weak_context"):
        return {**state, "analysis": build_refusal_response()}

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
    user_prompt = (
        f"Historical tickets for reference:\n\n{state['context']}\n\n---\n\n"
        f"PROBLEM DESCRIPTION: {state['description']}\n"
        f"Component: {state['component'] or 'Not specified'}\n"
        f"Technology: {state['technology'] or 'Not specified'}\n"
        f"Environment: {state['environment'] or 'Not specified'}\n\n"
        "Perform root cause analysis."
    )

    llm = _get_llm(temperature=0.2)
    response = llm.invoke([
        SystemMessage(content=rca_prompt),
        HumanMessage(content=user_prompt),
    ])

    analysis = response.content
    if check_hallucination(analysis, state["sources"]):
        analysis = build_refusal_response()
    analysis = truncate_response(analysis)

    usage = response.usage_metadata if hasattr(response, "usage_metadata") else {}
    prompt_tokens = usage.get("input_tokens", 0) if usage else 0
    completion_tokens = usage.get("output_tokens", 0) if usage else 0

    return {
        **state,
        "analysis": analysis,
        "metrics": {
            **state.get("metrics", {}),
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
    }


rca_graph = StateGraph(RCAState)
rca_graph.add_node("analyze", _rca_analyze)
rca_graph.set_entry_point("analyze")
rca_graph.add_edge("analyze", END)
rca_agent = rca_graph.compile()


# ---------------------------------------------------------------------------
# Solution Agent State Graph (non-streaming)
# ---------------------------------------------------------------------------

class SolutionState(TypedDict):
    description: str
    component: str
    technology: str
    environment: str
    root_cause: str
    sources: list[dict]
    context: str
    analysis: str
    metrics: dict
    weak_context: bool


def _solution_analyze(state: SolutionState) -> SolutionState:
    """Run solution recommendation using the LLM (used by graph)."""
    if state.get("weak_context"):
        return {**state, "analysis": build_refusal_response()}

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
        f"Historical tickets for reference:\n\n{state['context']}\n\n---\n\n"
        f"PROBLEM DESCRIPTION: {state['description']}\n"
        f"Component: {state['component'] or 'Not specified'}\n"
        f"Technology: {state['technology'] or 'Not specified'}\n"
        f"Environment: {state['environment'] or 'Not specified'}\n"
    )
    if state.get("root_cause"):
        user_content += f"\nROOT CAUSE ANALYSIS:\n{state['root_cause']}\n"
    user_content += "\nRecommend solutions."

    llm = _get_llm(temperature=0.2)
    response = llm.invoke([
        SystemMessage(content=solution_prompt),
        HumanMessage(content=user_content),
    ])

    analysis = response.content
    if check_hallucination(analysis, state["sources"]):
        analysis = build_refusal_response()
    analysis = truncate_response(analysis)

    usage = response.usage_metadata if hasattr(response, "usage_metadata") else {}
    prompt_tokens = usage.get("input_tokens", 0) if usage else 0
    completion_tokens = usage.get("output_tokens", 0) if usage else 0

    return {
        **state,
        "analysis": analysis,
        "metrics": {
            **state.get("metrics", {}),
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
    }


solution_graph = StateGraph(SolutionState)
solution_graph.add_node("analyze", _solution_analyze)
solution_graph.set_entry_point("analyze")
solution_graph.add_edge("analyze", END)
solution_agent = solution_graph.compile()


# ---------------------------------------------------------------------------
# Async Streaming Agents (used by SSE endpoints)
# ---------------------------------------------------------------------------

async def run_chat_agent_stream(user_message: str, top_k: int = 5):
    """Run chat agent with streaming via Bedrock invoke_model_with_response_stream."""
    from bedrock_client import chat_completion_stream_sse, get_cached_chat, _hash_key
    from guardrails import circuit_breaker
    from metrics import metrics

    t0 = time.perf_counter()

    tickets, emb_metrics = await search_similar_tickets(user_message, top_k=top_k)
    raw_sources = [
        {"ticket_id": t["ticket_id"], "summary": t["summary"],
         "similarity": round(t.get("similarity", 0) * 100, 1)}
        for t in tickets
    ]
    context_ids = [t["ticket_id"] for t in tickets]
    sources = validate_sources(raw_sources, context_ids)
    context = format_tickets_context(tickets)
    emb_ms = emb_metrics.get("embedding_ms", 0)

    if len(sources) == 0:
        refusal = build_refusal_response()
        total_ms = round((time.perf_counter() - t0) * 1000, 1)
        init_payload = json.dumps({"sources": [], "metrics": {
            "total_ms": total_ms, "embedding_ms": emb_ms,
            "llm_ms": 0, "prompt_tokens": 0,
            "completion_tokens": 0, "total_tokens": 0, "cached": False,
        }})
        yield f"event: init\ndata: {init_payload}\n\n"
        yield f"event: token\ndata: {json.dumps({'content': refusal})}\n\n"
        yield f"event: done\ndata: {init_payload}\n\n"
        return

    messages = [
        {"role": "system", "content": (
            "You are a JIRA Knowledge Base Assistant. You help engineers understand "
            "past incidents, bugs, and their resolutions. Answer based on the historical "
            "ticket data provided below. Be specific, reference ticket IDs, and provide "
            "actionable insights. If no relevant data is found, say so honestly."
        )},
        {"role": "user", "content": (
            f"Historical tickets for reference:\n\n{context}\n\n---\n\nUser question: {user_message}"
        )},
    ]

    cached = get_cached_chat(messages, temperature=0.3)
    if cached:
        total_ms = round((time.perf_counter() - t0) * 1000, 1)
        init_payload = json.dumps({"sources": sources, "metrics": {
            "total_ms": total_ms, "embedding_ms": emb_ms,
            "llm_ms": cached.duration_ms, "prompt_tokens": cached.prompt_tokens,
            "completion_tokens": cached.completion_tokens,
            "total_tokens": cached.total_tokens, "cached": True,
        }})
        yield f"event: init\ndata: {init_payload}\n\n"
        yield f"event: token\ndata: {json.dumps({'content': cached.content})}\n\n"
        yield f"event: done\ndata: {init_payload}\n\n"
        return

    init_payload = json.dumps({"sources": sources, "embedding_ms": emb_ms})
    yield f"event: init\ndata: {init_payload}\n\n"

    full_content = ""
    async for sse_event in chat_completion_stream_sse(messages, temperature=0.3):
        if sse_event.startswith("event: done"):
            data_line = sse_event.split("data: ", 1)[1].strip()
            done_data = json.loads(data_line)
            total_ms = round((time.perf_counter() - t0) * 1000, 1)
            llm_ms = done_data.get("total_ms", 0)
            stream_metrics = {
                "total_ms": total_ms,
                "embedding_ms": emb_ms,
                "llm_ms": llm_ms,
                "prompt_tokens": done_data.get("prompt_tokens", 0),
                "completion_tokens": done_data.get("completion_tokens", 0),
                "total_tokens": done_data.get("total_tokens", 0),
                "cached": False,
            }
            if check_hallucination(full_content, sources):
                full_content = build_refusal_response()
            full_content = truncate_response(full_content)
            from bedrock_client import cache_chat_result
            cache_key = _hash_key(json.dumps(messages, sort_keys=True), 0.3)
            cache_chat_result(cache_key, full_content, stream_metrics)
            yield f"event: done\ndata: {json.dumps(stream_metrics)}\n\n"
        elif sse_event.startswith("event: error"):
            yield sse_event
        else:
            token_line = sse_event.split("data: ", 1)[1].strip()
            token_data = json.loads(token_line)
            full_content += token_data.get("content", "")
            yield sse_event


async def run_triage_agent_stream(description: str, component: str = "",
                                  technology: str = "", environment: str = ""):
    """Run triage agent with streaming output."""
    from bedrock_client import chat_completion_stream_sse, get_cached_chat, _hash_key
    from guardrails import circuit_breaker
    from metrics import metrics

    t0 = time.perf_counter()

    search_query = f"{description} {component} {technology} {environment}"
    tickets, emb_metrics = await search_similar_tickets(search_query, top_k=3)
    raw_similar = [
        {"ticket_id": t["ticket_id"], "summary": t["summary"],
         "similarity": round(t.get("similarity", 0) * 100, 1)}
        for t in tickets
    ]
    context_ids = [t["ticket_id"] for t in tickets]
    similar = validate_sources(raw_similar, context_ids)
    context = format_tickets_context(tickets)
    emb_ms = emb_metrics.get("embedding_ms", 0)

    if len(similar) == 0:
        refusal = build_refusal_response()
        total_ms = round((time.perf_counter() - t0) * 1000, 1)
        init_payload = json.dumps({"similar_tickets": [], "metrics": {
            "total_ms": total_ms, "embedding_ms": emb_ms,
            "llm_ms": 0, "prompt_tokens": 0,
            "completion_tokens": 0, "total_tokens": 0, "cached": False,
        }})
        yield f"event: init\ndata: {init_payload}\n\n"
        yield f"event: token\ndata: {json.dumps({'content': refusal})}\n\n"
        yield f"event: done\ndata: {init_payload}\n\n"
        return

    from feedback_analytics import build_feedback_aware_triage_prompt

    base_triage_prompt = (
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
    triage_prompt = build_feedback_aware_triage_prompt(base_triage_prompt)

    messages = [
        {"role": "system", "content": triage_prompt},
        {"role": "user", "content": (
            f"Historical tickets for reference:\n\n{context}\n\n---\n\n"
            f"PROBLEM DESCRIPTION: {description}\n"
            f"Component: {component or 'Not specified'}\n"
            f"Technology: {technology or 'Not specified'}\n"
            f"Environment: {environment or 'Not specified'}\n\n"
            "Please perform triage, RCA, and propose a solution."
        )},
    ]

    cached = get_cached_chat(messages, temperature=0.2)
    if cached:
        total_ms = round((time.perf_counter() - t0) * 1000, 1)
        init_payload = json.dumps({"similar_tickets": similar, "metrics": {
            "total_ms": total_ms, "embedding_ms": emb_ms,
            "llm_ms": cached.duration_ms, "prompt_tokens": cached.prompt_tokens,
            "completion_tokens": cached.completion_tokens,
            "total_tokens": cached.total_tokens, "cached": True,
        }})
        yield f"event: init\ndata: {init_payload}\n\n"
        yield f"event: token\ndata: {json.dumps({'content': cached.content})}\n\n"
        yield f"event: done\ndata: {init_payload}\n\n"
        return

    init_payload = json.dumps({"similar_tickets": similar, "embedding_ms": emb_ms})
    yield f"event: init\ndata: {init_payload}\n\n"

    full_content = ""
    async for sse_event in chat_completion_stream_sse(messages, temperature=0.2):
        if sse_event.startswith("event: done"):
            data_line = sse_event.split("data: ", 1)[1].strip()
            done_data = json.loads(data_line)
            total_ms = round((time.perf_counter() - t0) * 1000, 1)
            llm_ms = done_data.get("total_ms", 0)
            stream_metrics = {
                "total_ms": total_ms,
                "embedding_ms": emb_ms,
                "llm_ms": llm_ms,
                "prompt_tokens": done_data.get("prompt_tokens", 0),
                "completion_tokens": done_data.get("completion_tokens", 0),
                "total_tokens": done_data.get("total_tokens", 0),
                "cached": False,
            }
            if check_hallucination(full_content, similar):
                full_content = build_refusal_response()
            full_content = truncate_response(full_content)
            from bedrock_client import cache_chat_result
            cache_key = _hash_key(json.dumps(messages, sort_keys=True), 0.2)
            cache_chat_result(cache_key, full_content, stream_metrics)
            yield f"event: done\ndata: {json.dumps(stream_metrics)}\n\n"
        elif sse_event.startswith("event: error"):
            yield sse_event
        else:
            token_line = sse_event.split("data: ", 1)[1].strip()
            token_data = json.loads(token_line)
            full_content += token_data.get("content", "")
            yield sse_event


# ---------------------------------------------------------------------------
# RCA Agent (Root Cause Analysis only)
# ---------------------------------------------------------------------------

async def run_rca_agent_stream(description: str, component: str = "",
                               technology: str = "", environment: str = ""):
    """Run RCA-focused agent with streaming output."""
    from bedrock_client import chat_completion_stream_sse, get_cached_chat, _hash_key
    from guardrails import circuit_breaker
    from metrics import metrics

    t0 = time.perf_counter()

    search_query = f"{description} {component} {technology} {environment}"
    tickets, emb_metrics = await search_similar_tickets(search_query, top_k=5)
    raw_similar = [
        {"ticket_id": t["ticket_id"], "summary": t["summary"],
         "similarity": round(t.get("similarity", 0) * 100, 1)}
        for t in tickets
    ]
    context_ids = [t["ticket_id"] for t in tickets]
    similar = validate_sources(raw_similar, context_ids)
    context = format_tickets_context(tickets)
    emb_ms = emb_metrics.get("embedding_ms", 0)

    if len(similar) == 0:
        refusal = build_refusal_response()
        total_ms = round((time.perf_counter() - t0) * 1000, 1)
        init_payload = json.dumps({"similar_tickets": [], "metrics": {
            "total_ms": total_ms, "embedding_ms": emb_ms,
            "llm_ms": 0, "prompt_tokens": 0,
            "completion_tokens": 0, "total_tokens": 0, "cached": False,
        }})
        yield f"event: init\ndata: {init_payload}\n\n"
        yield f"event: token\ndata: {json.dumps({'content': refusal})}\n\n"
        yield f"event: done\ndata: {init_payload}\n\n"
        return

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
        {"role": "user", "content": (
            f"Historical tickets for reference:\n\n{context}\n\n---\n\n"
            f"PROBLEM DESCRIPTION: {description}\n"
            f"Component: {component or 'Not specified'}\n"
            f"Technology: {technology or 'Not specified'}\n"
            f"Environment: {environment or 'Not specified'}\n\n"
            "Perform root cause analysis."
        )},
    ]

    cached = get_cached_chat(messages, temperature=0.2)
    if cached:
        total_ms = round((time.perf_counter() - t0) * 1000, 1)
        init_payload = json.dumps({"similar_tickets": similar, "metrics": {
            "total_ms": total_ms, "embedding_ms": emb_ms,
            "llm_ms": cached.duration_ms, "prompt_tokens": cached.prompt_tokens,
            "completion_tokens": cached.completion_tokens,
            "total_tokens": cached.total_tokens, "cached": True,
        }})
        yield f"event: init\ndata: {init_payload}\n\n"
        yield f"event: token\ndata: {json.dumps({'content': cached.content})}\n\n"
        yield f"event: done\ndata: {init_payload}\n\n"
        return

    init_payload = json.dumps({"similar_tickets": similar, "embedding_ms": emb_ms})
    yield f"event: init\ndata: {init_payload}\n\n"

    full_content = ""
    async for sse_event in chat_completion_stream_sse(messages, temperature=0.2):
        if sse_event.startswith("event: done"):
            data_line = sse_event.split("data: ", 1)[1].strip()
            done_data = json.loads(data_line)
            total_ms = round((time.perf_counter() - t0) * 1000, 1)
            llm_ms = done_data.get("total_ms", 0)
            stream_metrics = {
                "total_ms": total_ms,
                "embedding_ms": emb_ms,
                "llm_ms": llm_ms,
                "prompt_tokens": done_data.get("prompt_tokens", 0),
                "completion_tokens": done_data.get("completion_tokens", 0),
                "total_tokens": done_data.get("total_tokens", 0),
                "cached": False,
            }
            if check_hallucination(full_content, similar):
                full_content = build_refusal_response()
            full_content = truncate_response(full_content)
            from bedrock_client import cache_chat_result
            cache_key = _hash_key(json.dumps(messages, sort_keys=True), 0.2)
            cache_chat_result(cache_key, full_content, stream_metrics)
            yield f"event: done\ndata: {json.dumps(stream_metrics)}\n\n"
        elif sse_event.startswith("event: error"):
            yield sse_event
        else:
            token_line = sse_event.split("data: ", 1)[1].strip()
            token_data = json.loads(token_line)
            full_content += token_data.get("content", "")
            yield sse_event


# ---------------------------------------------------------------------------
# Solution Agent (Solution Recommendations only)
# ---------------------------------------------------------------------------

async def run_solution_agent_stream(description: str, component: str = "",
                                    technology: str = "", environment: str = "",
                                    root_cause: str = ""):
    """Run solution-focused agent with streaming output."""
    from bedrock_client import chat_completion_stream_sse, get_cached_chat, _hash_key
    from guardrails import circuit_breaker
    from metrics import metrics

    t0 = time.perf_counter()

    search_query = f"{description} {component} {technology} {environment}"
    tickets, emb_metrics = await search_similar_tickets(search_query, top_k=5)
    raw_similar = [
        {"ticket_id": t["ticket_id"], "summary": t["summary"],
         "similarity": round(t.get("similarity", 0) * 100, 1)}
        for t in tickets
    ]
    context_ids = [t["ticket_id"] for t in tickets]
    similar = validate_sources(raw_similar, context_ids)
    context = format_tickets_context(tickets)
    emb_ms = emb_metrics.get("embedding_ms", 0)

    if len(similar) == 0:
        refusal = build_refusal_response()
        total_ms = round((time.perf_counter() - t0) * 1000, 1)
        init_payload = json.dumps({"similar_tickets": [], "metrics": {
            "total_ms": total_ms, "embedding_ms": emb_ms,
            "llm_ms": 0, "prompt_tokens": 0,
            "completion_tokens": 0, "total_tokens": 0, "cached": False,
        }})
        yield f"event: init\ndata: {init_payload}\n\n"
        yield f"event: token\ndata: {json.dumps({'content': refusal})}\n\n"
        yield f"event: done\ndata: {init_payload}\n\n"
        return

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
        f"PROBLEM DESCRIPTION: {description}\n"
        f"Component: {component or 'Not specified'}\n"
        f"Technology: {technology or 'Not specified'}\n"
        f"Environment: {environment or 'Not specified'}\n"
    )
    if root_cause:
        user_content += f"\nROOT CAUSE ANALYSIS:\n{root_cause}\n"
    user_content += "\nRecommend solutions."

    messages = [
        {"role": "system", "content": solution_prompt},
        {"role": "user", "content": user_content},
    ]

    cached = get_cached_chat(messages, temperature=0.2)
    if cached:
        total_ms = round((time.perf_counter() - t0) * 1000, 1)
        init_payload = json.dumps({"similar_tickets": similar, "metrics": {
            "total_ms": total_ms, "embedding_ms": emb_ms,
            "llm_ms": cached.duration_ms, "prompt_tokens": cached.prompt_tokens,
            "completion_tokens": cached.completion_tokens,
            "total_tokens": cached.total_tokens, "cached": True,
        }})
        yield f"event: init\ndata: {init_payload}\n\n"
        yield f"event: token\ndata: {json.dumps({'content': cached.content})}\n\n"
        yield f"event: done\ndata: {init_payload}\n\n"
        return

    init_payload = json.dumps({"similar_tickets": similar, "embedding_ms": emb_ms})
    yield f"event: init\ndata: {init_payload}\n\n"

    full_content = ""
    async for sse_event in chat_completion_stream_sse(messages, temperature=0.2):
        if sse_event.startswith("event: done"):
            data_line = sse_event.split("data: ", 1)[1].strip()
            done_data = json.loads(data_line)
            total_ms = round((time.perf_counter() - t0) * 1000, 1)
            llm_ms = done_data.get("total_ms", 0)
            stream_metrics = {
                "total_ms": total_ms,
                "embedding_ms": emb_ms,
                "llm_ms": llm_ms,
                "prompt_tokens": done_data.get("prompt_tokens", 0),
                "completion_tokens": done_data.get("completion_tokens", 0),
                "total_tokens": done_data.get("total_tokens", 0),
                "cached": False,
            }
            if check_hallucination(full_content, similar):
                full_content = build_refusal_response()
            full_content = truncate_response(full_content)
            from bedrock_client import cache_chat_result
            cache_key = _hash_key(json.dumps(messages, sort_keys=True), 0.2)
            cache_chat_result(cache_key, full_content, stream_metrics)
            yield f"event: done\ndata: {json.dumps(stream_metrics)}\n\n"
        elif sse_event.startswith("event: error"):
            yield sse_event
        else:
            token_line = sse_event.split("data: ", 1)[1].strip()
            token_data = json.loads(token_line)
            full_content += token_data.get("content", "")
            yield sse_event
