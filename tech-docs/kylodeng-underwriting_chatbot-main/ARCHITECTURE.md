# Architecture Document — kylodeng/underwriting_chatbot-main

---

## 1. Overview

The Underwriting Chatbot is an AI-assisted life insurance underwriting platform that enables underwriters to assess customer risk profiles through a conversational interface. A frontend chat UI communicates with a FastAPI backend, which orchestrates a LangGraph-based multi-agent system. When queried, the agent retrieves customer profiles from SQLite databases, runs parallel specialist LLM assessments across domains (finance, health, life, KYC, etc.) using Anthropic Claude models (with optional Google Gemini), aggregates the results into a structured `UnderwritingReport`, and streams the response back to the user via Server-Sent Events. The repository also includes a suite of five AI-powered GitHub Actions CI/CD tools that perform automated code review, technical documentation generation, business documentation, test generation, and UAT facilitation — all powered by Claude via the Anthropic API.

---

## 2. Resources Deployed

| Resource | Type | Cloud Provider | Purpose |
|---|---|---|---|
| Backend (FastAPI) | Docker container | Self-hosted / Docker Compose | Serves `/chat` and `/health` endpoints; orchestrates LangGraph agent |
| Frontend | Docker container | Self-hosted / Docker Compose | Chat UI served on port 8080 |
| Redis Stack Server 7.2 | Docker container | Self-hosted / Docker Compose | LangGraph checkpoint/memory store for conversation state |
| PostgreSQL 16 (Alpine) | Docker container | Self-hosted / Docker Compose | Chainlit session/auth persistence |
| `customer_profile.db` | SQLite file (read-only mount) | Self-hosted | Customer demographic and profile data |
| `feature_importance.db` | SQLite file (read-only mount) | Self-hosted | CatBoost model feature importance data |
| `model_predictions.db` | SQLite file (read-only mount) | Self-hosted | Pre-computed ML risk classification predictions |
| `application_profile.db` | SQLite file (read-only mount) | Self-hosted | Insurance application data |
| `postgres_data` | Docker named volume | Self-hosted | Persistent PostgreSQL data |
| Anthropic Claude API | External SaaS | Anthropic | Primary LLM for agent reasoning, specialist assessment, and CI/CD tools |
| Google Gemini API | External SaaS | Google Cloud | Alternative LLM provider (configured, optional) |
| GitHub Actions runners | Managed CI/CD | GitHub | Executes all five automation workflow tools |
| SendGrid API | External SaaS | Twilio/SendGrid | Email notifications from CI/CD tools |
| `ai-delivery-outputs` repo | GitHub repository | GitHub | Stores generated documentation, test files, and audit outputs |

---

## 3. Data Flow

### Chat / Assessment Flow

1. **User** sends a message via the frontend chat UI (port 8080) over HTTP POST.
2. **Frontend** forwards the request to the backend API at `http://backend:8000/chat` with `message`, `session_id`, `model`, `temperature`, and `mode` parameters.
3. **Backend `/chat` endpoint** calls `build_agent()` which instantiates a LangGraph agent with a Redis-backed `AsyncRedisSaver` checkpointer (loading prior conversation history for the `session_id`).
4. **LangGraph agent** receives the `HumanMessage` and invokes the tagged Claude LLM (`anthropic-fast` by default) to reason about which tool to call next.
5. **Agent tool call — `get_customer_profile`**: Backend queries SQLite databases (`customer_profile.db`, `application_profile.db`) to retrieve structured customer data.
6. **Agent tool call — `customer_lookalike`**: Backend loads `backend/tmp/customer_similarity_dict.json` to find similar historical customers.
7. **Agent tool call — `run_underwriting_assessment`**: The assessment module fans out up to 4 parallel async specialist LLM calls (controlled by `asyncio.Semaphore(4)`), each using a domain-specific prompt from `assessment_criterias.json` (finance, health, life, KYC, etc.).
8. **Aggregator LLM** receives all specialist outputs and produces a structured `UnderwritingReport` Pydantic model (JSON) via `structured_output`.
9. **Backend** streams the response back to the frontend using **Server-Sent Events (SSE)** — emitting `tool_start`, `tool_end`, `response` (text chunks), and `chart` events.
10. **Frontend** renders streamed text and any chart payloads in the chat UI.
11. **Redis** persists conversation checkpoints so that follow-up questions in the same session maintain context.

### CI/CD Automation Flow

1. GitHub event (PR, push to `main`, tag, schedule, or `workflow_dispatch`) triggers one of the five workflow YAML files.
2. The runner checks out the source repository and installs `anthropic` and `requests`.
3. The corresponding Python script reads source/IaC files from the repository (via GitHub API or local checkout).
4. The script calls the **Anthropic Claude API** (`claude-sonnet-4-6`) with a structured prompt.
5. Outputs (Markdown docs, JSON reports, CSV test packs) are written to the `ai-delivery-outputs` GitHub repository via the GitHub Contents API.
6. **SendGrid** sends an email notification to `kylo.deng@capco.com` with a summary and link.
7. For PR-triggered reviews (Tool 1), a comment is also posted directly to the pull request.

---

## 4. Security Posture

### ✅ What Is Secured

- **SQLite databases mounted read-only** (`ro` flag in `docker-compose.yml`) — prevents backend from writing to source-of-truth data files.
- **Secrets managed via GitHub Actions secrets** — `ANTHROPIC_API_KEY`, `GH_TOKEN`, `SENDGRID_API_KEY` are not hardcoded in workflow YAML files.
- **`.env` file pattern** — backend uses `dotenv` for local secret loading, keeping credentials out of source code.
- **System prompt confidentiality** — agent explicitly instructed never to reveal internal system instructions or tool list to users.
- **Specialist LLM token caps** — `specialist_max_tokens: 1500` and `aggregator_max_tokens: 8000` limit runaway cost/data exposure.

### ❌ Security Gaps and Missing Controls

- **No TLS/HTTPS configured** — all inter-service communication (frontend→backend, client→frontend) is plain HTTP. There is no TLS termination in the Docker Compose setup. Any production deployment exposes data in transit unencrypted.
- **PostgreSQL credentials hardcoded in `docker-compose.yml`** — `POSTGRES_USER: chainlit`, `POSTGRES_PASSWORD: chainlit`, `POSTGRES_DB: chainlit` are plain-text defaults, never rotated, and committed to source control.
- **Redis has no authentication** — `redis-stack-server` is started with no password, no ACLs, and no TLS. Conversation checkpoints (which may contain PII/customer data) are stored unencrypted.
- **CORS policy is fully open** — `allow_origins=["*"]`, `allow_methods=["*"]`, `allow_headers=["*"]` in `main.py`. Any origin can call the backend API.
- **No authentication or authorisation on the `/chat` endpoint** — any client that can reach port 8000 can query customer data and run assessments.
- **`GH_TOKEN` scope unknown** — [TODO: What permissions does this token hold? If it has `repo` write scope across the org, it is overly broad and should be scoped to only the `ai-delivery-outputs` repo.]
- **Customer similarity data in `backend/tmp/`** — `customer_similarity_dict.json` contains ~10,000 customer IDs committed directly to the repository, potentially exposing customer identifiers.
- **SQLite databases likely containing PII** — `customer_profile.db`, `application_profile.db` etc. are mounted from a `./database/` directory with no encryption at rest noted.
- **No secrets scanning** — no `.gitleaks`, `trufflehog`, or GitHub secret scanning configuration observed.
- **No network segmentation** — all Docker services are on the default bridge network; Redis and PostgreSQL ports (6379, 5432) are exposed to the host.
- **`model_predictions.db` encryption** — ML predictions tied to customer IDs stored in plaintext SQLite. [TODO: Is this classified as personal data under applicable regulations?]

---

## 5. Environment Variables and Secrets

| Name | Required | Sensitivity | Where Set |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | 🔴 Secret | GitHub Actions secret; `.env` file (backend) |
| `GH_TOKEN` | Yes | 🔴 Secret | GitHub Actions secret |
| `SENDGRID_API_KEY` | Yes | 🔴 Secret | GitHub Actions secret |
| `GOOGLE_API_KEY` | Yes (if using Gemini) | 🔴 Secret | `.env` file (backend) |
| `REDIS_HOST` | No | 🟢 Low | `docker-compose.yml` environment block; defaults to `localhost` |
| `DATABASE_URL` | Yes (frontend) | 🟡 Medium | `docker-compose.yml` environment block (hardcoded credentials) |
| `BACKEND_URL` | Yes (frontend) | 🟢 Low | `docker-compose.yml` environment block |
| `POSTGRES_USER` | Yes | 🟡 Medium | `docker-compose.yml` (hardcoded: `chainlit`) |
| `POSTGRES_PASSWORD` | Yes | 🔴 Secret | `docker-compose.yml` (hardcoded: `chainlit`) ⚠️ |
| `POSTGRES_DB` | Yes | 🟢 Low | `docker-compose.yml` (hardcoded: `chainlit`) |
| `OUTPUT_REPO` | No | 🟢 Low | GitHub Actions env; defaults to `ai-delivery-outputs` |
| `OUTPUT_REPO_OWNER` | No | 🟢 Low | GitHub Actions env; defaults to `github.repository_owner` |
| `NOTIFY_EMAIL` | No | 🟢 Low | GitHub Actions env; hardcoded to `kylo.deng@capco.com` |
| `SENDER_EMAIL` | No | 🟢 Low | GitHub Actions env; hardcoded to `noreply@ai-delivery.capco.com` |
| `REVIEW_MODE` | No | 🟢 Low | Set dynamically in CI workflow step |
| `PR_NUMBER` | No | 🟢 Low | Set dynamically in CI workflow step |
| `RELEASE_VERSION` | No | 🟢 Low | Set dynamically in CI workflow step |
| `PROJECT_NAME` | No | 🟢 Low | Set dynamically in CI workflow step |
| `TEST_MODE` | No | 🟢 Low | GitHub Actions env; defaults to `generate` |
| `UAT_MODE` | No | 🟢 Low | Set dynamically in CI workflow step |

---

## 6. Dependencies

| Dependency | Type | Purpose | Version Pinned? |
|---|---|---|---|
| Anthropic Claude API (`claude-sonnet-4-20250514`, `claude-haiku-4-5-20251001`) | External SaaS API | Primary LLM for chat agent, specialist assessment, and all 5 CI tools | Model name pinned; SDK version not pinned in CI |
| Google Gemini API (`gemini-3-flash-preview`) | External SaaS API | Alternative LLM provider | Model name pinned |
| LangChain / LangGraph | Python library | Agent orchestration, graph state management, tool binding | [TODO: check `requirements.txt` or `pyproject.toml` for pinned versions] |
| `langchain-anthropic` | Python library | LangChain adapter for Claude | [TODO: version?] |
| `langchain-google-genai` | Python library | LangChain adapter for Gemini | [TODO: version?] |
| Redis Stack Server 7.2 | Docker image | LangGraph checkpoint / conversation memory | `7.2.0-v14` pinned |
| PostgreSQL 16 | Docker image | Chainlit session persistence | `16-alpine` pinned |
| `anthropic` (Python SDK) | Python library | Direct Claude calls in CI/CD scripts | Not pinned in CI workflows |
| `requests` | Python library | GitHub API and SendGrid calls in CI/CD scripts | Not pinned |
| `fastapi`, `uvicorn` | Python library | Backend HTTP server | [TODO: version?] |
| `sse-starlette` | Python library | Server-Sent Events streaming | [TODO: version?] |
| `pydantic` | Python library | Structured output models | [TODO: version?] |
| `python-dotenv` | Python library | Local `.env` loading | [TODO: version?] |
| `catboost` | Python library (inference only) | ML risk classification (model card present; tool usage implied) | [TODO: is this actually imported anywhere?] |
| SendGrid API | External SaaS | Email notifications from CI/CD tools | N/A |
| `ai-delivery-outputs` (GitHub repo) | External GitHub repository | Stores all generated documentation and test artifact outputs | Must exist under same org owner |
| GitHub Actions | SaaS CI/CD | Runs all 5 automation workflows | N/A |
| `chainlit` | Frontend framework (implied) | Chat UI and session management | [TODO: version and exact framework usage?] |

---

## 7. Deployment Instructions

### Prerequisites

- Docker and Docker Compose v2+ installed
- A `.env` file in the project root containing at minimum:

```bash
ANTHROPIC_API_KEY=sk-ant-...
GOOGLE_API_KEY=AIza...          # optional if not using Gemini
```

- SQLite database files present under `./database/`:
  - `customer_profile.db`
  - `feature_importance.db`
  - `model_predictions.db`
  - `application_profile.db`
- PostgreSQL init script at `./postgres/init.sql`

### Local / Docker Compose Deployment

```bash
# 1. Clone the repository
git clone https://github.com/kylodeng/underwriting_chatbot-main.git
cd underwriting_chatbot-main

# 2. Create the .env file
cp .env.example .env          # [TODO: confirm .env.example exists]
# Edit .env and populate ANTHROPIC_API_KEY and GOOGLE_API_KEY

# 3. Build and start all services
docker compose up --build -d

# 4. Verify services are healthy
docker compose ps
curl http://localhost:8000/health   # should return {"status": "ok"}

# 5. Access the frontend
open http://localhost:8080
```

### Tear Down

```bash
docker compose down -v    # removes containers AND the postgres_data volume
```

### CI/CD Tools — Manual Trigger

```bash
# Tool 1: Manual code review on a specific PR
gh workflow run tool1_code_review.yml \
  -f review_mode=pr \
  -f pr_number=42

# Tool 2: Regenerate tech docs
gh workflow run tool2_tech_docs.yml

# Tool 3: Generate business docs for a release
gh workflow run tool3_business_docs.yml \
  -f project_name="Underwriting Chatbot" \
  -f release_version="1.0.0"

# Tool 4: Generate tests
gh workflow run tool4_auto_testing.yml \
  -f test_mode=generate

# Tool 5: Generate UAT test pack
gh workflow run tool5_uat.yml \
  -f uat_mode=generate \
  -f release_version="1.0.0"
```

### Release Tagging (triggers Tool 3 automatically)

```bash
git tag v1.0.0
git push origin v1.0.0
```

---

## 8. Risks and TODOs

### Extracted from Code

| Location | Risk / TODO |
|---|---|
| `backend/agent/graph.py` line 1 | **TODO (in code):** Migrate Redis to an external managed service (e.g. Azure Cache for Redis) so memory persists across serverless/ephemeral backend instances |
| `backend/modules/LLMS.py` | **TODO (in code):** Add more LLM providers; `azure` and `openai` entries are `None` — calling either will raise `ValueError` at runtime |
| `backend/main.py` | `lifespan` context manager is commented out — no graceful startup/