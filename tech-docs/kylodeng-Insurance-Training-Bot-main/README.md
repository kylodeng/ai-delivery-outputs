# Insurance Training Bot

## 1. Project Overview

Insurance Training Bot is a FastAPI-based conversational training system designed to help new insurance agents in Hong Kong master product knowledge and sales techniques. It operates in two modes: a **Teacher mode** for guided learning and **Roleplay mode** where the agent practices against a simulated customer with a randomly generated profile. The system uses a RAG (Retrieval-Augmented Generation) pipeline backed by real insurance product PDFs to ensure all product facts cited are grounded in source documents.

---

## 2. Tech Stack

| Component | Technology | Version/Notes |
|---|---|---|
| Backend framework | FastAPI | Python 3.13 (CI), 3.12 (workflow scripts) |
| Package manager | uv (astral-sh) | v3 (via GitHub Actions) |
| LLM routing | OpenRouter | Default model: `openai/gpt-oss-20b:free` |
| LLM client | LangChain / `langchain-openai` | `ChatOpenAI` wrapper |
| Agent framework | LangGraph | Via `langchain.agents` |
| Embeddings / Vector store | `core` library (local) | Supports ChromaDB, FAISS, Pinecone |
| PDF parsing | pdfplumber | Used in `core/chunker.py` |
| Document annotation | LLM-based (same model) | Cached to `.annot.json` sidecar files |
| Session persistence | JSON file (`data/sessions.json`) | Survives server restarts |
| HTTP client | httpx | SSL verification disabled (see Known Issues) |
| CI/CD | GitHub Actions | 5 AI-delivery workflow tools |
| AI delivery tools | Anthropic Claude | `claude-sonnet-4-6` |
| Email notifications | SendGrid | Via `SENDGRID_API_KEY` |
| Deployment target | Azure App Service | Two apps: API + Frontend |
| Frontend serving | FastAPI `StaticFiles` + Chainlit UI | Vite dev server on port 5173 |
| Environment config | python-dotenv | `.env` file |

---

## 3. Architecture

The system is composed of three main layers:

**Core RAG Library (`core/`):** PDFs under `data/Insurance-product-info/` are processed at ingest time — each PDF is annotated by an LLM (product name, doc type, page relevance), split into semantic chunks by `core/chunker.py`, and embedded into a vector store (Chroma, FAISS, or Pinecone, selected via environment variable). Annotation results are cached as `.annot.json` sidecar files to avoid repeated LLM calls.

**FastAPI Backend (`api/`):** On startup, the server loads the persisted vector store and session state. Incoming chat requests are routed to either the **Teacher agent** (streaming, multi-turn) or the **Assessor agent** (one-shot, post-roleplay) defined in `api/agent.py`. Both agents are LangGraph agents equipped with eight RAG tools (`api/rag_tools.py`) that query the vector store for product-specific information. The Roleplay customer persona is powered by the base LLM with no RAG tools. Sessions (including conversation history and customer profiles) are stored in `data/sessions.json`. PDF source files are served statically at `/docs/` so the frontend can link to them.

**GitHub Actions AI Delivery Tools (`.github/`):** Five independent workflow scripts run against this repository using Claude (`claude-sonnet-4-6`) via the Anthropic API to automate code review, technical documentation, business documentation, test generation, and UAT facilitation. Outputs are written to a separate `ai-delivery-outputs` GitHub repository.

```
PDFs (data/)
    └─► core/annotator  ─► .annot.json sidecar cache
    └─► core/chunker    ─► chunk dicts
    └─► core/ingest     ─► embed_chunks ─► Vector Store (Chroma/FAISS/Pinecone)

HTTP Request
    └─► FastAPI (api/main.py)
          ├─► Teacher Agent (LangGraph + 8 RAG tools) ─► StreamingResponse
          ├─► Assessor Agent (LangGraph + 8 RAG tools) ─► JSON response
          └─► Roleplay Customer (base LLM, no tools)  ─► StreamingResponse

GitHub Events ─► .github/workflows/ ─► .github/scripts/ ─► Claude API
                                                          └─► ai-delivery-outputs repo
```

---

## 4. Local Development Setup

1. **Clone the repository**

```bash
git clone https://github.com/kylodeng/Insurance-Training-Bot-main.git
cd Insurance-Training-Bot-main
```

2. **Install uv (if not already installed)**

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

3. **Install Python dependencies**

```bash
uv sync
```

4. **Create a `.env` file** (see [Environment Variables](#5-environment-variables) below)

```bash
cp .env.example .env   # if an example file exists, otherwise create manually
```

5. **Place insurance product PDFs** under `data/Insurance-product-info/` (subdirectories are supported).

6. **Ingest PDFs into the vector store**

```bash
uv run python -m core.ingest
```

   Or via the API endpoint after starting the server:

```bash
curl -X POST http://localhost:8000/ingest
```

7. **Start the FastAPI server**

```bash
uv run uvicorn api.main:app --reload --port 8000
```

8. **(Optional) Start the Chainlit / Vite frontend dev server**

   [TODO: What command starts the frontend? No frontend source files were provided.]

---

## 5. Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `API_KEY` | Yes | `""` | API key for the LLM provider (OpenRouter or Anthropic) |
| `OPENAI_URL_BASE` | No | `https://openrouter.ai/api/v1` | Base URL for the OpenAI-compatible LLM API |
| `OPENAI_MODEL` | No | `openai/gpt-oss-20b:free` | Model name passed to the LLM client |
| `SHOW_TOOL_CALLS` | No | `true` | Log and stream tool call events by default; overridable per-session in Chainlit UI |
| `ANTHROPIC_API_KEY` | Yes (CI only) | — | Anthropic API key used by the five GitHub Actions AI delivery tools |
| `GH_TOKEN` | Yes (CI only) | — | GitHub personal access token for reading repos and writing to `ai-delivery-outputs` |
| `SENDGRID_API_KEY` | Yes (CI only) | — | SendGrid API key for email notifications from CI workflows |
| `OUTPUT_REPO` | No (CI only) | `ai-delivery-outputs` | Name of the GitHub repository where CI tool outputs are written |
| `OUTPUT_REPO_OWNER` | No (CI only) | `GITHUB_REPOSITORY_OWNER` | GitHub owner of the output repo |
| `NOTIFY_EMAIL` | No (CI only) | `kylo.deng@capco.com` | Recipient email for CI workflow notifications |
| `SENDER_EMAIL` | No (CI only) | `kylo.deng@capco.com` | Sender email for CI workflow notifications |

> **Note:** The ingest pipeline also reads `API_KEY` and `OPENAI_URL_BASE` when building the annotation LLM. For annotation it defaults the model to `claude-sonnet-4-6` and base URL to `https://api.anthropic.com/v1` if those env vars are not set.

---

## 6. Running Tests

Tests are run with pytest via uv:

```bash
uv run pytest tests/ -v
```

This is the same command used by the `test` job in `.github/workflows/deploy.yml`. The CI pipeline runs on Python 3.13.

[TODO: Where does the `tests/` directory live and what frameworks/fixtures does it use? No test files were included in the provided source.]

---

## 7. Deployment

### CI/CD (GitHub Actions — automatic)

On every push to `main`, the `Test & Deploy` workflow (`.github/workflows/deploy.yml`) runs automatically:

1. Runs the full test suite (see above).
2. On success, deploys to **two Azure App Service** instances in parallel:
   - **API:** app name `training-bot-api`, using secret `AZURE_WEBAPP_PUBLISH_PROFILE_API`
   - **Frontend:** app name `training-bot-frontend`, using secret `AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND`

Dependencies are exported from uv before deployment:

```bash
uv export --no-dev --format requirements-txt -o requirements.txt
```

The Azure deployment action used is `azure/webapps-deploy@v3`.

### Manual / Local Ingest

To rebuild the vector store from PDFs:

```bash
uv run python -m core.ingest --pdf-dir data/Insurance-product-info --verbose
```

[TODO: What Azure App Service startup command is configured? E.g. `uvicorn api.main:app --host 0.0.0.0 --port 8000` — not specified in the provided files.]

### GitHub Actions Secrets Required for Deployment

| Secret | Purpose |
|---|---|
| `AZURE_WEBAPP_PUBLISH_PROFILE_API` | Publish profile for `training-bot-api` App Service |
| `AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND` | Publish profile for `training-bot-frontend` App Service |
| `ANTHROPIC_API_KEY` | Used by all five AI delivery workflow tools |
| `GH_TOKEN` | Used by all five AI delivery workflow tools |
| `SENDGRID_API_KEY` | Used by all five AI delivery workflow tools |

---

## 8. Known Issues / TODOs

| Location | Issue / TODO |
|---|---|
| `api/main.py` | SSL certificate verification is **disabled** (`verify=False`) on all httpx clients. This is a security risk in production. |
| `api/main.py` | `SHOW_TOOL_CALLS` print statement uses an f-string with embedded quotes — will raise a `SyntaxError` on Python < 3.12: `print(f"SHOW_TOOL_CALLS={os.getenv("SHOW_TOOL_CALLS"," ").lower()== "true"}")` |
| `api/agent.py` | `ASSESSOR_SYSTEM` prompt is truncated in the provided file — the full tool list for the assessor agent is cut off. |
| `core/annotator.py` | Comment `# custom annotation logic` suggests the `annotate_document` function body is incomplete in the provided file. |
| `core/chunker.py` | `split_by_words` function body is truncated in the provided file. |
| `core/ingest.py` | `--pdf-dir` argument default path is truncated in the provided file. |
| `.github/scripts/shared.py` | Email helper functions (`send_email`, `email_html`, `write_audit_entry`) are referenced by all tool scripts but their implementations are truncated/missing from the provided file. |
| `.github/scripts/tool1_code_review.py` | The `review_pr` function's PR comment body is truncated — the auto-generated-by footer is incomplete. |
| `.github/scripts/tool2_tech_docs.py` | `build_index` function references undefined variable `r` instead of `repo`. |
| `api/sessions.py` | `CustomerProfile.describe()` method is truncated in the provided file. |
| `tool5_uat.py` | `build_test_pack_csv` function body is truncated. |
| `data/Network_Hospitals_with_Cashless_Arrangement.pdf.annot.json` | Annotation file is truncated. |
| General | No disaster recovery, monitoring, or alerting configuration is present in any of the provided files. |
| General | Frontend source code is not present in the provided files — deployment to `training-bot-frontend` App Service is configured but the frontend technology and build process are unknown. [TODO: What framework is the frontend built with — Chainlit, Vite/React, or both?] |
| `api/sessions.py` | Escalation contacts are not defined — runbook placeholder: [TODO: fill in team contacts] |