"""
AgentCore Gateway Tools
========================
Exposes JIRA Triage Agent endpoints as MCP-compatible tools
for use with AgentCore Gateway.

These tools can be invoked by other agents via the Agent-to-Agent (A2A)
protocol or through AgentCore Gateway's MCP interface.
"""

import json
from typing import Optional


# ---------------------------------------------------------------------------
# Tool Definitions for AgentCore Gateway
# ---------------------------------------------------------------------------

TRIAGE_TOOL = {
    "name": "jira_triage",
    "description": (
        "Analyze a JIRA incident and provide triage, root cause analysis, "
        "and solution recommendations based on historical ticket data."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "description": {
                "type": "string",
                "description": "Problem description or incident report"
            },
            "component": {
                "type": "string",
                "description": "Affected component (e.g., payment-gateway, auth-service)"
            },
            "technology": {
                "type": "string",
                "description": "Technology stack (e.g., Java, Python, React)"
            },
            "environment": {
                "type": "string",
                "description": "Environment (Production, Staging, QA, Development, UAT)"
            }
        },
        "required": ["description"]
    }
}

RCA_TOOL = {
    "name": "jira_rca",
    "description": (
        "Perform Root Cause Analysis on a JIRA incident. "
        "Identifies root cause category, probable cause, contributing factors, "
        "and verification steps based on historical patterns."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "description": {
                "type": "string",
                "description": "Problem description or incident report"
            },
            "component": {
                "type": "string",
                "description": "Affected component"
            },
            "technology": {
                "type": "string",
                "description": "Technology stack"
            },
            "environment": {
                "type": "string",
                "description": "Environment"
            }
        },
        "required": ["description"]
    }
}

SOLUTION_TOOL = {
    "name": "jira_solution",
    "description": (
        "Recommend solutions for a JIRA incident. "
        "Provides immediate mitigation, permanent fix, implementation steps, "
        "verification, and prevention recommendations."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "description": {
                "type": "string",
                "description": "Problem description or incident report"
            },
            "component": {
                "type": "string",
                "description": "Affected component"
            },
            "technology": {
                "type": "string",
                "description": "Technology stack"
            },
            "environment": {
                "type": "string",
                "description": "Environment"
            },
            "root_cause": {
                "type": "string",
                "description": "Optional root cause analysis to inform solution recommendations"
            }
        },
        "required": ["description"]
    }
}

KNOWLEDGE_SEARCH_TOOL = {
    "name": "jira_knowledge_search",
    "description": (
        "Search the JIRA knowledge base for similar past incidents. "
        "Returns relevant historical tickets with similarity scores."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Search query or question about past incidents"
            },
            "top_k": {
                "type": "integer",
                "description": "Number of similar tickets to return (default: 5)"
            }
        },
        "required": ["query"]
    }
}

FEEDBACK_TOOL = {
    "name": "jira_feedback_stats",
    "description": (
        "Get feedback analytics for triage quality. "
        "Returns average rating, distribution, and per-component/technology breakdowns."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {},
        "required": []
    }
}


# ---------------------------------------------------------------------------
# Tool Registry
# ---------------------------------------------------------------------------

AGENTCORE_TOOLS = [
    TRIAGE_TOOL,
    RCA_TOOL,
    SOLUTION_TOOL,
    KNOWLEDGE_SEARCH_TOOL,
    FEEDBACK_TOOL,
]


def get_tool_definitions() -> list[dict]:
    """Return all tool definitions for AgentCore Gateway."""
    return AGENTCORE_TOOLS


def get_tool_by_name(name: str) -> Optional[dict]:
    """Get a specific tool definition by name."""
    for tool in AGENTCORE_TOOLS:
        if tool["name"] == name:
            return tool
    return None
