import os
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

# AWS Credentials
AWS_ACCESS_KEY_ID = os.getenv("AWS_ACCESS_KEY_ID", "")
AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY", "")
AWS_DEFAULT_REGION = os.getenv("AWS_DEFAULT_REGION", "us-east-1")

# AWS RDS PostgreSQL
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://admin:password@localhost:5432/jira_triage"
)

# AWS DynamoDB
DYNAMODB_TABLE_PREFIX = os.getenv("DYNAMODB_TABLE_PREFIX", "jira_triage")
DYNAMODB_SESSIONS_TABLE = os.getenv("DYNAMODB_SESSIONS_TABLE", "jira_triage_sessions")
DYNAMODB_MESSAGES_TABLE = os.getenv("DYNAMODB_MESSAGES_TABLE", "jira_triage_messages")
DYNAMODB_TRIAGE_TABLE = os.getenv("DYNAMODB_TRIAGE_TABLE", "jira_triage_tickets")
DYNAMODB_CACHE_TABLE = os.getenv("DYNAMODB_CACHE_TABLE", "jira_triage_cache")
DYNAMODB_RATE_LIMIT_TABLE = os.getenv("DYNAMODB_RATE_LIMIT_TABLE", "jira_triage_rate_limits")
DYNAMODB_CIRCUIT_BREAKER_TABLE = os.getenv("DYNAMODB_CIRCUIT_BREAKER_TABLE", "jira_triage_circuit_breaker")
DYNAMODB_FEEDBACK_TABLE = os.getenv("DYNAMODB_FEEDBACK_TABLE", "jira_triage_feedback")
DYNAMODB_PROMPT_IMPROVEMENTS_TABLE = os.getenv("DYNAMODB_PROMPT_IMPROVEMENTS_TABLE", "jira_triage_prompt_improvements")

# Amazon Bedrock
BEDROCK_REGION = os.getenv("BEDROCK_REGION", "us-east-1")
BEDROCK_CHAT_MODEL = os.getenv("BEDROCK_CHAT_MODEL", "anthropic.claude-sonnet-4-20250514-v1:0")
BEDROCK_EMBEDDING_MODEL = os.getenv("BEDROCK_EMBEDDING_MODEL", "cohere.embed-english-v3")
BEDROCK_EMBEDDING_DIMENSION = int(os.getenv("BEDROCK_EMBEDDING_DIMENSION", "1024"))

# App
APP_HOST = os.getenv("APP_HOST", "0.0.0.0")
APP_PORT = int(os.getenv("APP_PORT", "8000"))

# AWS Bedrock AgentCore (for managed agent deployment)
AGENTCORE_ENABLED = os.getenv("AGENTCORE_ENABLED", "false").lower() == "true"
AGENTCORE_MEMORY_ENABLED = os.getenv("AGENTCORE_MEMORY_ENABLED", "false").lower() == "true"
AGENTCORE_MEMORY_NAMESPACE = os.getenv("AGENTCORE_MEMORY_NAMESPACE", "jira-triage-agent")
AGENTCORE_MEMORY_SHORT_TERM_ID = os.getenv("AGENTCORE_MEMORY_SHORT_TERM_ID", "")
AGENTCORE_MEMORY_LONG_TERM_ID = os.getenv("AGENTCORE_MEMORY_LONG_TERM_ID", "")

# JIRA Upstream Integration
JIRA_BASE_URL = os.getenv("JIRA_BASE_URL", "")  # e.g. https://yourorg.atlassian.net
JIRA_API_TOKEN = os.getenv("JIRA_API_TOKEN", "")
JIRA_USER_EMAIL = os.getenv("JIRA_USER_EMAIL", "")
