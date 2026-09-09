# JIRA Triage & Knowledge Agent (AWS)

A Retrieval-Augmented Generation (RAG) powered assistant that helps engineers understand past JIRA incidents, perform ticket triage, and conduct Root Cause Analysis (RCA) using historical ticket data. Built with LangGraph agentic AI workflows, deployed on AWS.

## Features

- **Knowledge Base Chat** — Ask questions about past incidents and get answers grounded in historical JIRA ticket data
- **Ticket Triage & RCA** — Submit a problem description and receive automated priority assignment, root cause analysis, and solution recommendations
- **Streaming Responses** — Real-time token-by-token streaming via Server-Sent Events (SSE)
- **LangGraph Agentic AI** — Multi-step agent workflows for chat retrieval and triage analysis using LangGraph StateGraphs
- **Chat Sessions** — Persistent conversation history with session management via DynamoDB
- **Similarity Search** — Vector-based semantic search using pgvector on AWS RDS PostgreSQL
- **DynamoDB-Backed Caching** — Embedding and chat completion caches stored in DynamoDB with TTL, shared across all instances
- **DynamoDB-Backed Guardrails** — Rate limiting and circuit breaker state stored in DynamoDB, shared across all instances
- **Data Ingestion Pipeline** — Batch CSV ingestion with validation, deduplication, and Bedrock embedding generation
- **Feedback Loop** — Star ratings on triage tickets drive AI-powered prompt improvement, stored in DynamoDB
- **JIRA Upstream Integration** — Push AI-recommended solutions directly to upstream JIRA via REST API (add comment, transition status)
- **Human-in-the-Loop** — Review triage results before pushing to JIRA; save locally first, then push when ready
- **Health Dashboard** — Monitor RDS, DynamoDB, and Bedrock connectivity, ticket counts, and circuit breaker state
- **Observability Metrics** — Prometheus-compatible metrics endpoint with a built-in dashboard for HTTP, LLM, DB, cache, and guardrail monitoring

## Architecture

```
<<<<<<< HEAD
<<<<<<< HEAD
   ┌──────────────┐      ┌───────────────────┐        ┌─────────────────────┐
   │   Frontend   │────▶ │  FastAPI Backend  │  ────▶ │  AWS RDS PostgreSQL │
   │  (AngularJS) │◀──── │   (Uvicorn)       │  ◀──── │  + pgvector         │
   └──────────────┘      └────────┬──────────┘        └─────────────────────┘
=======
   ┌──────────────┐      ┌───────────────────┐      ┌─────────────────────┐
   │   Frontend   │────▶ │  FastAPI Backend  │────▶ │  AWS RDS PostgreSQL │
   │  (AngularJS) │◀──── │   (Uvicorn)       │◀──── │  + pgvector         │
   └──────────────┘      └────────┬──────────┘      └─────────────────────┘
>>>>>>> a7d33327fb30ae4df5bc14ce1966c6d1ee7578ee
=======
   ┌──────────────┐      ┌───────────────────┐       ┌─────────────────────┐
   │   Frontend   │────▶ │  FastAPI Backend  │ ────▶ │  AWS RDS PostgreSQL │
   │  (AngularJS) │◀──── │   (Uvicorn)       │ ◀──── │  + pgvector         │
   └──────────────┘      └────────┬──────────┘       └─────────────────────┘
>>>>>>> 2802815238f1e9d3bb354a466d318a075e3a7f25
                                  │
                  ┌───────────────┼───────────┐
                  │               │           │
          ┌───────▼───────┐ ┌─────▼─────┐ ┌───▼───────────┐
          │ Amazon        │ │  AWS      │ │  LangGraph    │
          │ Bedrock       │ │ DynamoDB  │ │  Agent        │
          │ Claude+Cohere │ │ 9 tables  │ │  Workflows    │
          └───────────────┘ └───────────┘ └───────────────┘
```

### DynamoDB Tables

All 9 tables are auto-created on application startup:

| Table | Purpose | Key |
|-------|---------|-----|
| `jira_triage_sessions` | Chat session metadata | `id` (Number) |
| `jira_triage_messages` | Chat message history | `session_id` (Hash) + `id` (Range) |
| `jira_triage_tickets` | Saved triage tickets | `id` (Number) |
| `jira_triage_cache` | Embedding + chat cache (TTL) | `cache_key` (String) + GSI on `category` |
| `jira_triage_rate_limits` | Per-IP rate limit timestamps | `client_ip` (String) |
| `jira_triage_circuit_breaker` | Circuit breaker state per service | `service_name` (String) |
| `jira_triage_feedback` | User feedback on triage tickets | `ticket_ref` (String) |
| `jira_triage_prompt_improvements` | AI-generated prompt adjustments | `adjustment_id` (String) |

### Data Flow

1. **Ingestion**: CSV tickets → Bedrock Cohere embeddings → stored in RDS PostgreSQL with pgvector
2. **Query**: User query → Bedrock embedding → pgvector cosine similarity search → relevant tickets retrieved
3. **Generation**: Retrieved context + query → LangGraph agent → Bedrock Claude → streamed response via SSE
4. **Caching**: Embeddings cached 24h, chat completions cached 1h — both in DynamoDB with local L1 hot-key cache
5. **Storage**: Chat sessions, messages, and triage tickets stored in DynamoDB (PAY_PER_REQUEST billing)

## Tech Stack

| Layer       | Technology                                                        |
|-------------|-------------------------------------------------------------------|
| Frontend    | AngularJS 1.8.3                                                   |
| Backend     | Python 3.12, FastAPI, Uvicorn                                     |
| Database    | AWS RDS PostgreSQL 15+ with pgvector                              |
| NoSQL       | AWS DynamoDB (sessions, messages, triage, cache, guardrails)      |
| LLM         | Amazon Bedrock — Claude Sonnet (chat/RCA)                         |
| Embeddings  | Amazon Bedrock — Cohere embed-english-v3 (1024-dim)               |
| Agentic AI  | LangGraph StateGraph workflows                                    |
| AgentCore   | AWS Bedrock AgentRuntime (managed agent deployment)               |
| HTTP Client | httpx (async), boto3 (AWS SDK)                                    |

## AWS Bedrock AgentCore Deployment

This application is ready for deployment on AWS Bedrock AgentCore. AgentCore provides managed infrastructure for running AI agents at scale with built-in memory, identity, and observability.

### AgentCore Components Used

| Component | Purpose | Configuration |
|-----------|---------|---------------|
| **Runtime** | Managed execution environment for the agent | `AGENTCORE_ENABLED=true` |
| **Memory** | Persistent conversation history across sessions | `AGENTCORE_MEMORY_ENABLED=true` |
| **Gateway** | Expose triage/RCA/solution as MCP tools for other agents | Via `agentcore_tools.py` |
| **Identity** | OAuth-based authentication (optional) | Via AWS IAM/Cognito |

### Project Structure (AgentCore)

```
backend/
├── agentcore_app.py        # AgentCore Runtime entrypoint
├── agentcore_tools.py      # MCP tool definitions for AgentCore Gateway
├── agentcore_memory.py     # AgentCore Memory integration
├── main.py                 # FastAPI app (local development)
├── langgraph_agent.py      # LangGraph agent workflows
└── ...
```

### Local Development (No AgentCore)

Run the FastAPI server directly:

```bash
cd backend
uvicorn main:app --host 0.0.0.0 --port 8000
```

All AgentCore features are disabled by default. The app uses DynamoDB for sessions and messages.

### Deploy to AgentCore Runtime

**Step 1: Install AgentCore CLI**

```bash
npm i -g @aws/agentcore
```

**Step 2: Initialize AgentCore Project**

```bash
cd 019_AWS_JIRA_Ticket_Solution
agentcore create --name jira-triage-agent --defaults
```

**Step 3: Configure Environment**

Update `backend/.env`:

```env
AGENTCORE_ENABLED=true
AGENTCORE_MEMORY_ENABLED=true
AGENTCORE_MEMORY_NAMESPACE=jira-triage-agent
AGENTCORE_MEMORY_SHORT_TERM_ID=<your-short-term-memory-id>
AGENTCORE_MEMORY_LONG_TERM_ID=<your-long-term-memory-id>
```

**Step 4: Deploy**

```bash
agentcore deploy
```

AgentCore handles packaging, infrastructure provisioning, and deployment automatically.

### AgentCore Gateway Tools

The following MCP tools are available for other agents to invoke:

| Tool | Description |
|------|-------------|
| `jira_triage` | Full triage analysis (priority, RCA, solution) |
| `jira_rca` | Root cause analysis only |
| `jira_solution` | Solution recommendations only |
| `jira_knowledge_search` | Search historical tickets |
| `jira_feedback_stats` | Get feedback analytics |

These tools are defined in `backend/agentcore_tools.py` and can be registered with AgentCore Gateway.

### AgentCore Memory

When `AGENTCORE_MEMORY_ENABLED=true`:

- **Short-term memory**: Stores chat session messages in AgentCore Memory
- **Long-term memory**: Stores feedback and knowledge for cross-session learning
- **Fallback**: Automatically falls back to DynamoDB if AgentCore Memory is unavailable

### AG-UI Protocol Support

The agent supports the AG-UI protocol for streaming responses:

```python
from bedrock_agentcore.runtime import serve_ag_ui

# Deploy with AG-UI support
serve_ag_ui(agentcore_app.app)
```

### A2A Protocol Support

Other agents can invoke this agent via the A2A protocol:

```python
from bedrock_agentcore.runtime import serve_a2a

# Deploy with A2A support
serve_a2a(agentcore_app.app)
```

## UI Layout

The application uses an AngularJS SPA with a sidebar navigation. **Triage & RCA** is the landing page (default route).

### Sidebar Navigation
1. **Triage & RCA** — Landing page. Submit a problem description, get AI-powered triage, root cause analysis, and solution recommendations
2. **AI Chat** — Conversational knowledge base chat with session management
3. **Tickets** — Browse, search, filter, and manage saved triage tickets
4. **Feedback** — View feedback analytics and AI-generated prompt improvements
5. **Health** — System health dashboard (RDS, DynamoDB, Bedrock connectivity)
6. **Metrics** — Observability dashboard (HTTP, LLM, DB, cache, guardrails, AgentCore)

### Triage → JIRA Push Flow
1. User submits a problem description on the Triage page
2. AI streams triage analysis (root cause, solution, priority, similar tickets)
3. User clicks **"Save as Ticket"** — saves to DynamoDB
4. **"Push to Upstream JIRA"** section appears after saving
5. User enters a JIRA issue key (e.g. `PROJ-1234`) and optionally selects a status transition
6. Clicking **"Push to JIRA"** adds the AI recommendation as a JIRA comment via REST API

## Project Structure

```
019_AWS_JIRA_Ticket_Solution/
├── backend/
│   ├── main.py                  # FastAPI application and API routes
│   ├── config.py                # Environment variable configuration
│   ├── database.py              # RDS PostgreSQL connection, schema init
│   ├── bedrock_client.py        # Amazon Bedrock client (Claude + Cohere)
│   ├── dynamodb.py              # DynamoDB client (sessions, messages, triage)
│   ├── dynamodb_cache.py        # DynamoDB-backed cache with TTL + L1 hot-key cache
│   ├── dynamodb_guardrails.py   # DynamoDB-backed rate limiter + circuit breaker
│   ├── langgraph_agent.py       # LangGraph agent workflows (chat, triage, RCA, solution)
│   ├── retriever.py             # Vector similarity search and ticket formatting
│   ├── guardrails.py            # Input/output validation, jailbreak detection
│   ├── feedback_analytics.py    # Feedback recording, analytics, prompt improvement engine
│   ├── metrics.py               # Prometheus-compatible metrics collector
│   ├── agentcore_app.py         # AgentCore Runtime entrypoint (managed deployment)
│   ├── agentcore_tools.py       # MCP tool definitions for AgentCore Gateway
│   ├── agentcore_memory.py      # AgentCore Memory integration
│   ├── requirements.txt         # Python dependencies
│   └── .env.example             # Environment variable template (copy to .env)
├── frontend/
│   ├── index.html               # SPA entry point
│   ├── css/                     # Stylesheets
│   ├── js/                      # AngularJS app, controllers, services
│   └── partials/                # View templates
├── dataset/
│   └── jira_j2ee_tickets_rag_dataset_200Count.csv
├── ingest_pipeline.py           # Batch CSV ingestion pipeline with CLI
├── jira_daily_ingest.py         # JIRA incremental daily ingestion (REST API + Bedrock)
├── run.sh                       # Startup/management script
├── pyproject.toml               # Project metadata
├── .python-version              # Python 3.12
└── .gitignore
```

## AWS Prerequisites

### 1. AWS Account & IAM

- An active AWS account with programmatic access
- IAM user/role with permissions for:
  - **Amazon Bedrock**: `bedrock:InvokeModel`, `bedrock:InvokeModelWithResponseStream`, `bedrock:ListFoundationModels`
  - **AWS RDS**: Connect to a PostgreSQL instance
  - **AWS DynamoDB**: Full CRUD on the application tables (auto-created on startup)
- Ensure Claude (Anthropic) and Cohere Embed models are enabled in Bedrock Model Access

### 2. AWS RDS PostgreSQL

Create a PostgreSQL 15+ instance with pgvector:

```bash
aws rds create-db-instance \
  --db-instance-identifier jira-triage-db \
  --db-instance-class db.t3.medium \
  --engine postgres \
  --engine-version 15 \
  --master-username admin \
  --master-user-password <your-password> \
  --allocated-storage 20 \
  --publicly-accessible
```

Then enable pgvector extension:

```sql
CREATE EXTENSION IF NOT EXISTS vector;
```

> **Note**: AWS RDS for PostgreSQL supports pgvector on versions 15+ with the `rds.enable_vector` parameter.

### 3. AWS DynamoDB

All 9 tables are **auto-created on application startup**. No manual setup required. If you prefer to create them manually, see the table definitions in `dynamodb.py`, `dynamodb_cache.py`, `dynamodb_guardrails.py`, and `feedback_analytics.py`.

### 4. Amazon Bedrock Model Access

Ensure these models are enabled in the [Bedrock console](https://console.aws.amazon.com/bedrock/) under **Model access**:

- **Anthropic Claude** — `anthropic.claude-sonnet-4-20250514-v1:0` (or your preferred Claude model)
- **Cohere Embed** — `cohere.embed-english-v3`

## Setup

### 1. Clone the Repository

```bash
git clone <your-repo-url>
cd 019_AWS_JIRA_Ticket_Solution
```

### 2. Create Virtual Environment

```bash
python -m venv venv
source venv/bin/activate
```

### 3. Install Dependencies

```bash
pip install -r backend/requirements.txt
```

### 4. Configure Environment Variables

Copy the example and fill in your AWS credentials:

```bash
cp backend/.env.example backend/.env
```

Edit `backend/.env`:

```env
# =============================================================================
# AWS Configuration
# =============================================================================
AWS_ACCESS_KEY_ID=your_access_key_here
AWS_SECRET_ACCESS_KEY=your_secret_key_here
AWS_DEFAULT_REGION=us-east-1

# =============================================================================
# AWS RDS PostgreSQL (with pgvector extension)
# =============================================================================
DATABASE_URL=postgresql://admin:your_password@your-rds-endpoint.region.rds.amazonaws.com:5432/jira_triage

# =============================================================================
# AWS DynamoDB Tables (all auto-created on startup)
# =============================================================================
DYNAMODB_TABLE_PREFIX=jira_triage
DYNAMODB_SESSIONS_TABLE=jira_triage_sessions
DYNAMODB_MESSAGES_TABLE=jira_triage_messages
DYNAMODB_TRIAGE_TABLE=jira_triage_tickets
DYNAMODB_CACHE_TABLE=jira_triage_cache
DYNAMODB_RATE_LIMIT_TABLE=jira_triage_rate_limits
DYNAMODB_CIRCUIT_BREAKER_TABLE=jira_triage_circuit_breaker
DYNAMODB_FEEDBACK_TABLE=jira_triage_feedback
DYNAMODB_PROMPT_IMPROVEMENTS_TABLE=jira_triage_prompt_improvements

# =============================================================================
# Amazon Bedrock (LLM + Embeddings)
# =============================================================================
BEDROCK_REGION=us-east-1
BEDROCK_CHAT_MODEL=anthropic.claude-sonnet-4-20250514-v1:0
BEDROCK_EMBEDDING_MODEL=cohere.embed-english-v3
BEDROCK_EMBEDDING_DIMENSION=1024

# =============================================================================
# Application
# =============================================================================
APP_HOST=0.0.0.0
APP_PORT=8000

# =============================================================================
# JIRA Upstream Integration (for pushing AI-recommended solutions)
# =============================================================================
JIRA_BASE_URL=https://yourorg.atlassian.net
JIRA_API_TOKEN=your_jira_api_token_here
JIRA_USER_EMAIL=your_email@company.com
```

### 5. Ingest Ticket Data

```bash
# Ingest all CSVs from dataset/
python ingest_pipeline.py

# Or ingest a specific file
python ingest_pipeline.py --file dataset/jira_j2ee_tickets_rag_dataset_200Count.csv

# Dry run (validate without ingesting)
python ingest_pipeline.py --dry-run

# Show DB stats
python ingest_pipeline.py --stats
```

### 6. Start the Server

```bash
# Using the management script
./run.sh start

# Or directly with uvicorn
cd backend && uvicorn main:app --host 0.0.0.0 --port 8000
```

Open [http://localhost:8000](http://localhost:8000) in your browser.

## DynamoDB-Backed Caching

The caching layer uses DynamoDB with a two-tier architecture:

```
Request → L1 Local Cache (100 items) → DynamoDB Cache → Bedrock API
              (in-memory dict)          (persistent, shared)
```

| Cache | TTL | Max Size | Purpose |
|-------|-----|----------|---------|
| **Embedding** | 24 hours | 2,000 entries | Same text → same vector, rarely changes |
| **Chat** | 1 hour | 500 entries | Identical prompts → cached response |

**How it works:**
1. On cache hit: L1 local cache returns instantly (sub-millisecond)
2. On L1 miss: DynamoDB GetItem (~5ms), populates L1 for next time
3. On cache miss: Call Bedrock API, store result in both L1 and DynamoDB
4. Eviction: DynamoDB TTL auto-expires old entries; background scan cleans up overflow

**Benefits over in-memory only:**
- Cache persists across server restarts
- Shared across multiple Uvicorn workers
- Shared across multiple container instances
- No memory pressure on the application server

## DynamoDB-Backed Guardrails

Rate limiting and circuit breaker state are stored in DynamoDB for shared state across instances:

### Rate Limiter

- Stores per-IP request timestamps in `jira_triage_rate_limits`
- Window: 60 seconds, max 30 requests per IP
- L1 local cache for hot IPs (sub-millisecond checks)
- DynamoDB TTL auto-cleans expired entries
- Fail-open on DynamoDB errors (availability over strictness)

### Circuit Breaker

The circuit breaker is a resilience pattern that prevents cascading failures when an external service (Bedrock, RDS, Ollama) is down or slow. Instead of letting every request hang waiting for timeouts, the circuit breaker **fails fast** and stops calling the failing service until it recovers.

#### How It Works (3 States)

```
           ┌───────────┐ failures >= 3   ┌───────────┐  30s cooldown   ┌───────────┐
  ───────▶ │  CLOSED   │──────────────▶  │   OPEN    │──────────────▶  │ HALF-OPEN │
  (normal) │           │                 │           │                 │           │
           │  requests │                 │  reject   │                 │ test one  │
           │  pass     │                 │  all      │                 │ request   │
           └───────────┘                 └───────────┘                 └───────────┘
                ▲                                                         │
                │           success                                       │
                └─────────────────────────────────────────────────────────┘
```

| State | Behavior |
|-------|----------|
| **CLOSED** | Normal operation. Requests pass through. Failures are counted. |
| **OPEN** | After 3 consecutive failures, the breaker trips. All requests are **immediately rejected** without calling the failing service. Users get an instant error instead of a 30s timeout. |
| **HALF-OPEN** | After 30s cooldown, one test request is allowed through. If it succeeds → back to CLOSED. If it fails → back to OPEN. |

#### Why It's Needed

- **Prevents cascading failure** — If Bedrock goes down, your app won't exhaust threads/connections waiting for timeouts on every request
- **Fails fast** — Users get an immediate "service unavailable" error instead of hanging for 30+ seconds
- **Protects resources** — Threads, memory, and DB connections aren't wasted on calls that will fail
- **Auto-recovery** — Once the service comes back, the breaker closes automatically and traffic resumes

#### DynamoDB-Backed (Shared State)

- State stored in `jira_triage_circuit_breaker` table
- All instances share the same circuit breaker state — one instance trips it, all respect it
- DynamoDB atomic writes (`UPDATE ... SET failure_count = failure_count + 1`) ensure thread-safety without locks
- Each external service (Bedrock, RDS, Ollama) has its own independent circuit breaker

## API Endpoints

| Method | Endpoint                      | Description                                    |
|--------|-------------------------------|------------------------------------------------|
| GET    | `/api/health`                 | Health check (RDS, DynamoDB, Bedrock)          |
| GET    | `/api/stats`                  | Ticket counts, categories, cache stats         |
| GET    | `/api/ticket/{ticket_id}`     | Get a specific ticket by ID                    |
| POST   | `/api/chat`                   | Non-streaming knowledge base chat              |
| POST   | `/api/chat/stream`            | Streaming knowledge base chat (SSE)            |
| POST   | `/api/triage`                 | Non-streaming triage & RCA                     |
| POST   | `/api/triage/stream`          | Streaming triage & RCA (SSE)                   |
| POST   | `/api/rca`                    | Non-streaming root cause analysis only         |
| POST   | `/api/rca/stream`             | Streaming root cause analysis (SSE)            |
| POST   | `/api/solution`               | Non-streaming solution recommendations         |
| POST   | `/api/solution/stream`        | Streaming solution recommendations (SSE)       |
| GET    | `/api/sessions`               | List all chat sessions (DynamoDB)              |
| POST   | `/api/sessions`               | Create a new chat session                      |
| GET    | `/api/sessions/{id}`          | Get session with messages                      |
| PUT    | `/api/sessions/{id}`          | Rename a session                               |
| DELETE | `/api/sessions/{id}`          | Delete a session                               |
| POST   | `/api/sessions/{id}/messages` | Save a message to a session                    |
| GET    | `/metrics`                    | Prometheus-compatible metrics (text/plain)     |
| GET    | `/api/metrics/summary`        | JSON metrics summary for the dashboard         |
| GET    | `/api/feedback/stats`         | Feedback analytics (avg rating, distribution)  |
| GET    | `/api/feedback/low-rated`     | List low-rated tickets (1-2 stars)             |
| POST   | `/api/feedback/analyze`       | Run AI analysis for prompt improvements        |
| GET    | `/api/feedback/adjustments`   | List active/all prompt adjustments             |
| POST   | `/api/feedback/adjustments/{id}/deactivate` | Deactivate a prompt adjustment |
| POST   | `/api/ticket/{id}/jira-update` | Push triage recommendation to upstream JIRA |
| GET    | `/api/jira/issue/{key}`        | Fetch issue details from upstream JIRA       |
| POST   | `/api/jira/ingest`            | Trigger incremental JIRA ingestion (body: `{days, date, project}`) |
| GET    | `/api/jira/ingest/stats`      | Show vector DB ticket statistics              |

## LangGraph Agent Workflows

The application uses LangGraph StateGraphs for structured agentic AI workflows:

### Chat Agent

```
[User Query] → [Retrieve Tickets] → [Generate with LLM] → [Stream Response]
                    │                      │
                    ▼                      ▼
              pgvector search      Bedrock Claude
              + Bedrock Cohere     with context
              embedding
```

- **Purpose**: General-purpose Q&A about past incidents
- **Endpoint**: `POST /api/chat/stream`
- **Input**: Natural language question about past issues
- **Output**: Conversational answer referencing similar historical tickets
- **Use case**: "What similar outages have we had before?" / "How did we fix the payment timeout issue?"

### Triage Agent

```
[Problem Description] → [Retrieve Tickets] → [Analyze & Triage] → [Stream Response]
        │                       │                    │
        ▼                       ▼                    ▼
  + Component/            pgvector search      Bedrock Claude
  Technology/             + Bedrock Cohere      with triage prompt
  Environment             embedding
```

- **Purpose**: Combined triage + RCA + solution recommendation in one call
- **Endpoint**: `POST /api/triage/stream`
- **Input**: Problem description, component, technology, environment
- **Output**:
  - **Priority**: P1 (Critical) through P4 (Low)
  - **Issue Type**: Bug, Incident, Task, etc.
  - **Component**: Most appropriate team/component based on historical patterns
  - **Root Cause Category**: Configuration, Code Defect, Infrastructure, Dependency, Resource, Security, Data
  - **Probable Cause**: Specific root cause explanation
  - **Solution**: Recommended fix based on what worked for similar past tickets
  - **Business Impact**: Estimated impact assessment
  - **Resolution Time**: Estimated time to resolve
- **Use case**: When you want a full analysis in one step — triage, diagnose, and recommend

### RCA Agent (Root Cause Analysis)

```
[Problem Description] → [Retrieve Tickets] → [Deep RCA Analysis] → [Stream Response]
        │                       │                    │
        ▼                       ▼                    ▼
  + Component/            pgvector search      Bedrock Claude
  Technology/             + Bedrock Cohere      with RCA-focused prompt
  Environment             embedding
```

- **Purpose**: Root cause analysis only (no triage, no solution)
- **Endpoint**: `POST /api/rca/stream`
- **Input**: Problem description, component, technology, environment
- **Output**:
  - **Root Cause Category**: Configuration, Code Defect, Infrastructure, Dependency, Resource, Security, Data
  - **Probable Cause**: Most likely specific root cause based on historical patterns
  - **Contributing Factors**: Secondary factors that may have contributed
  - **Evidence**: References to specific similar ticket IDs that support the analysis
  - **Verification Steps**: Concrete steps to confirm the root cause
- **Use case**: When you already know the priority but need deeper diagnosis before fixing

### Solution Agent (Solution Recommendations)

```
[Problem Description] → [Retrieve Tickets] → [Solution Design] → [Stream Response]
        │                       │                    │
        ▼                       ▼                    ▼
  + Component/            pgvector search      Bedrock Claude
  Technology/             + Bedrock Cohere      with solution-focused prompt
  Environment             embedding
  + Optional Root Cause
```

- **Purpose**: Solution recommendation only (optionally takes root cause as input for more targeted advice)
- **Endpoint**: `POST /api/solution/stream`
- **Input**: Problem description, component, technology, environment, optional root cause
- **Output**:
  - **Immediate Mitigation**: Quick steps to reduce impact right now
  - **Permanent Fix**: Recommended long-term solution based on what worked for similar past tickets
  - **Implementation Steps**: Step-by-step instructions for the fix
  - **Verification**: How to verify the fix works
  - **Prevention**: How to prevent this issue from recurring
  - **Related Tickets**: References to specific past tickets where similar solutions were applied
- **Use case**: When you already have the root cause and need actionable next steps

All agents support:
- **Streaming**: Token-by-token SSE responses via `run_chat_agent_stream()`, `run_triage_agent_stream()`, `run_rca_agent_stream()`, and `run_solution_agent_stream()`
- **Non-streaming**: Direct invocation via compiled LangGraph graphs (`chat_agent`, `triage_agent`, `rca_agent`, `solution_agent`)
- **Caching**: DynamoDB-backed cache with L1 hot-key cache for identical queries
- **Guardrails**: Hallucination detection, output truncation, source validation

## Management Script

```bash
./run.sh start     # Start the server
./run.sh stop      # Stop the server
./run.sh restart   # Restart the server
./run.sh status    # Check if server is running
./run.sh ingest    # Ingest data into vector DB
./run.sh logs      # Tail the server logs
```

## CSV Format

The ingestion pipeline expects CSV files with at minimum these columns:

| Required Columns | Recommended Columns |
|------------------|---------------------|
| `ticket_id`      | `issue_description` |
| `summary`        | `issue_type`        |
|                  | `priority`          |
|                  | `component`         |
|                  | `technology`        |
|                  | `root_cause`        |
|                  | `solution`          |
|                  | `root_cause_category` |

## Ingestion Pipeline Options

```bash
python ingest_pipeline.py --help
python ingest_pipeline.py                  # Ingest all CSVs
python ingest_pipeline.py --file data.csv  # Ingest specific file
python ingest_pipeline.py --clear          # Clear DB and re-ingest
python ingest_pipeline.py --dry-run        # Validate only
python ingest_pipeline.py --validate       # Validate CSV files
python ingest_pipeline.py --stats          # Show DB statistics
```

## JIRA Daily Incremental Pipeline

Fetches yesterday's tickets from JIRA REST API and upserts them into the vector DB with Bedrock embeddings.

**Requirements**: Set `JIRA_BASE_URL`, `JIRA_API_TOKEN`, and `JIRA_USER_EMAIL` in `backend/.env`.

```bash
python jira_daily_ingest.py                    # Ingest yesterday's tickets
python jira_daily_ingest.py --days 3           # Ingest last 3 days
python jira_daily_ingest.py --date 2026-09-07  # Ingest a specific date
python jira_daily_ingest.py --project INFRA    # Filter by JIRA project
python jira_daily_ingest.py --dry-run          # Preview without ingesting
python jira_daily_ingest.py --stats            # Show DB stats
python jira_daily_ingest.py --cron             # Run as daemon (daily loop)
```

**Cron setup** (run at 6 AM daily):
```
0 6 * * * cd /path/to/019_AWS_JIRA_Ticket_Solution && python jira_daily_ingest.py >> logs/ingest.log 2>&1
```

**How it works**:
1. Builds JQL: `created >= "YYYY-MM-DD" AND created <= "YYYY-MM-DD"`
2. Fetches issues via JIRA REST API v2 (paginated, handles 100/page limit)
3. Maps JIRA fields to DB schema (standard fields + custom fields `customfield_10001-10013`)
4. Generates Bedrock Cohere embeddings for each ticket
5. Upserts into `jira_tickets` table (skips existing ticket IDs)
6. Supports `--cron` mode: runs once, sleeps until 6 AM, repeats

**API endpoints**:
- `POST /api/jira/ingest` — Trigger ingestion from UI (body: `{days, date, project}`)
- `GET /api/jira/ingest/stats` — Show vector DB ticket statistics

## Code Walkthrough

### Module Dependency Graph

```
main.py
  ├── database.py ──────────► dynamodb.py (sessions, messages, triage)
  │     └── config.py             └── config.py
  │     └── metrics.py            └── metrics.py
  │
  ├── retriever.py ─────────► bedrock_client.py ──► dynamodb_cache.py
  │     └── database.py            └── config.py        └── config.py
  │     └── bedrock_client.py      └── metrics.py       └── metrics.py
  │     └── metrics.py
  │
  ├── langgraph_agent.py ───► retriever.py
  │     └── bedrock_client.py      └── guardrails.py
  │     └── guardrails.py          └── metrics.py
  │     └── config.py
  │     └── feedback_analytics.py ──► dynamodb.py (feedback, prompt_improvements)
  │
  └── guardrails.py ────────► dynamodb_guardrails.py
        └── metrics.py              └── config.py
                                     └── metrics.py

jira_daily_ingest.py (standalone CLI / cron daemon)
  ├── config.py (JIRA_BASE_URL, JIRA_API_TOKEN, JIRA_USER_EMAIL)
  ├── database.py ──► psycopg2 + pgvector → RDS PostgreSQL
  └── bedrock_client.py ──► Bedrock Cohere embeddings
```

### Entry Point: `main.py`

The FastAPI application is created here. On startup (`@app.on_event("startup")`), four initialization functions run in sequence:

1. `init_db()` — Creates the `jira_tickets` table in RDS PostgreSQL with pgvector `vector(1024)` column
2. `init_dynamodb_tables()` — Creates 3 DynamoDB tables: sessions, messages, triage tickets
3. `init_cache_table()` + `init_guardrails_tables()` — Creates 3 more DynamoDB tables: cache, rate limits, circuit breaker
4. `init_feedback_tables()` — Creates 2 DynamoDB tables: feedback, prompt_improvements

The middleware at line 45 intercepts every request to record HTTP metrics (count, latency, errors) before it reaches any endpoint.

### Request Lifecycle: Chat Stream

When a user sends a message, here's exactly what happens:

```
POST /api/chat/stream
  │
  ├─ validate_chat_request()          [guardrails.py:375]
  │    ├─ rate_limiter.is_allowed()   [dynamodb_guardrails.py] — checks DynamoDB for IP
  │    ├─ check_prompt_injection()    [guardrails.py:196] — 35+ regex patterns
  │    ├─ sanitize_input()            [guardrails.py:219] — strip control chars
  │    ├─ validate_input()            [guardrails.py:187] — length check (2000 max)
  │    └─ check_circuit_breaker()     [dynamodb_guardrails.py] — shared state check
  │
  ├─ save_message(session_id, "user") [dynamodb.py] — write to DynamoDB immediately
  │
  ├─ run_chat_agent_stream()          [langgraph_agent.py]
  │    │
  │    ├─ search_similar_tickets()    [retriever.py:7]
  │    │    ├─ get_embedding(query)   [bedrock_client.py:88]
  │    │    │    ├─ Check DynamoDB cache (L1 local → DynamoDB GetItem)
  │    │    │    └─ On miss: Bedrock Cohere API → store in DynamoDB cache
  │    │    │
  │    │    └─ pgvector SQL query     [retriever.py:14]
  │    │         └─ SELECT ... ORDER BY embedding <=> query::vector LIMIT 5
  │    │
  │    ├─ validate_sources()          [guardrails.py:231] — filter below 50% similarity
  │    │
  │    ├─ Check chat cache            [bedrock_client.py:297]
  │    │    └─ DynamoDB cache get (L1 → DynamoDB)
  │    │
  │    ├─ Bedrock Claude streaming    [bedrock_client.py:162]
  │    │    └─ invoke_model_with_response_stream()
  │    │
  │    ├─ Yield SSE events:
  │    │    ├─ event: init  ──→  {sources, embedding_ms}
  │    │    ├─ event: token ──→  {content: "token..."}
  │    │    └─ event: done  ──→  {total_ms, tokens, ...}
  │    │
  │    ├─ check_hallucination()       [guardrails.py:243] — verify ticket IDs exist
  │    ├─ truncate_response()         [guardrails.py:254] — cap at 2048 tokens
  │    │
  │    ├─ cache_chat_result()         [bedrock_client.py:280] — store in DynamoDB cache
  │    │
  │    └─ save_message(session_id, "assistant")  [dynamodb.py] — persist full response
  │
  └─ StreamingResponse(stream_gen(), media_type="text/event-stream")
```

### Request Lifecycle: Triage Stream

Same pattern as chat, but with a feedback-aware triage-specific system prompt:

```
POST /api/triage/stream
  │
  ├─ validate_chat_request()
  │
  ├─ run_triage_agent_stream()        [langgraph_agent.py:283]
  │    │
  │    ├─ search_similar_tickets(query, top_k=3)
  │    │
  │    ├─ build_feedback_aware_triage_prompt()  [feedback_analytics.py]
  │    │    └─ Appends active prompt adjustments to base triage prompt
  │    │
  │    ├─ Bedrock Claude with feedback-aware triage prompt:
  │    │    "1. TRIAGE: Propose priority (P1-P4)..."
  │    │    "2. ROOT CAUSE ANALYSIS..."
  │    │    "3. SOLUTION..."
  │    │    "4. IMPACT ASSESSMENT..."
  │    │    + any active prompt adjustments from feedback loop
  │    │
  │    └─ Stream SSE events
  │
  └─ StreamingResponse
```

### Request Lifecycle: RCA Stream

Focused exclusively on root cause analysis:

```
POST /api/rca/stream
  │
  ├─ validate_chat_request()
  │
  ├─ run_rca_agent_stream()           [langgraph_agent.py]
  │    │
  │    ├─ search_similar_tickets(query, top_k=5)
  │    │
  │    ├─ Bedrock Claude with RCA-focused prompt:
  │    │    "1. ROOT CAUSE CATEGORY..."
  │    │    "2. PROBABLE CAUSE..."
  │    │    "3. CONTRIBUTING FACTORS..."
  │    │    "4. EVIDENCE..."
  │    │    "5. VERIFICATION STEPS..."
  │    │
  │    └─ Stream SSE events
  │
  └─ StreamingResponse
```

### Request Lifecycle: Solution Stream

Focused exclusively on solution recommendations, with optional root cause context:

```
POST /api/solution/stream
  │
  ├─ validate_chat_request()
  │
  ├─ run_solution_agent_stream()      [langgraph_agent.py]
  │    │
  │    ├─ search_similar_tickets(query, top_k=5)
  │    │
  │    ├─ Bedrock Claude with solution-focused prompt:
  │    │    "1. IMMEDIATE MITIGATION..."
  │    │    "2. PERMANENT FIX..."
  │    │    "3. IMPLEMENTATION STEPS..."
  │    │    "4. VERIFICATION..."
  │    │    "5. PREVENTION..."
  │    │    "6. RELATED TICKETS..."
  │    │
  │    └─ Stream SSE events
  │
  └─ StreamingResponse
```

### Data Ingestion: `ingest_pipeline.py`

```
python ingest_pipeline.py
  │
  ├─ init_db()                     — ensure jira_tickets table exists
  │
  ├─ For each CSV in dataset/:
  │    │
  │    ├─ pd.read_csv()            — load into DataFrame
  │    │
  │    ├─ For each row:
  │    │    ├─ Check duplicate      — SELECT id WHERE ticket_id = X
  │    │    ├─ build_ticket_text()  — concatenate all fields into rich text
  │    │    ├─ get_embedding()      — Bedrock Cohere API (with cache)
  │    │    └─ INSERT INTO jira_tickets ... embedding = '[0.123,...]'::vector
  │    │
  │    └─ Print progress: [1/200] JIRA-001
  │
  └─ Summary: 200 ingested, 0 skipped, 0 errors
```

### JIRA Daily Incremental Ingestion: `jira_daily_ingest.py`

```
python jira_daily_ingest.py
  │
  ├─ get_date_range()                — yesterday's date (or --days N / --date YYYY-MM-DD)
  │
  ├─ build_jql()                     — 'created >= "2026-09-07" AND created <= "2026-09-07"'
  │
  ├─ fetch_jira_issues()             — JIRA REST API v2 GET /rest/api/2/search
  │    └─ Paginated (100/page), auto-follows startAt until all fetched
  │
  ├─ jira_response_to_row()          — Map JIRA fields to DB columns
  │    ├─ Standard: summary, description, status, priority, components, labels
  │    └─ Custom fields: customfield_10001-10013 (technology, root_cause, etc.)
  │
  ├─ ingest_tickets()                — For each row:
  │    ├─ Check duplicate            — SELECT id WHERE ticket_id = X
  │    ├─ build_ticket_text()        — concatenate fields into rich text
  │    ├─ get_embedding()            — Bedrock Cohere API (with cache)
  │    └─ INSERT INTO jira_tickets ... embedding = '[0.123,...]'::vector
  │
  └─ Summary: 15 ingested, 3 skipped (duplicates), 0 errors
```

### Key Design Decisions

**Why two-tier caching (L1 local + DynamoDB)?**

DynamoDB GetItem takes ~5ms. In-memory dict takes ~0.001ms. By keeping the 100 most-accessed items in memory, hot keys are served instantly while cold keys still benefit from persistent caching across restarts and instances.

```
Request → L1 dict (0.001ms) → DynamoDB (5ms) → Bedrock API (500-2000ms)
           ↑ hit rate ~80%     ↑ hit rate ~15%    ↑ miss rate ~5%
```

**Why DynamoDB for guardrails instead of in-memory?**

With 200 concurrent users across multiple Uvicorn workers, each worker has its own rate limiter. User A hitting worker 1 gets 30 requests, then hitting worker 2 gets another 30. DynamoDB makes the limit truly global.

**Why streaming SSE instead of WebSocket?**

SSE is simpler (unidirectional, auto-reconnect, works through proxies), and the LLM generates tokens one direction. No need for bidirectional communication.

**Why pgvector instead of Amazon OpenSearch / Pinecone?**

For 200 tickets, pgvector is sufficient and avoids an additional AWS service. The `ORDER BY embedding <=> query::vector` query with an IVFFlat or HNN index handles this scale easily. At 100k+ tickets, consider Amazon OpenSearch with k-NN.

### File-by-File Reference

| File | Lines | Purpose |
|------|-------|---------|
| `main.py` | ~750 | FastAPI app, all API endpoints, middleware, startup |
| `bedrock_client.py` | ~290 | Bedrock Claude + Cohere client, streaming, caching |
| `dynamodb.py` | ~280 | DynamoDB CRUD for sessions, messages, triage tickets |
| `dynamodb_cache.py` | ~220 | DynamoDB-backed cache with L1 hot-key cache |
| `dynamodb_guardrails.py` | ~260 | DynamoDB-backed rate limiter + circuit breaker |
| `langgraph_agent.py` | ~600 | LangGraph StateGraphs + async streaming agents (chat, triage, RCA, solution) |
| `guardrails.py` | ~300 | Input/output validation, jailbreak detection |
| `feedback_analytics.py` | ~210 | Feedback recording, analytics, prompt improvement engine |
| `retriever.py` | ~65 | pgvector similarity search, ticket formatting |
| `database.py` | ~85 | RDS PostgreSQL init, re-exports DynamoDB functions |
| `metrics.py` | ~370 | Prometheus-compatible metrics collector |
| `config.py` | ~40 | Environment variable loader (including AgentCore config) |
| `agentcore_app.py` | ~150 | AgentCore Runtime entrypoint for managed deployment |
| `agentcore_tools.py` | ~120 | MCP tool definitions for AgentCore Gateway |
| `agentcore_memory.py` | ~200 | AgentCore Memory integration with DynamoDB fallback |
| `ingest_pipeline.py` | ~280 | Batch CSV ingestion with CLI |
| `jira_daily_ingest.py` | ~340 | JIRA incremental daily ingestion (REST API → vector DB) |

### DynamoDB Table Schemas

**`jira_triage_cache`**
```
{
  "cache_key": "a1b2c3d4...",        // SHA256 hash (String, Partition Key)
  "category": "embedding",            // "embedding" or "chat" (String, GSI)
  "value": "{\"embedding\":[...]}...",// JSON-serialized value (String)
  "ttl": 1725700000,                  // Expiry epoch (Number, TTL enabled)
  "created_at": 1725613600,           // Creation time (Number)
  "accessed_at": 1725614000           // Last access time (Number)
}
```

**`jira_triage_rate_limits`**
```
{
  "client_ip": "192.168.1.100",      // Client IP (String, Partition Key)
  "timestamps": ["1725613600.123",...],// Request timestamps (List of Strings)
  "ttl": 1725613720                   // Expiry epoch (Number, TTL enabled)
}
```

**`jira_triage_circuit_breaker`**
```
{
  "service_name": "bedrock",          // Service name (String, Partition Key)
  "failures": 2,                      // Consecutive failure count (Number)
  "state": "closed",                  // "closed" | "open" | "half_open" (String)
  "last_failure_time": "1725613600"   // Epoch of last failure (String)
}
```

## Observability Metrics

The application includes a built-in metrics system with Prometheus-compatible export and a web dashboard.

### Dashboard

Open [http://localhost:8000/#!/metrics](http://localhost:8000/#!/metrics) to view the Metrics tab with auto-refresh support.

### Prometheus Endpoint

Scrape `GET /metrics` (text/plain) with Datadog Agent, Prometheus, or Grafana:

```
http://localhost:8000/metrics
```

### Metrics Collected

| Category | Metric | Type | Labels |
|----------|--------|------|--------|
| **HTTP** | `http_requests_total` | counter | method, endpoint, status |
| | `http_request_duration_seconds` | histogram | method, endpoint |
| | `http_request_errors_total` | counter | endpoint, error_type |
| **LLM** | `llm_duration_seconds` | histogram | operation, model |
| | `llm_tokens_total` | counter | model, type (prompt/completion) |
| | `llm_requests_total` | counter | operation, model |
| **Embedding** | `embedding_duration_seconds` | histogram | model |
| | `embedding_requests_total` | counter | model |
| **Database** | `db_query_duration_seconds` | histogram | operation |
| | `db_query_total` | counter | operation, status |
| **Vector Search** | `vector_search_duration_seconds` | histogram | — |
| **Cache** | `cache_operations_total` | counter | operation (hit/miss/evict) |
| | `cache_size` | gauge | cache (embedding/chat) |
| | `cache_hit_rate` | gauge | — |
| **Circuit Breaker** | `circuit_breaker_state` | gauge | — (0=closed, 1=open, 2=half_open) |
| | `circuit_breaker_events_total` | counter | transition |
| **Rate Limiter** | `rate_limit_allowed_total` | counter | — |
| | `rate_limit_rejected_total` | counter | — |
| **Guardrails** | `guardrail_blocked_total` | counter | reason |
| | `guardrail_duration_seconds` | histogram | — |
| **App** | `active_sessions` | gauge | — |
| | `total_tickets` | gauge | — |
| | `uptime_seconds` | gauge | — |
| **AgentCore Runtime** | `agentcore_runtime_invocations_total` | counter | agent_type |
| | `agentcore_runtime_errors_total` | counter | agent_type, error_type |
| | `agentcore_runtime_duration_seconds` | histogram | agent_type |
| | `agentcore_runtime_sessions_active` | gauge | — |
| **AgentCore Memory** | `agentcore_memory_reads_total` | counter | memory_type |
| | `agentcore_memory_writes_total` | counter | memory_type |
| | `agentcore_memory_errors_total` | counter | memory_type, error_type |
| | `agentcore_memory_latency_seconds` | histogram | operation |
| **AgentCore Gateway** | `agentcore_gateway_requests_total` | counter | tool_name |
| | `agentcore_gateway_errors_total` | counter | tool_name, error_type |
| | `agentcore_gateway_duration_seconds` | histogram | tool_name |
| **AgentCore Identity** | `agentcore_identity_auth_success_total` | counter | — |
| | `agentcore_identity_auth_failure_total` | counter | — |

## AWS Cost Considerations

| Service | Billing Model | Estimated Cost |
|---------|--------------|----------------|
| **RDS PostgreSQL** | Instance hours + storage | ~$0.035/hr (db.t3.medium) + $0.115/GB |
| **DynamoDB** | PAY_PER_REQUEST | ~$1.25/M writes, $0.25/M reads |
| **Bedrock Claude** | Per token | ~$3/M input tokens, $15/M output tokens |
| **Bedrock Cohere** | Per token | ~$0.10 per 1M tokens |

At 200 concurrent users, estimated DynamoDB cost: **$0.50–2.00/month** (cache + sessions + guardrails combined).

## Datadog Integration

Point your Datadog Agent's Prometheus integration at the `/metrics` endpoint:

```yaml
prometheus:
  endpoint: http://localhost:8000/metrics
  namespace: jira_agent
```

## Migration from Local Version

If migrating from the local Ollama-based version:

1. **Data**: Run `python ingest_pipeline.py --clear` then re-ingest to generate Bedrock embeddings (dimension changed from 768 to 1024)
2. **Sessions**: Local PostgreSQL chat sessions are not migrated. DynamoDB tables are created fresh on startup
3. **Config**: Replace `OLLAMA_*` env vars with `AWS_*` and `BEDROCK_*` vars in `backend/.env`
4. **Dependencies**: Run `pip install -r backend/requirements.txt` to install boto3, langgraph, and langchain packages

## Next Steps / Further Enhancements

### 1. SSO Integration

Integrate enterprise single sign-on for secure, auditable access.

**Implementation:**
- **AWS Cognito** as the identity provider with SAML 2.0 / OIDC federation
- Add `aws-cognito-sdk` and `python-jose` (JWT validation) to dependencies
- FastAPI middleware to validate JWT tokens on every request
- AngularJS login page redirecting to Cognito hosted UI
- Role-based access control (RBAC): Admin, Engineer, Viewer roles stored in Cognito custom attributes

**Architecture:**
```
Browser → Cognito Hosted UI → JWT Token → FastAPI Middleware → API Endpoints
                                    │
                                    ▼
                         Verify signature + expiry
                         Extract user_id, role
                         Attach to request.state
```

**DynamoDB additions:**
- `jira_triage_users` table — user profiles, role mappings, last login timestamps
- Add `user_id` field to `jira_triage_messages` and `jira_triage_tickets` for per-user scoping

**Benefits:**
- No more shared access — each engineer has their own session history
- Audit trail: who triaged what, who accessed which tickets
- Integration with corporate Active Directory via SAML federation

---

### 2. Live Log File Analysis

Add a new agent that ingests live application logs and correlates them with JIRA tickets for proactive incident detection.

**Implementation:**
- **Amazon CloudWatch Logs** as the log source (or S3 for historical logs)
- **Amazon OpenSearch** for full-text log search and aggregation
- New LangGraph agent: `log_analysis_agent` that accepts log patterns/queries
- New API endpoint: `POST /api/logs/analyze` — describe a log pattern, get correlated JIRA tickets
- New AngularJS view: `#!/logs` — log search interface with syntax highlighting

**Architecture:**
```
CloudWatch Logs ──► Log Ingestion Lambda ──► OpenSearch Index
                                                  │
                                        ┌─────────▼──────────┐
                                        │ log_analysis_agent │
                                        │ (LangGraph)        │
                                        └─────────┬──────────┘
                                                  │
                              ┌───────────────────┼───────────────────┐
                              ▼                   ▼                   ▼
                     Embed log pattern    Search OpenSearch    Search pgvector
                     via Bedrock Cohere   for similar logs     for related JIRA
                              │                   │                   │
                              └───────────────────┼───────────────────┘
                                                  ▼
                                        Bedrock Claude generates
                                        correlation report + RCA
```

**LangGraph Agent State:**
```python
class LogAnalysisState(TypedDict):
    log_pattern: str           # User's log query or error pattern
    time_range: str            # "1h", "24h", "7d"
    service_filter: str        # Optional service name filter
    opensearch_results: list   # Matching log entries from OpenSearch
    similar_logs: list         # Log clusters with same signature
    related_tickets: list      # JIRA tickets with matching root cause
    analysis: str              # LLM-generated correlation report
```

**Use cases:**
- "Show me all NullPointerExceptions in the last 24h and which JIRA tickets are related"
- "Find all OOM errors and which past incidents had similar patterns"
- "Analyze this stack trace and recommend which team should investigate"

---

### 3. Change Management Integration

Add a change management workflow that links triage tickets to deployment changes, enabling impact analysis and rollback decisions.

**Implementation:**
- **AWS CodePipeline** or **GitHub Actions** integration via webhooks
- New DynamoDB table: `jira_triage_changes` — deployment records linked to triage tickets
- New API endpoints:
  - `POST /api/changes` — record a deployment/change
  - `GET /api/changes` — list changes with filters (service, date range, status)
  - `GET /api/changes/{id}` — get change details with linked triage tickets
  - `POST /api/changes/{id}/rollback` — trigger rollback, update status
- New AngularJS view: `#!/changes` — change timeline with deployment status indicators
- LangGraph agent extension: when triage is performed, automatically check recent changes for the affected service

**Architecture:**
```
GitHub/CodePipeline Webhook
        │
        ▼
POST /api/changes
        │
        ├── Store in jira_triage_changes (DynamoDB)
        │
        └── Trigger LangGraph correlation:
             │
             ├─ Search recent triage tickets for affected service
             ├─ Check if any open incidents correlate with this change
             └─ If correlation found → notify via SNS/Slack
```

**DynamoDB table: `jira_triage_changes`**
```
{
  "id": "CHG-0001",
  "service": "payment-gateway",
  "environment": "production",
  "change_type": "deployment",          // deployment | config | schema | dependency
  "version": "v2.3.1",
  "status": "completed",                // pending | in_progress | completed | rolled_back
  "author": "user@example.com",
  "commit_sha": "abc123...",
  "linked_triage_tickets": ["TRG-0042"], // Auto-linked by agent
  "rollback_available": true,
  "created_at": "2026-09-06T10:30:00Z",
  "completed_at": "2026-09-06T10:45:00Z"
}
```

**Triage integration:**
When a triage ticket is created for a service, the agent automatically:
1. Queries `jira_triage_changes` for recent deployments to that service
2. If a deployment happened within the incident window → flags it as potential root cause
3. Adds the deployment info to the triage analysis
4. Suggests rollback as a mitigation step

**Benefits:**
- End-to-end visibility: incident → root cause → deployment that caused it
- Automated rollback recommendations
- Change freeze enforcement (block deployments during active P1 incidents)
- Post-incident change audit trail

---

### 4. Additional Enhancements

| Enhancement | Description | AWS Services |
|-------------|-------------|--------------|
| **Slack/Teams Integration** | Bot that receives triage alerts and allows slash commands for quick triage | SNS, Lambda, API Gateway |
| **Automated Ticket Creation** | Auto-create JIRA tickets from triage analysis via JIRA REST API | Lambda, Step Functions |
| **Multi-region Deployment** | Deploy across us-east-1 and us-west-2 for DR | Route 53, RDS Read Replicas |
| **Cost Dashboard** | Track Bedrock token usage and DynamoDB costs per team | CloudWatch, Cost Explorer |
| **A/B Testing** | Compare Claude vs other Bedrock models for triage quality | Bedrock Model Gateway |
| **Feedback Loop** | Use triage feedback ratings to fine-tune prompts | DynamoDB, Bedrock |

## Feedback Loop & Prompt Improvement

The application includes a continuous feedback loop that uses star ratings on triage tickets to improve the triage system prompt over time. This creates a self-improving system where the AI learns from human feedback to produce better analyses.

### How It Works

#### Step 1: User Rates a Triage Ticket

After reviewing a triage analysis, the user can rate it 1-5 stars and optionally leave a comment. This happens on the Tickets page (`#!/tickets`) via the feedback form at the bottom of each ticket detail view.

```
User clicks star rating → POST /api/tickets/{id}/feedback
                                      │
                                      ▼
                        save_triage_feedback() stores in DynamoDB
                                      │
                                      ▼
                        record_feedback() stores in jira_triage_feedback
                        with full ticket context (description, analysis,
                        component, technology)
```

The feedback record captures not just the rating, but the full context of what the AI analyzed — the problem description, the component/technology involved, and the analysis it produced. This context is critical for the AI to understand *what went wrong* when it analyzes patterns later.

#### Step 2: AI Analyzes Feedback Patterns

When an admin triggers analysis via the Feedback Analytics dashboard (`#!/feedback`) or the API (`POST /api/feedback/analyze`), the system:

1. **Gathers statistics**: Total feedback count, average rating, rating distribution, per-component and per-technology breakdowns
2. **Identifies low-rated tickets**: Tickets rated 1-2 stars with their comments and analysis snippets
3. **Sends to Bedrock Claude**: A special analysis prompt asks Claude to identify patterns in the feedback data and generate specific, actionable prompt improvements

```python
# The analysis prompt sent to Bedrock Claude:
"""
You are an expert at improving AI triage system prompts based on user feedback.

Analyze the feedback data below and generate up to 3 specific, actionable prompt
improvements. For each improvement, provide:
1. A short description of the issue
2. The exact text to add to the triage system prompt
3. The type: 'priority_clarification', 'root_cause_focus', 'solution_quality', or 'context_handling'

Focus on:
- Low-rated tickets (1-2 stars) — what went wrong in the analysis
- Common patterns in negative feedback comments
- Components or technologies with consistently low ratings
"""
```

#### Step 3: Prompt Improvements Are Generated

Claude analyzes the feedback patterns and generates improvement suggestions. For example:

- "Low-rated tickets often involve database connection issues but the analysis focuses on application code. Add emphasis on checking infrastructure dependencies first."
- "When the component is 'payment-gateway', the triage often misses P1 priority. Add: 'Payment-related issues should default to P1 unless clearly non-critical.'"
- "Analysis quality drops when the description is under 50 words. Add: 'For brief descriptions, ask clarifying questions before triaging.'"

These are stored as prompt adjustments in `jira_triage_prompt_improvements` with an `active` flag.

#### Step 4: Triage Prompt Is Enhanced

On every subsequent triage request, `build_feedback_aware_triage_prompt()` is called. It:

1. Starts with the base triage system prompt
2. Queries `jira_triage_prompt_improvements` for all active adjustments
3. Appends each active adjustment's `prompt_addition` text to the prompt
4. Returns the enhanced prompt to the LLM

```python
# feedback_analytics.py
def build_feedback_aware_triage_prompt(base_prompt: str) -> str:
    adjustments = get_current_prompt_adjustments()
    if not adjustments:
        return base_prompt

    enhancement = "\n\n### ADDITIONAL TRAINING guidelines (from feedback analysis):\n"
    for adj in adjustments:
        enhancement += f"\n- {adj['description']}\n  {adj['prompt_addition']}\n"

    return base_prompt + enhancement
```

#### Step 5: Better Analysis → Higher Ratings

The next time a user triages a ticket, the enhanced prompt produces better analysis because it includes the lessons learned from previous feedback. This creates a virtuous cycle:

```
Feedback (1-2 stars) → AI Analysis → Prompt Improvement → Better Triage → Higher Ratings
        ↑                                                                    │
        └────────────────────────────────────────────────────────────────────┘
```

### Complete Data Flow Diagram

```
┌─────────────────────────────────────────────────────────────────────────┐
│                        FEEDBACK LOOP DATA FLOW                          │
└─────────────────────────────────────────────────────────────────────────┘

  USER                    BACKEND                    DynamoDB
  ────                    ───────                    ───────
    │                         │                          │
    │  1. Rate ticket         │                          │
    │  (1-5 stars + comment)  │                          │
    │──────────────────────▶  │                          │
    │                         │                          │
    │                    save_triage_feedback()          │
    │                    record_feedback()               │
    │                         │ ────────────────────────▶│
    │                         │                          │
    │                         │                          │  jira_triage_feedback
    │                         │                          │  (rating, comment,
    │                         │                          │   component, technology,
    │                         │                          │   description, analysis)
    │                         │                          │
  ADMIN                       │                          │
    │  2. Trigger analysis    │                          │
    │  POST /api/feedback/    │                          │
    │       analyze           │                          │
    │───────────────────────▶ │                          │
    │                         │                          │
    │                    get_feedback_stats()            │
    │                    get_low_rated_tickets()         │
    │                         │ ────────────────────────▶│
    │                         │ ◀────────────────────────│
    │                         │                          │
    │                    Send to Bedrock Claude:         │
    │                    "Analyze these low-rated        │
    │                     tickets and suggest prompt     │
    │                     improvements"                  │
    │                         │                          │
    │                         │    ┌──────────────┐      │
    │                         │──▶ │ Bedrock      │      │
    │                         │    │ Claude       │      │
    │                         │◀── │ (analysis)   │      │
    │                         │    └──────────────┘      │
    │                         │                          │
    │                    save_prompt_adjustment()        │
    │                         │ ────────────────────────▶│
    │                         │                          │
    │                         │                          │  jira_triage_prompt_
    │                         │                          │  improvements
    │                         │                          │  (adjustment_id,
    │                         │                          │   prompt_addition,
    │                         │                          │   active: true)
    │                         │                          │
  ENGINEER                    │                          │
    │  3. Triage new ticket   │                          │
    │  POST /api/triage/      │                          │
    │       stream            │                          │
    │───────────────────────▶ │                          │
    │                         │                          │
    │                    build_feedback_aware_           │
    │                    triage_prompt()                 │
    │                         │─────────────────────────▶│
    │                         │◀─────────────────────────│
    │                         │                          │
    │                         │   Base prompt            │
    │                         │   + active adjustments   │
    │                         │                          │
    │                    run_triage_agent_stream()       │
    │                         │    ┌──────────────┐      │
    │                         │──▶ │ Bedrock      │      │
    │                         │    │ Claude       │      │
    │                         │◀── │ (enhanced)   │      │
    │                         │    └──────────────┘      │
    │                         │                          │
    │  4. Better analysis!    │                          │
    │◀──────────────────────  │                          │
    │                         │                          │
    │  5. Rate higher (4-5)   │                          │
    │──────────────────────▶  │                          │
    │                         │ ────────────────────────▶│
```

### Feedback API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/feedback/stats` | Overall feedback statistics (avg rating, distribution, per-component/technology) |
| GET | `/api/feedback/low-rated` | List tickets rated 1-2 stars with comments |
| POST | `/api/feedback/analyze` | Trigger AI analysis to generate prompt improvements |
| GET | `/api/feedback/adjustments` | List active and all historical prompt adjustments |
| POST | `/api/feedback/adjustments/{id}/deactivate` | Deactivate a specific prompt adjustment |

### DynamoDB Tables

**`jira_triage_feedback`**
```
{
  "ticket_ref": "TRG-0042",              // Ticket reference (String, Partition Key)
  "rating": 2,                            // Star rating 1-5 (Number)
  "comment": "Analysis missed root cause",// User comment (String)
  "component": "payment-gateway",         // Ticket component (String)
  "technology": "Java",                   // Ticket technology (String)
  "description": "...",                   // Ticket description (String)
  "analysis_snippet": "...",             // First 500 chars of analysis (String)
  "created_at": "2026-09-06T10:30:00Z"   // Creation timestamp (String)
}
```

**`jira_triage_prompt_improvements`**
```
{
  "adjustment_id": "adj_a1b2c3",         // UUID (String, Partition Key)
  "adjustment_type": "root_cause_focus", // Type category (String)
  "description": "Add emphasis on...",   // Human-readable description (String)
  "prompt_addition": "When analyzing...",// Text appended to triage prompt (String)
  "triggered_by": "feedback_analysis_15_entries", // What triggered it (String)
  "active": true,                        // Whether currently applied (Boolean)
  "applied_count": 0,                    // Times applied to triage (Number)
  "created_at": "2026-09-06T10:30:00Z", // Creation timestamp (String)
  "deactivated_at": null                 // Deactivation timestamp (String, nullable)
}
```

### Prompt Adjustment Types

| Type | Description |
|------|-------------|
| `priority_clarification` | Clarifies when to assign P1 vs P2 vs P3 |
| `root_cause_focus` | Adds emphasis on specific root cause patterns |
| `solution_quality` | Improves solution recommendation specificity |
| `context_handling` | Better handling of limited-context tickets |

### Frontend Dashboard

Access the Feedback Analytics dashboard at `#!/feedback` to:
- View rating distribution bar chart
- See per-component and per-technology breakdowns
- Review low-rated tickets with comments
- Run AI analysis to generate new prompt improvements
- Manage active prompt adjustments (activate/deactivate)

### Key Design Decisions

**Why prompt engineering instead of fine-tuning?**

Fine-tuning a model requires thousands of examples, costs $hundreds, and takes hours. Prompt engineering with feedback-driven adjustments achieves 80% of the benefit at 1% of the cost. The adjustments are immediately visible, reversible, and auditable.

**Why require minimum 3 feedback entries before analysis?**

With fewer than 3 ratings, there's not enough signal to identify patterns. The AI might overfit to a single outlier. Requiring a minimum ensures the analysis is based on actual trends.

**Why store adjustments in DynamoDB instead of a config file?**

DynamoDB allows:
- Runtime activation/deactivation without restart
- Audit trail of all adjustments (active and inactive)
- Multiple instances share the same adjustments
- UI management via the Feedback Analytics dashboard

**Why deactivate instead of delete?**

Deactivation preserves the history of what was tried. If a prompt adjustment seemed helpful but later caused issues, you can see the full timeline. The `applied_count` field tracks how many triages used each adjustment.

**Why analyze on-demand instead of automatically?**

Automatic analysis after every feedback would be expensive (Bedrock API calls) and might generate noise. On-demand analysis lets admins:
- Wait for sufficient feedback to accumulate
- Review and approve adjustments before activation
- Time analysis for sprint retrospectives or improvement cycles

---

## License

MIT License

Copyright (c) 2026 Pravin Pardeshi

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

=======
>>>>>>> a7d33327fb30ae4df5bc14ce1966c6d1ee7578ee
