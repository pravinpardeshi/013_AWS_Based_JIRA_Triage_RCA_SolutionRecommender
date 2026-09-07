"""
AgentCore Memory Integration
==============================
Provides persistent conversation memory using AWS Bedrock AgentCore Memory.

Supports:
- Short-term memory (session context)
- Long-term memory (cross-session knowledge)
- Automatic memory management and retrieval

Falls back to DynamoDB when AgentCore Memory is not configured.
"""

import json
import time
from typing import Optional
from config import (
    AGENTCORE_MEMORY_ENABLED,
    AGENTCORE_MEMORY_NAMESPACE,
    AGENTCORE_MEMORY_SHORT_TERM_ID,
    AGENTCORE_MEMORY_LONG_TERM_ID,
)


# ---------------------------------------------------------------------------
# Memory Client
# ---------------------------------------------------------------------------

_memory_client = None


def _get_memory_client():
    """Get or create AgentCore Memory client."""
    global _memory_client
    if _memory_client is None and AGENTCORE_MEMORY_ENABLED:
        try:
            from bedrock_agentcore.memory import AgentCoreMemory
            _memory_client = AgentCoreMemory()
        except ImportError:
            pass
    return _memory_client


# ---------------------------------------------------------------------------
# Short-Term Memory (Session Context)
# ---------------------------------------------------------------------------

def save_session_message(
    session_id: str,
    role: str,
    content: str,
    sources: list[dict] = None,
) -> bool:
    """
    Save a message to short-term memory (session context).

    Args:
        session_id: Unique session identifier
        role: "user" or "assistant"
        content: Message content
        sources: Optional list of source tickets

    Returns:
        True if saved successfully, False otherwise
    """
    if not AGENTCORE_MEMORY_ENABLED:
        # Fall back to DynamoDB
        from dynamodb import save_message
        save_message(session_id, role, content, sources or [])
        return True

    client = _get_memory_client()
    if client is None:
        from dynamodb import save_message
        save_message(session_id, role, content, sources or [])
        return True

    try:
        event = {
            "role": role,
            "content": content,
            "sources": sources or [],
            "timestamp": time.time(),
        }
        client.add_memory_event(
            memory_id=AGENTCORE_MEMORY_SHORT_TERM_ID,
            session_id=session_id,
            namespace=AGENTCORE_MEMORY_NAMESPACE,
            event=event,
        )
        return True
    except Exception:
        # Fall back to DynamoDB on error
        from dynamodb import save_message
        save_message(session_id, role, content, sources or [])
        return False


def get_session_messages(
    session_id: str,
    limit: int = 50,
) -> list[dict]:
    """
    Retrieve messages from short-term memory.

    Args:
        session_id: Unique session identifier
        limit: Maximum number of messages to retrieve

    Returns:
        List of message dictionaries
    """
    if not AGENTCORE_MEMORY_ENABLED:
        from dynamodb import get_session_messages
        return get_session_messages(session_id, limit)

    client = _get_memory_client()
    if client is None:
        from dynamodb import get_session_messages
        return get_session_messages(session_id, limit)

    try:
        events = client.get_memory_events(
            memory_id=AGENTCORE_MEMORY_SHORT_TERM_ID,
            session_id=session_id,
            namespace=AGENTCORE_MEMORY_NAMESPACE,
            limit=limit,
        )
        return [
            {
                "role": e.get("role", "user"),
                "content": e.get("content", ""),
                "sources": e.get("sources", []),
            }
            for e in events
        ]
    except Exception:
        from dynamodb import get_session_messages
        return get_session_messages(session_id, limit)


# ---------------------------------------------------------------------------
# Long-Term Memory (Cross-Session Knowledge)
# ---------------------------------------------------------------------------

def store_knowledge(
    key: str,
    content: str,
    metadata: dict = None,
) -> bool:
    """
    Store knowledge in long-term memory for future retrieval.

    Args:
        key: Unique knowledge key
        content: Knowledge content
        metadata: Optional metadata (component, technology, etc.)

    Returns:
        True if stored successfully
    """
    if not AGENTCORE_MEMORY_ENABLED:
        return False

    client = _get_memory_client()
    if client is None:
        return False

    try:
        event = {
            "key": key,
            "content": content,
            "metadata": metadata or {},
            "timestamp": time.time(),
        }
        client.add_memory_event(
            memory_id=AGENTCORE_MEMORY_LONG_TERM_ID,
            session_id="knowledge",
            namespace=AGENTCORE_MEMORY_NAMESPACE,
            event=event,
        )
        return True
    except Exception:
        return False


def search_knowledge(
    query: str,
    limit: int = 5,
) -> list[dict]:
    """
    Search long-term memory for relevant knowledge.

    Args:
        query: Search query
        limit: Maximum results

    Returns:
        List of matching knowledge entries
    """
    if not AGENTCORE_MEMORY_ENABLED:
        return []

    client = _get_memory_client()
    if client is None:
        return []

    try:
        events = client.search_memory_events(
            memory_id=AGENTCORE_MEMORY_LONG_TERM_ID,
            namespace=AGENTCORE_MEMORY_NAMESPACE,
            query=query,
            limit=limit,
        )
        return [
            {
                "key": e.get("key", ""),
                "content": e.get("content", ""),
                "metadata": e.get("metadata", {}),
            }
            for e in events
        ]
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Feedback Memory (for prompt improvement)
# ---------------------------------------------------------------------------

def store_feedback(
    ticket_ref: str,
    rating: int,
    comment: str,
    component: str = "",
    technology: str = "",
) -> bool:
    """
    Store feedback in long-term memory for analysis.

    Args:
        ticket_ref: Ticket reference ID
        rating: Star rating (1-5)
        comment: User comment
        component: Ticket component
        technology: Ticket technology

    Returns:
        True if stored successfully
    """
    if not AGENTCORE_MEMORY_ENABLED:
        return False

    client = _get_memory_client()
    if client is None:
        return False

    try:
        event = {
            "type": "feedback",
            "ticket_ref": ticket_ref,
            "rating": rating,
            "comment": comment,
            "component": component,
            "technology": technology,
            "timestamp": time.time(),
        }
        client.add_memory_event(
            memory_id=AGENTCORE_MEMORY_LONG_TERM_ID,
            session_id="feedback",
            namespace=AGENTCORE_MEMORY_NAMESPACE,
            event=event,
        )
        return True
    except Exception:
        return False
