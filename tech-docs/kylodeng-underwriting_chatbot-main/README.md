# Underwriting Chatbot

## 1. Project Overview

An AI-powered life insurance underwriting assistant that helps underwriters assess customer risk profiles through a conversational chat interface. The system uses a multi-agent LangGraph pipeline to run parallel specialist assessments across finance, health, and life domains before aggregating them into a structured underwriting report. A suite of five GitHub Actions workflows provides automated code review, documentation generation, test generation, and UAT facilitation via Claude AI.

---

## 2. Tech Stack

| Component | Technology | Version/Notes |
|---|---|---|
| Backend framework | FastAPI | Python, with SSE streaming |
| Agent orchestration | LangGraph | StateGraph with tool-calling loop |
| LLM (default/fast) | Anthropic Claude Haiku | `claude-haiku-4-5-20251001` |
| LLM (specialist/aggregator) | Anthropic Claude Sonnet | `claude-sonnet-4-20250514` |
| LLM (alternative) | Google Gemini | `gemini-3-flash-preview` |
| Frontend | Chainlit | Connects to backend via `BACKEND_URL` |
| Session memory / checkpointing | Redis (LangGraph AsyncRedisSaver) | `redis/redis-stack-server:7.2.0-v14` |
| Frontend persistence (chat history) | PostgreSQL | `postgres:16-alpine` |
| Customer data | SQLite databases | Mounted read-only into backend container |
| Customer similarity index | JSON file | `backend/tmp/customer_similarity_dict.json` |
| Risk model metadata | CatBoostClassifier model card | `backend/model_card.json` |
| CI/CD AI tools | GitHub Actions + Anthropic Claude | `claude-sonnet-4-6` (shared.py) |
| Containerisation | Docker Compose | Multi-service |
| Configuration | YAML | `backend/config.yml` |
| Data validation | Pydantic v2 | `UnderwritingReport`, `AreaOfInterest`, etc. |

---

## 3. Architecture

The system is composed of four Docker services that interact as follows:

1. **Frontend (Chainlit)** — serves the chat UI. User messages are POSTed to the backend's `/chat` endpoint and responses are streamed back via Server-Sent Events (SSE). Chat history is persisted in **PostgreSQL**.

2. **Backend (FastAPI)** — receives chat requests, builds a LangGraph agent for each request, and streams events back to the frontend. The agent (`agent_with_skills.py` / `graph.py`) uses a tool-calling loop: it decides which tool to invoke, calls it, observes the result, and repeats until it emits a `"done"` action.

3. **Agent tools** — three tools are registered with the agent:
   - `get_customer_profile` — retrieves a customer record from the SQLite databases.
   - `customer_lookalike` — returns a list of similar customers from the pre-computed JSON similarity index.
   - `run_underwriting_assessment` — runs parallel specialist LLM calls (finance, health, life domains) using an `asyncio.Semaphore(4)` to cap concurrency, then aggregates results into a structured `UnderwritingReport` via a Pydantic-structured output LLM call.

4. **Redis** — stores LangGraph conversation checkpoints so the agent can maintain context across turns within a session.

5. **GitHub Actions workflows** — five independent workflows invoke Python scripts that use a shared Claude AI client (`shared.py`) to perform automated code review, README/architecture/runbook generation, business documentation, test generation, and UAT test pack creation against this repository.

```
User ──► Frontend (Chainlit :8080)
              │ HTTP POST /chat  (SSE stream back)
              ▼
         Backend (FastAPI :8000)
              │ LangGraph agent loop
              ├──► get_customer_profile   ──► SQLite DBs (read-only volumes)
              ├──► customer_lookalike     ──► customer_similarity_dict.json
              └──► run_underwriting_assessment
                        │ asyncio parallel specialist LLM calls (Claude Haiku)
                        └──► aggregator LLM call (Claude Haiku, structured output)
              │ Checkpoint per turn
              ▼
           Redis (:6379)

         PostgreSQL (:5432) ◄── Frontend (chat history)
```

---

## 4. Local Development Setup

### Prerequisites

- Docker and Docker Compose installed
- An Anthropic API key
- (Optional) A Google API key if using the Gemini model

**Step 1 — Clone the repository**

```bash
git clone https://github.com/kylodeng/underwriting_chatbot-main.git
cd underwriting_chatbot-main
```

**Step 2 — Create the root `.env` file**

```bash
cp .env.example .env   # if an example exists, otherwise create manually
```

Populate `.env` with at minimum:

```env
ANTHROPIC_API_KEY=your_anthropic_key_here
GOOGLE_API_KEY=your_google_key_here
```

[TODO: Confirm whether a `.env.example` file exists and what other variables are required at root level]

**Step 3 — Ensure the SQLite database files are present**

The backend expects these files to exist (they are mounted read-only):

```
database/customer_profile.db
database/feature_importance.db
database/model_predictions.db
database/application_profile.db
```

[TODO: Confirm how these database files are obtained — are they checked in, generated by a script, or downloaded separately?]

**Step 4 — Build and start all services**

```bash
docker compose up --build
```

**Step 5 — Verify services are running**

```bash
curl http://localhost:8000/health
# Expected: {"status": "ok"}
```

**Step 6 — Open the frontend**

Navigate to [http://localhost:8080](http://localhost:8080) in your browser.

---

**Running the backend outside Docker (for development)**

```bash
cd backend
pip install -r requirements.txt   # [TODO: confirm requirements.txt exists]
uvicorn main:app --reload --port 8000
```

---

## 5. Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | — | Anthropic API key for Claude models |
| `GOOGLE_API_KEY` | No | — | Google API key for Gemini model; required only if `gemini` model is selected |
| `REDIS_HOST` | No | `localhost` | Redis hostname; set to `redis` automatically in Docker Compose |
| `ANTHROPIC_API_KEY` (GitHub secret) | Yes (CI) | — | Used by all five GitHub Actions workflows |
| `GH_TOKEN` (GitHub secret) | Yes (CI) | — | GitHub PAT used by CI scripts to read repos and write to the output repo |
| `SENDGRID_API_KEY` (GitHub secret) | Yes (CI) | — | SendGrid key for email notifications from CI workflows |
| `OUTPUT_REPO` (CI env) | No | `ai-delivery-outputs` | GitHub repo name where CI tools write generated documents |
| `OUTPUT_REPO_OWNER` (CI env) | No | `GITHUB_REPOSITORY_OWNER` | GitHub owner of the output repo |
| `NOTIFY_EMAIL` (CI env) | No | `kylo.deng@capco.com` | Recipient email for CI notifications |
| `SENDER_EMAIL` (CI env) | No | `kylo.deng@capco.com` | Sender email for CI notifications |

[TODO: Are there any additional environment variables consumed by the frontend (Chainlit) beyond `BACKEND_URL` and `DATABASE_URL`?]

---

## 6. Running Tests

[TODO: No test files or test runner configuration were found in the provided source files. How are tests run for this project?]

The repository includes a GitHub Actions workflow (Tool 4 — Auto Testing, `.github/workflows/tool4_auto_testing.yml`) that uses Claude AI to **generate** pytest test files for source files on pull requests or on a Wednesday schedule. Generated tests are written to the `ai-delivery-outputs` repository rather than run in-place.

To trigger test generation manually:

1. Go to **Actions → Tool 4 — Auto Testing** in the GitHub UI.
2. Select **Run workflow**, choose mode `generate` or `gap-analysis`, and run.

---

## 7. Deployment

### Local / development deployment (Docker Compose)

```bash
docker compose up --build -d
```

```bash
# Check all containers are healthy
docker compose ps
```

```bash
# Tail logs
docker compose logs -f backend
```

```bash
# Tear down
docker compose down
```

### GitHub Actions CI/CD workflows

The five automation tools are triggered automatically or manually:

| Workflow | Trigger | Manual dispatch |
|---|---|---|
| Tool 1 — Code Review | PR open/sync; Monday 08:00 UTC | Actions → Tool 1 → Run workflow |
| Tool 2 — Tech Docs | Push to `main`; Sunday 06:00 UTC | Actions → Tool 2 → Run workflow |
| Tool 3 — Business Docs | Push of `v*` tag; manual | Actions → Tool 3 → Run workflow |
| Tool 4 — Auto Testing | PR open/sync on source files; Wednesday 07:00 UTC | Actions → Tool 4 → Run workflow |
| Tool 5 — UAT | `release/*` branch creation; manual | Actions → Tool 5 → Run workflow |

Required GitHub repository secrets (set under **Settings → Secrets and variables → Actions**):

```
ANTHROPIC_API_KEY
GH_TOKEN
SENDGRID_API_KEY
```

To trigger Tool 3 via a version tag:

```bash
git tag v1.0.0
git push origin v1.0.0
```

[TODO: Is there a cloud infrastructure deployment (e.g. Kubernetes, Azure Container Apps, Terraform)? No IaC files were found beyond Docker Compose.]

---

## 8. Known Issues / TODOs

Extracted from code comments:

| Location | Issue / TODO |
|---|---|
| `backend/agent/graph.py` | `# TODO: migrate Redis to an external service (e.g. Azure Cache for Redis, dedicated Redis container) so that memory persists across serverless backend instances.` |
| `backend/modules/LLMS.py` | `# TODO: add more providers here` — `azure` and `openai` entries in the model mapper are set to `None` and will raise `ValueError` if selected. |
| `backend/main.py` | `lifespan` context manager is commented out — application lifecycle management is incomplete. |
| `backend/main.py` | `_charts_sent` is a module-level set — it is never cleared and will grow unboundedly across sessions in a long-running process. |
| `backend/modules/assessment.py` | Specialist LLM token cap (`specialist_max_tokens: 1500`) was added to prevent runaway output; comment notes previous runs were hitting 2,772 tokens. |
| `.github/scripts/tool2_tech_docs.py` | File is truncated mid-function (`build_index` references `{r` — likely a copy/paste artifact). |
| `.github/scripts/tool1_code_review.py` | File is truncated — `review_pr` comment block and remaining functions are cut off. |
| `.github/scripts/tool5_uat.py` | `build_test_pack_csv` function is truncated. |
| General | No IaC (Terraform, Bicep, etc.) found — cloud deployment path is undefined. |
| General | No `requirements.txt` or `pyproject.toml` visible for the backend — dependency installation method outside Docker is unclear. |
| General | Database files (`*.db`) are mounted read-only but no seeding or generation script is evident in the provided files. |
| `backend/prompts/assessment_criterias.json` | File is truncated — full set of assessment categories for `deep` and `fast` modes is not visible. |