"""
AWS Bedrock AgentCore Integration
==================================
Wraps existing LangGraph agents for deployment on AWS Bedrock AgentCore.

Supports:
- AgentCore Runtime: Managed execution environment
- AgentCore Memory: Persistent conversation history
- AgentCore Gateway: Expose endpoints as MCP tools
- AG-UI Protocol: Streaming agent responses
"""

import json
import asyncio
from typing import AsyncGenerator
from bedrock_agentcore import BedrockAgentCoreApp
from config import (
    AGENTCORE_MEMORY_ENABLED,
    AGENTCORE_MEMORY_NAMESPACE,
)


# ---------------------------------------------------------------------------
# AgentCore App
# ---------------------------------------------------------------------------

app = BedrockAgentCoreApp()


# ---------------------------------------------------------------------------
# Agent Entrypoint (main handler for AgentCore Runtime)
# ---------------------------------------------------------------------------

@app.entrypoint
async def agent_handler(request: dict) -> AsyncGenerator[dict, None]:
    """
    Main entrypoint for AgentCore Runtime.

    Accepts a request with:
    - prompt: str - The user's message
    - mode: str - "chat", "triage", "rca", or "solution"
    - session_id: str (optional) - For memory persistence
    - component: str (optional) - For triage/rca/solution
    - technology: str (optional) - For triage/rca/solution
    - environment: str (optional) - For triage/rca/solution
    - root_cause: str (optional) - For solution mode
    - top_k: int (optional) - Number of similar tickets for chat mode
    """
    prompt = request.get("prompt", "")
    mode = request.get("mode", "chat")
    session_id = request.get("session_id")
    component = request.get("component", "")
    technology = request.get("technology", "")
    environment = request.get("environment", "")
    root_cause = request.get("root_cause", "")
    top_k = request.get("top_k", 5)

    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("prompt must be a non-empty string")

    # Route to appropriate agent based on mode
    if mode == "triage":
        async for event in _triage_stream(prompt, component, technology, environment):
            yield event
    elif mode == "rca":
        async for event in _rca_stream(prompt, component, technology, environment):
            yield event
    elif mode == "solution":
        async for event in _solution_stream(prompt, component, technology, environment, root_cause):
            yield event
    else:
        # Default: chat mode
        async for event in _chat_stream(prompt, top_k, session_id):
            yield event


# ---------------------------------------------------------------------------
# Agent Stream Functions
# ---------------------------------------------------------------------------

async def _chat_stream(
    message: str, top_k: int = 5, session_id: str = None
) -> AsyncGenerator[dict, None]:
    """Stream chat agent responses."""
    from langgraph_agent import run_chat_agent_stream

    async for sse_event in run_chat_agent_stream(message, top_k=top_k):
        if sse_event.startswith("event: token"):
            data_line = sse_event.split("data: ", 1)[1].strip()
            token_data = json.loads(data_line)
            yield {"type": "token", "content": token_data.get("content", "")}
        elif sse_event.startswith("event: init"):
            data_line = sse_event.split("data: ", 1)[1].strip()
            init_data = json.loads(data_line)
            yield {"type": "init", "data": init_data}
        elif sse_event.startswith("event: done"):
            data_line = sse_event.split("data: ", 1)[1].strip()
            done_data = json.loads(data_line)
            yield {"type": "done", "data": done_data}


async def _triage_stream(
    description: str, component: str = "", technology: str = "", environment: str = ""
) -> AsyncGenerator[dict, None]:
    """Stream triage agent responses."""
    from langgraph_agent import run_triage_agent_stream

    async for sse_event in run_triage_agent_stream(description, component, technology, environment):
        if sse_event.startswith("event: token"):
            data_line = sse_event.split("data: ", 1)[1].strip()
            token_data = json.loads(data_line)
            yield {"type": "token", "content": token_data.get("content", "")}
        elif sse_event.startswith("event: init"):
            data_line = sse_event.split("data: ", 1)[1].strip()
            init_data = json.loads(data_line)
            yield {"type": "init", "data": init_data}
        elif sse_event.startswith("event: done"):
            data_line = sse_event.split("data: ", 1)[1].strip()
            done_data = json.loads(data_line)
            yield {"type": "done", "data": done_data}


async def _rca_stream(
    description: str, component: str = "", technology: str = "", environment: str = ""
) -> AsyncGenerator[dict, None]:
    """Stream RCA agent responses."""
    from langgraph_agent import run_rca_agent_stream

    async for sse_event in run_rca_agent_stream(description, component, technology, environment):
        if sse_event.startswith("event: token"):
            data_line = sse_event.split("data: ", 1)[1].strip()
            token_data = json.loads(data_line)
            yield {"type": "token", "content": token_data.get("content", "")}
        elif sse_event.startswith("event: init"):
            data_line = sse_event.split("data: ", 1)[1].strip()
            init_data = json.loads(data_line)
            yield {"type": "init", "data": init_data}
        elif sse_event.startswith("event: done"):
            data_line = sse_event.split("data: ", 1)[1].strip()
            done_data = json.loads(data_line)
            yield {"type": "done", "data": done_data}


async def _solution_stream(
    description: str, component: str = "", technology: str = "",
    environment: str = "", root_cause: str = ""
) -> AsyncGenerator[dict, None]:
    """Stream solution agent responses."""
    from langgraph_agent import run_solution_agent_stream

    async for sse_event in run_solution_agent_stream(
        description, component, technology, environment, root_cause
    ):
        if sse_event.startswith("event: token"):
            data_line = sse_event.split("data: ", 1)[1].strip()
            token_data = json.loads(data_line)
            yield {"type": "token", "content": token_data.get("content", "")}
        elif sse_event.startswith("event: init"):
            data_line = sse_event.split("data: ", 1)[1].strip()
            init_data = json.loads(data_line)
            yield {"type": "init", "data": init_data}
        elif sse_event.startswith("event: done"):
            data_line = sse_event.split("data: ", 1)[1].strip()
            done_data = json.loads(data_line)
            yield {"type": "done", "data": done_data}
