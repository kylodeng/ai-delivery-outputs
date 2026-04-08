# Solution overview: Insurance Agent Training Bot
**Version:** 0.1.0 | **Date:** 2026-04-08 | **Status:** Draft

## Executive summary
Insurance agents currently face a gap between product knowledge and practical selling skills, and building that capability through traditional training is slow and inconsistent. This platform gives agents an always-available AI-powered training companion that teaches product knowledge, simulates realistic customer conversations, and assesses agent readiness — all grounded in the company's own insurance product documents. The business value is faster agent onboarding, more consistent product knowledge across the sales force, and reduced reliance on senior staff for one-to-one coaching.

## Business context
**Problem statement:** New and existing insurance agents need to learn complex product details and develop effective customer conversation skills. Traditional training methods (classroom sessions, shadowing, printed materials) are time-consuming, inconsistent in quality, and do not scale to large or distributed sales teams.

**Affected users / teams:** Insurance sales agents (primary users); training managers and team leaders who oversee agent development; potentially compliance teams who need to ensure agents present product information accurately.

**Current pain points:** [TODO: what was the manual/legacy process? e.g. printed product brochures, classroom role-plays, one-to-one coaching from senior agents?]

## What this solution does
The platform gives insurance agents a conversational AI training companion accessible through a web browser. It works in three distinct modes:

- **Teacher mode** — the agent can ask questions about any insurance product and receive clear, accurate answers drawn directly from official product documents. The AI coach also sets exercises, quizzes the agent, and gives guidance on how to have better conversations with customers.
- **Roleplay mode** — the AI plays the role of a realistic customer (with a generated profile, financial situation, and personality), allowing the agent to practise a full sales conversation in a safe environment before speaking to real customers.
- **Assessment mode** — after a roleplay session ends, the AI reviews how the agent performed and provides structured feedback on their knowledge accuracy and conversation technique.

All three modes draw exclusively from the company's own insurance product PDF documents, which are loaded into the system in advance. This means the AI never makes up product details — every answer is traceable to a source document.

Conversation history is saved automatically, so agents can return to previous sessions and continue where they left off.

## What it does NOT do (out of scope)
- **Does not submit or process real insurance applications or quotes** — this is a training tool only; no transactions are executed.
- **Does not connect to live policy administration or CRM systems** — agent performance data and training records are not written back to any HR or sales management platform.
- **Does not manage or enforce regulatory compliance** — it does not track mandatory CPD hours, licensing requirements, or regulatory exam readiness in a compliant way.
- **Does not support video or audio interaction** — the interface is text-based chat only; no voice roleplay or video assessment.
- **Does not automatically update product knowledge** — when product PDFs change, a manual re-ingestion step is required to update the system's knowledge base.

## Data handled
| Data type | Sensitivity | Retention | Storage location |
|---|---|---|---|
| Insurance product PDF documents | Low–Medium (commercially sensitive product details) | [TODO: define retention policy] | Local server directory (`data/Insurance-product-info/`) |
| Vectorised product knowledge (embeddings) | Low | [TODO] | Local ChromaDB index (`data/chroma_index/`) or FAISS/Pinecone depending on configuration |
| Agent conversation history (chat threads) | Medium (may contain agent names, practice scenarios) | [TODO: define retention policy] | Local SQLite database (Chainlit-managed) |
| Simulated customer profiles | Low (fully synthetic/randomly generated) | Session duration | In-memory, not persisted beyond session |
| API keys (LLM provider, embedding service) | High (credentials) | Indefinite while in use | Environment variables / `.env` file on server |

## Stakeholders
| Role | Name | Responsibility |
|---|---|---|
| Solution owner | [TODO] | Accountable for delivery and budget |
| Business sponsor | [TODO] | Strategic direction — defines training goals and success criteria |
| Tech lead | [TODO] | Technical decisions, deployment, and maintenance |
| Training manager / key users | [TODO] | Day-to-day usage; uploading new product PDFs; reviewing agent progress |
| Insurance agents | [TODO: confirm scope — all agents? new joiners only?] | Primary end users of the training platform |

## Risks and dependencies
- **Single-machine deployment risk:** The system appears to run entirely on a local server with no cloud hosting, load balancing, or failover. If the server goes down, training is unavailable.
- **No disaster recovery found:** There is no evidence of automated backup for the vector index or conversation history database. A server failure could result in loss of ingested knowledge and all agent chat history.
- **External API dependency:** The platform relies on third-party AI services (Anthropic/Claude for conversation, Voyage AI for document embeddings). Outages or pricing changes at these providers would directly impact availability and running costs.
- **SSL certificate handling:** The code explicitly disables SSL verification for HTTP calls to AI providers, which is a security risk in a production environment and suggests the system was built for a corporate network with a proxy. This should be resolved before wider deployment.
- **Manual knowledge base updates:** Product PDF changes require a manual technical re-ingestion process. There is no automated pipeline to detect and apply updates when product documents change.
- **No user authentication found:** The Chainlit frontend does not appear to have login/authentication configured, meaning anyone who can reach the URL could access the training bot and conversation history.
- **Sensitive credentials in environment files:** API keys are stored in a local `.env` file. There is no evidence of a secrets management solution (e.g. a vault or key management service).

## Success metrics
[TODO: confirm with business sponsor, but suggested candidates:]
1. **Agent knowledge accuracy rate** — percentage of assessment sessions where the agent scores above a defined threshold (e.g. 80% correct) before being signed off on a product.
2. **Time to competency** — average number of days from joining to first passing assessment, tracked before and after the platform's introduction.
3. **Platform adoption rate** — percentage of active agents completing at least one full roleplay or assessment session per week.

## Go-live and milestones
[TODO: Target date and key milestones — e.g. PDF ingestion sign-off, user acceptance testing with a pilot group of agents, full rollout]

---

## Gap Questionnaire
_These are the only items Claude could not determine from the code and IaC.
Please fill these in before the document is finalised._

1. Who are the named solution owner, business sponsor, and tech lead responsible for this platform, and who in the training or sales management team will administer it day-to-day?
2. What is the target go-live date, and are there any hard deadlines driven by a new product launch, regulatory requirement, or sales cycle?
3. Which group of agents is in scope for the initial rollout — all agents, new joiners only, a specific region or product line — and roughly how many users is that?
4. What was the previous training process for agents (e.g. classroom sessions, printed brochures, shadowing), and what specifically should this platform replace or supplement?
5. How should agent performance and training completion be tracked — does the business need the platform to report results into an existing HR, LMS, or CRM system?
6. Is this platform intended to run on a company-managed server on the internal network, or should it be hosted in the cloud and accessible externally? What are the IT security and data residency requirements?
7. How often do insurance product documents change, and who is responsible for keeping the knowledge base up to date when they do?
8. Are there regulatory or compliance requirements around AI-generated training content — for example, does any output need to be reviewed or approved before agents rely on it?
9. What are the data retention requirements for agent conversation history, and should agents be able to see each other's sessions, or is each agent's history private?

---
_Draft auto-generated by AI Delivery Bot · 2026-04-08 05:49 UTC · Source: kylodeng/Insurance-Training-Bot-main v0.1.0_
