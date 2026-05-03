# Underwriting Chatbot

## 1. Project Overview

An AI-powered underwriting assistant that helps insurance underwriters assess customer risk profiles through a conversational chat interface. The backend orchestrates multiple specialist LLM agents (finance, health, life, etc.) that independently evaluate different risk dimensions before an aggregator LLM produces a structured `UnderwritingReport`. A suite of five GitHub Actions CI/CD tools—powered by Claude—automates code review, technical documentation, business documentation, test generation, and UAT facilitation for the repository itself.

---

## 2. Tech Stack

| Component | Technology | Version/Notes |
|---|---|---|
| Backend API | FastAPI | Python, SSE streaming via `sse-starlette` |
| Agent Orchestration | LangGraph / LangChain | `StateGraph`, tool-calling, streaming events |
| Primary LLM (fast) | Anthropic Claude Haiku | `claude-haiku-4-5-20251001` |
| Primary LLM (standard) | Anthropic Claude Sonnet | `claude-sonnet-4-20250514` |
| Secondary LLM | Google Gemini | `gemini-3-flash-preview` |
| Frontend | [TODO: what framework is the frontend built with?] | Served on port 8080 |
| Session Memory / Checkpointing | Redis (LangGraph Redis checkpointer) | `redis/redis-stack-server:7.2.0-v14` |
| Chat UI Persistence | PostgreSQL | `postgres:16-alpine` |
| Customer Data | SQLite databases | `customer_profile.db`, `feature_importance.db`, `model_predictions.db`, `application_profile.db` |
| ML Model | CatBoostClassifier | v1.0, trained on merged customer/KYC/application data |
| Containerisation | Docker Compose | Multi-service: redis, postgres, backend, frontend |
| CI/CD AI Tools | Anthropic Claude Sonnet | `claude-sonnet-4-6` via GitHub Actions |
| Email Notifications | SendGrid | Used by CI/CD workflow scripts |
| Python Runtime | Python | 3.12 (CI), [TODO: confirm backend Dockerfile Python version] |

---

## 3. Architecture

The system is composed of four Docker services that interact as follows:

1. **Frontend** (port 8080) accepts user chat messages and streams responses. It connects to the **Backend** over HTTP (`BACKEND_URL=http://backend:8000`) and persists chat history in **PostgreSQL**.
2. **Backend** (port 8000) is a FastAPI application. On each `/chat` request it builds a LangGraph agent (`build_agent`) configured with three tools: `get_customer_profile`, `customer_lookalike`, and `run_underwriting_assessment`. The agent uses **Redis** (via `AsyncRedisSaver`) as a LangGraph checkpointer so conversation thread state persists across turns within a session.
3. The **underwriting assessment** tool fans out up to 4 concurrent specialist LLM calls (bounded by a semaphore) — one per assessment category (finance, health, life, etc.) — then feeds all specialist reports to an aggregator LLM that produces a structured `UnderwritingReport` Pydantic object.
4. Customer data is served from read-only **SQLite databases** mounted into the backend container.
5. **Redis** also backs the LangGraph checkpoint store; it is currently expected to run locally (see Known Issues).
6. Five **GitHub Actions workflows** (`.github/workflows/tool1–5`) each invoke a corresponding Python script under `.github/scripts/` that calls the Anthropic Claude API to perform automated code review, documentation generation, test generation, and UAT facilitation, writing outputs to a separate `ai-delivery-outputs` repository.

```
User
 │
 ▼
Frontend (port 8080)
 │  HTTP + SSE
 ▼
Backend FastAPI (port 8000)
 ├── LangGraph Agent
 │    ├── get_customer_profile  ──► SQLite DBs
 │    ├── customer_lookalike    ──► customer_similarity_dict.json
 │    └── run_underwriting_assessment
 │         ├── Specialist LLM × N (tagged "thinking")
 │         └── Aggregator LLM  → UnderwritingReport
 ├── Redis (session checkpointing, port 6379)
 └── PostgreSQL (chat persistence, port 5432)
```

---

## 4. Local Development Setup

### Prerequisites
- Docker and Docker Compose installed
- API keys for Anthropic and (optionally) Google Gemini

**Steps:**

1. **Clone the repository**
   ```bash
   git clone https://github.com/kylodeng/underwriting_chatbot-main.git
   cd underwriting_chatbot-main
   ```

2. **Create the root `.env` file** (used by Docker Compose and the backend)
   ```bash
   cp .env.example .env   # if an example exists, otherwise create manually
   ```
   Populate at minimum:
   ```env
   ANTHROPIC_API_KEY=your_anthropic_key
   GOOGLE_API_KEY=your_google_key
   ```
   [TODO: confirm whether a `.env.example` file exists and list all required variables]

3. **Ensure SQLite database files are present** under `./database/`
   ```
   database/
   ├── customer_profile.db
   ├── feature_importance.db
   ├── model_predictions.db
   └── application_profile.db
   ```
   [TODO: document how to obtain or seed these database files]

4. **Build and start all services**
   ```bash
   docker compose up --build
   ```

5. **Verify the backend is healthy**
   ```bash
   curl http://localhost:8000/health
   # Expected: {"status": "ok"}
   ```

6. **Open the frontend**
   Navigate to `http://localhost:8080` in your browser.

### Running the backend locally (without Docker)

1. **Install Python dependencies**
   ```bash
   cd backend
   pip install -r requirements.txt   # [TODO: confirm requirements.txt exists]
   ```

2. **Start Redis** (required for LangGraph checkpointing)
   ```bash
   docker run -p 6379:6379 redis/redis-stack-server:7.2.0-v14
   ```

3. **Start the backend**
   ```bash
   cd backend
   uvicorn main:app --reload --port 8000
   ```

---

## 5. Environment Variables

### Backend / Docker Compose

| Variable | Required | Default | Description |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | — | API key for Anthropic Claude models |
| `GOOGLE_API_KEY` | Yes (if using Gemini) | — | API key for Google Gemini models |
| `REDIS_HOST` | No | `localhost` | Hostname of the Redis instance for LangGraph checkpointing |

### GitHub Actions CI/CD Workflows

| Variable | Required | Default | Description |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | — | Claude API key for all five AI delivery tools |
| `GH_TOKEN` | Yes | — | GitHub token with repo read/write access for fetching code and writing outputs |
| `SENDGRID_API_KEY` | Yes | — | SendGrid API key for email notifications |
| `OUTPUT_REPO` | No | `ai-delivery-outputs` | Name of the GitHub repository where generated documents are written |
| `OUTPUT_REPO_OWNER` | No | `github.repository_owner` | Owner of the output repository |
| `NOTIFY_EMAIL` | No | `kylo.deng@capco.com` | Email address to receive notifications |
| `SENDER_EMAIL` | No | `kylo.deng@capco.com` | Sender address for outbound emails |

---

## 6. Running Tests

[TODO: are there existing tests in the repository (e.g. a `tests/` directory)? No test files were found in the provided source.]

The repository includes an automated test-generation GitHub Actions workflow (Tool 4) that uses Claude to generate `pytest` / `jest` test files. To trigger it manually:

1. Go to **Actions → Tool 4 — Auto Testing** in GitHub.
2. Click **Run workflow** and choose mode `generate` or `gap-analysis`.

To run any generated tests locally once they exist:

```bash
# Python (pytest)
cd backend
pytest tests/ -v

# JavaScript/TypeScript (jest)
cd frontend
npm test
```

---

## 7. Deployment

### Local / Development — Docker Compose

```bash
# Build images and start all services in detached mode
docker compose up --build -d

# View logs
docker compose logs -f

# Stop all services
docker compose down

# Stop and remove volumes (wipes PostgreSQL data)
docker compose down -v
```

### CI/CD — GitHub Actions

The repository contains five automated workflows triggered on various events:

| Workflow | Trigger | Purpose |
|---|---|---|
| Tool 1 — Code Review | PR open/sync, Monday 08:00 UTC, manual | AI code review posted as PR comment |
| Tool 2 — Tech Docs | Push to `main`, Sunday 06:00 UTC, manual | Generates README, ARCHITECTURE, RUNBOOK |
| Tool 3 — Business Docs | Version tag (`v*`), manual dispatch | Generates solution overview + gap questionnaire |
| Tool 4 — Auto Testing | PR open/sync on `src/**`, Wednesday 07:00 UTC, manual | Generates or analyses test coverage |
| Tool 5 — UAT | `release/*` branch creation, manual dispatch | Generates UAT test pack or analyses results CSV |

All workflows require the following GitHub repository secrets to be set:

```
ANTHROPIC_API_KEY
GH_TOKEN
SENDGRID_API_KEY
```

### Production Deployment

[TODO: is there Terraform, Bicep, Helm, or other IaC for cloud deployment? No IaC files were found in the provided sources.]

[TODO: what is the target cloud provider and hosting environment (e.g. Azure Container Apps, AWS ECS, GKE)?]

---

## 8. Known Issues / TODOs

Extracted directly from code comments:

| Location | Issue / TODO |
|---|---|
| `backend/agent/graph.py` | **TODO:** Migrate Redis to an external service (e.g. Azure Cache for Redis, dedicated Redis container) so that memory persists across serverless backend instances. Currently Redis is expected on `localhost:6379`, which will not persist state in a serverless deployment. |
| `backend/modules/LLMS.py` | **TODO:** Add more LLM providers. `azure` and `openai` entries in the model mapper are currently `None` and will raise `ValueError` if selected. |
| `backend/agent/agent_with_skills.py` | Two agent implementations exist (`agent_with_skills.py` and `graph.py`). [TODO: clarify which is the active/production agent and whether `agent_with_skills.py` is a replacement or experimental branch.] |
| `backend/main.py` | A `lifespan` context manager is commented out — its purpose and whether it should be re-enabled is unclear. |
| `.github/scripts/shared.py` | `send_email`, `email_html`, and `write_audit_entry` functions are imported by all tool scripts but their implementations are truncated in the provided files. [TODO: confirm these functions are fully implemented in the actual file.] |
| `backend/config.yml` | `specialist_max_tokens: 1500` was set to cap runaway output (comment notes previous runs hit 2772 tokens). May need tuning for complex profiles. |
| General | No `tests/` directory or test files were found in the repository sources. |
| General | No IaC (Terraform, Bicep, CloudFormation) was found — production infrastructure deployment path is undefined. |