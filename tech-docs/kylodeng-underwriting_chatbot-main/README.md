# Underwriting Chatbot

## 1. Project Overview

An AI-powered underwriting assistant that helps insurance underwriters assess customer risk profiles through a conversational chat interface. The system uses a multi-agent LLM architecture to run parallel specialist assessments across domains (finance, health, life, etc.) and aggregates them into a structured underwriting report with a risk classification. A suite of five GitHub Actions CI/CD workflows provides automated code review, documentation generation, test generation, and UAT facilitation powered by Anthropic Claude.

---

## 2. Tech Stack

| Component | Technology | Version/Notes |
|---|---|---|
| Backend framework | FastAPI | Python, with SSE streaming |
| Agent orchestration | LangGraph | `StateGraph`-based agent loop |
| LLM – default (fast) | Anthropic Claude Haiku | `claude-haiku-4-5-20251001` |
| LLM – full assessment | Anthropic Claude Sonnet | `claude-sonnet-4-20250514` |
| LLM – alternative | Google Gemini | `gemini-3-flash-preview` |
| Frontend | Chainlit | Served on port 8080 |
| Session memory / checkpointing | Redis (LangGraph Redis Saver) | `redis/redis-stack-server:7.2.0-v14` |
| Frontend persistence DB | PostgreSQL | `postgres:16-alpine` |
| Customer data | SQLite databases | Mounted as read-only volumes |
| Customer similarity index | JSON file | `backend/tmp/customer_similarity_dict.json` |
| Risk model card | CatBoostClassifier (metadata only) | `backend/model_card.json` v1.0 |
| CI/CD AI tooling | Anthropic Claude Sonnet | `claude-sonnet-4-6` via GitHub Actions |
| Container orchestration | Docker Compose | Local development |
| Environment config | python-dotenv | `.env` file at repo root |

---

## 3. Architecture

```
┌────────────┐     HTTP/SSE      ┌──────────────────────────────────────────┐
│  Frontend  │ ◄────────────────► │             Backend (FastAPI)            │
│ (Chainlit) │  POST /chat        │                                          │
│  port 8080 │  GET  /health      │  ┌────────────────────────────────────┐  │
└────────────┘                   │  │  Agent (LangGraph StateGraph)      │  │
       │                         │  │                                    │  │
       │ DB writes               │  │  agent_with_skills.py              │  │
       ▼                         │  │  ┌──────────┐  ┌────────────────┐ │  │
┌────────────┐                   │  │  │ agent LLM│  │ tool executor  │ │  │
│ PostgreSQL │                   │  │  │ (Haiku)  │  │                │ │  │
│  port 5432 │                   │  │  └──────────┘  └────────────────┘ │  │
└────────────┘                   │  │       │Tools:                      │  │
                                 │  │  • get_customer_profile            │  │
┌────────────┐  checkpoint r/w   │  │  • customer_lookalike              │  │
│   Redis    │ ◄─────────────────│  │  • run_underwriting_assessment     │  │
│  port 6379 │                   │  └──────────────┬───────────────────-┘  │
└────────────┘                   │                 │                        │
                                 │  ┌──────────────▼────────────────────┐  │
┌────────────────────┐  read-only│  │    Assessment Engine              │  │
│ SQLite databases   │ ◄─────────│  │  (assessment.py)                  │  │
│ • customer_profile │  volumes  │  │                                   │  │
│ • feature_import.  │           │  │  Parallel specialist LLM calls    │  │
│ • model_prediction │           │  │  (semaphore=4) → aggregator LLM   │  │
│ • application_prof.│           │  │  → structured UnderwritingReport  │  │
└────────────────────┘           │  └───────────────────────────────────┘  │
                                 └──────────────────────────────────────────┘
```

**Data flow summary:**
1. The user sends a message via the Chainlit frontend to `POST /chat`.
2. The FastAPI backend streams Server-Sent Events (SSE) back to the frontend.
3. `build_agent()` constructs a LangGraph agent with Redis-backed checkpointing (per `session_id`/`thread_id`) for conversation memory.
4. The agent LLM decides which tool(s) to call; tools are executed one at a time.
5. `run_underwriting_assessment` fans out to N specialist LLM calls in parallel (capped at 4 concurrent), then an aggregator LLM produces a structured `UnderwritingReport` Pydantic model.
6. The final answer is streamed back token-by-token via SSE events (`response` event type); tool activity is signalled via `tool_start`/`tool_end` events.
7. Session history is persisted in Redis; Chainlit conversation data is persisted in PostgreSQL.

**GitHub Actions AI tooling** runs independently against the repo using five Claude-powered workflows: code review, tech docs, business docs, test generation, and UAT facilitation. Outputs are written to a separate `ai-delivery-outputs` repository.

---

## 4. Local Development Setup

**Prerequisites:** Docker Desktop, Python 3.12, Node.js [TODO: confirm Node version required by frontend]

1. **Clone the repository**

```bash
git clone https://github.com/kylodeng/underwriting_chatbot-main.git
cd underwriting_chatbot-main
```

2. **Create the root `.env` file** (see [Environment Variables](#5-environment-variables) section)

```bash
cp .env.example .env   # [TODO: confirm whether .env.example exists]
# Then edit .env and populate required values
```

3. **Start infrastructure services (Redis + PostgreSQL)**

```bash
docker compose up redis postgres -d
```

4. **Start all services via Docker Compose**

```bash
docker compose up --build
```

5. **Verify the backend is healthy**

```bash
curl http://localhost:8000/health
# Expected: {"status": "ok"}
```

6. **Access the frontend**

   Open [http://localhost:8080](http://localhost:8080) in your browser.

7. **(Optional) Run the backend locally without Docker**

```bash
cd backend
pip install -r requirements.txt   # [TODO: confirm requirements.txt filename/location]
uvicorn main:app --reload --port 8000
```

---

## 5. Environment Variables

The backend reads from a `.env` file at the repo root (loaded via `python-dotenv`). The following variables are referenced in the source files:

| Variable | Required | Default | Description |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | — | API key for Anthropic Claude (all LLM calls) |
| `GOOGLE_API_KEY` | No | — | API key for Google Gemini (`gemini` model option) |
| `REDIS_HOST` | No | `localhost` | Redis hostname; set to `redis` when running via Docker Compose |
| `GH_TOKEN` | Yes (CI only) | — | GitHub personal access token for GitHub Actions workflows |
| `SENDGRID_API_KEY` | Yes (CI only) | — | SendGrid API key for workflow email notifications |
| `OUTPUT_REPO` | No (CI only) | `ai-delivery-outputs` | GitHub repo name where AI workflow outputs are written |
| `OUTPUT_REPO_OWNER` | No (CI only) | `GITHUB_REPOSITORY_OWNER` | Owner of the output repo |
| `NOTIFY_EMAIL` | No (CI only) | `kylo.deng@capco.com` | Recipient email for workflow notifications |
| `SENDER_EMAIL` | No (CI only) | `kylo.deng@capco.com` | Sender email for workflow notifications |

The PostgreSQL credentials are hardcoded in `docker-compose.yml` (`chainlit`/`chainlit`/`chainlit`) and passed to the frontend via the `DATABASE_URL` environment variable. [TODO: should these be moved to `.env` for security?]

---

## 6. Running Tests

[TODO: No test files or test runner configuration were found in the provided source files. What is the test framework and where do tests live?]

The repo includes a GitHub Actions workflow (Tool 4) that **auto-generates** tests using Claude and writes them to the `ai-delivery-outputs` repository. To trigger it manually:

```bash
# Via GitHub CLI
gh workflow run "Tool 4 — Auto Testing" --field test_mode=generate

# Or trigger gap analysis mode
gh workflow run "Tool 4 — Auto Testing" --field test_mode=gap-analysis
```

---

## 7. Deployment

### Local / Docker Compose (development)

```bash
# Build and start all services
docker compose up --build

# Stop all services
docker compose down

# Stop and remove volumes (destructive — clears PostgreSQL data)
docker compose down -v
```

### GitHub Actions CI/CD Workflows

Five automated workflows are available. All require the following repository secrets to be set in GitHub:
- `ANTHROPIC_API_KEY`
- `GH_TOKEN`
- `SENDGRID_API_KEY`

| Workflow | Trigger | Manual dispatch |
|---|---|---|
| Tool 1 — Code Review | PR open/sync, Monday 08:00 UTC | Yes (`repo` or `pr` mode) |
| Tool 2 — Tech Docs | Push to `main`, Sunday 06:00 UTC | Yes |
| Tool 3 — Business Docs | Push of `v*` tag, | Yes (requires `project_name`, `release_version`) |
| Tool 4 — Auto Testing | PR open/sync on `src/**`, Wednesday 07:00 UTC | Yes (`generate` or `gap-analysis`) |
| Tool 5 — UAT Facilitation | `release/*` branch creation | Yes (`generate` or `analyse` mode) |

```bash
# Example: manually trigger tech doc generation
gh workflow run "Tool 2 — Tech Documentation"

# Example: trigger business doc generation for a release
gh workflow run "Tool 3 — Business Documentation" \
  --field project_name="Underwriting Chatbot" \
  --field release_version="1.0.0"

# Example: trigger UAT test pack generation
gh workflow run "Tool 5 — UAT Facilitation" \
  --field uat_mode=generate \
  --field release_version="1.0.0"
```

[TODO: Is there a Kubernetes, cloud (Azure/AWS/GCP), or Terraform deployment configuration? None was found in the provided files.]

---

## 8. Known Issues / TODOs

Extracted from code comments and source files:

| Location | Issue / TODO |
|---|---|
| `backend/agent/graph.py` | **TODO:** Migrate Redis to an external service (e.g. Azure Cache for Redis, dedicated Redis container) so that memory persists across serverless backend instances. |
| `backend/modules/LLMS.py` | **TODO:** Add more LLM providers (currently `azure` and `openai` entries in the mapper return `None` and will raise `ValueError` if requested). |
| `backend/agent/graph.py` | The `lifespan` context manager for FastAPI is commented out — application startup/shutdown lifecycle is not currently managed. |
| `backend/agent/agent_with_skills.py` | The `agent_with_skills.py` and `graph.py` appear to be two parallel implementations of the agent. [TODO: Which is the canonical implementation used by `main.py`?] |
| `docker-compose.yml` | PostgreSQL credentials (`chainlit`/`chainlit`) are hardcoded in the compose file rather than read from `.env`. |
| `backend/main.py` | The `_charts_sent` set is a module-level in-process set, which will not work correctly across multiple backend instances or restarts. |
| `.github/scripts/tool5_uat.py` (SYSTEM_RUNBOOK) | Escalation path: `[TODO: fill in team contacts]` |
| `.github/scripts/tool3_business_docs.py` | Stakeholder names, go-live dates, and success metrics are always `[TODO]` — must be filled in manually after generation. |
| `backend/config.yml` | `specialist_max_tokens: 1500` — comment notes this was added because specialist LLMs were previously hitting 2772 tokens, indicating potential truncation risk for complex profiles. |