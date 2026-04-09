# Solution overview: Underwriting Chatbot
**Version:** 0.1.0 | **Date:** 2026-04-09 | **Status:** Draft

## Executive summary
Insurance underwriters today must manually review customer profiles, interpret complex risk data, and apply specialist knowledge across multiple risk categories — a time-consuming and inconsistent process. This solution provides an AI-powered assistant that helps underwriters quickly gather customer information, run structured risk assessments, and receive a clear, evidence-based recommendation — all through a simple chat interface. The business value is faster, more consistent underwriting decisions with a full audit trail of the reasoning applied.

## Business context
**Problem statement:** Underwriters must assess customer risk across multiple specialist domains (medical, financial, lifestyle, etc.) by manually reviewing records and applying judgement. This process is slow, subject to individual variation, and difficult to scale.

**Affected users / teams:** Insurance underwriters and their supervisors; potentially also compliance and risk teams who review underwriting decisions.

**Current pain points:** [TODO: what was the manual/legacy process? e.g. were underwriters reviewing paper files or a legacy system? How long did a typical assessment take? Was there inconsistency between underwriters?]

## What this solution does
The chatbot allows an underwriter to type a question or request in plain English — for example, "Show me the profile for customer CUST00000001" or "Run a risk assessment for this customer." Behind the scenes, the assistant:

1. **Looks up customer information** from internal databases covering personal details, application history, and predictive model scores.
2. **Runs a multi-specialist risk assessment** by sending the customer's profile to several focused AI analysts, each examining a different risk area (e.g. medical history, financial profile, lifestyle factors) in parallel. Their findings are then combined into a single structured report.
3. **Shows similar customers** ("lookalike analysis") so the underwriter can benchmark the current applicant against comparable historical cases — including charts showing how similar customers were classified.
4. **Produces a structured underwriting report** that includes a risk classification (Preferred, Standard Plus, Standard, or Substandard), a plain-English summary, the key factors driving the decision, and a list of follow-up actions required before a policy can be issued.
5. **Remembers the conversation** so the underwriter can ask follow-up questions within the same session without re-entering context.

## What it does NOT do (out of scope)
- **Does not make a final policy decision or bind coverage.** The chatbot produces a recommendation and a list of follow-up items; a human underwriter remains accountable for the final decision.
- **Does not write to customer records.** All database access is read-only; the chatbot cannot update, correct, or create customer profiles or application data.
- **Does not integrate with policy administration or CRM systems.** The chatbot reads from its own SQLite data files and does not connect to any wider insurance platform or external data provider.
- **Does not handle claims, policy renewals, or customer-facing interactions.** This tool is designed exclusively for internal underwriter use.
- **Does not provide regulatory or legal advice.** The assessment guidance is derived from internal underwriting documents only.

## Data handled
| Data type | Sensitivity | Retention | Storage location |
|---|---|---|---|
| Customer personal profile (age, income, employment, nationality, smoker status) | High — personally identifiable information | [TODO: retention policy] | Read-only SQLite database (`customer_profile.db`) |
| Insurance application details (coverage requested, risk classification, family medical history) | High — sensitive health and financial data | [TODO: retention policy] | Read-only SQLite database (`application_profile.db`) |
| Predictive model scores and outputs | Medium — internal model data | [TODO: retention policy] | Read-only SQLite database (`model_predictions.db`) |
| Feature importance data (model explainability) | Low — internal analytical data | [TODO: retention policy] | Read-only SQLite database (`feature_importance.db`) |
| Chat conversation history | Medium — contains underwriting queries and decisions | [TODO: retention policy] | PostgreSQL database |
| Conversation session state (in-progress conversations) | Medium | Short-term only (session duration) | Redis (in-memory cache) |
| Customer similarity mapping | Medium — derived analytical data | [TODO: retention policy] | Local JSON file (`customer_similarity_dict.json`) |

> ⚠️ **Note:** Data encryption at rest is not evidenced in the code for SQLite files or the Redis instance. This should be confirmed or addressed before go-live.

## Stakeholders
| Role | Name | Responsibility |
|---|---|---|
| Solution owner | [TODO] | Accountable for delivery and budget |
| Business sponsor | [TODO] | Strategic direction and sign-off |
| Tech lead | [TODO] | Technical decisions and architecture |
| Key users | [TODO: underwriting team] | Day-to-day usage and UAT feedback |
| Data / compliance lead | [TODO] | Data privacy, retention, and regulatory sign-off |

## Risks and dependencies
- **No disaster recovery identified.** Redis (used for conversation memory) is running in-memory with no evidenced backup or failover. If the service restarts, in-progress sessions would be lost. There is a code comment acknowledging this is a known gap.
- **Single-region deployment assumed.** No multi-region or high-availability configuration is visible in the codebase. An outage in the hosting environment would make the tool unavailable.
- **Static local data files.** Customer data is stored in local SQLite files. There is no automated refresh or synchronisation process evident — data may become stale.
- **Dependency on third-party AI providers.** The system relies on both Google Gemini and Anthropic Claude APIs. Any outage, pricing change, or policy change by these providers would directly affect availability and cost.
- **Sensitive data sent to external AI APIs.** Customer profiles containing personal and health-related information are sent to external LLM providers. This requires careful review against data protection regulations (e.g. GDPR, local insurance data rules).
- **Assessment criteria derived from internal documents.** The specialist assessment prompts are generated from Word documents in an `inputs/` folder. If those documents are outdated or incomplete, the quality of assessments will be affected.
- **No authentication or access control visible.** The API layer (`allow_origins=["*"]`) accepts requests from any origin. Role-based access control and user authentication are not evidenced in the codebase.
- **Skills-based architecture is incomplete (v1 is work-in-progress).** The current version (v0) loads all tools for every query, which the team has identified as inefficient. The improved version is not yet finished.

## Success metrics
[TODO: How will you measure if this solution is working? Suggested candidate metrics:]
- **Average time to produce an underwriting assessment** — compare pre- and post-deployment to measure efficiency gain.
- **Underwriter acceptance rate** — percentage of AI-generated recommendations that are accepted without material change by the underwriter.
- **Data completeness rate** — percentage of assessments completed without "data gap" flags, indicating data quality improvement over time.

## Go-live and milestones
[TODO: Target date and key milestones — e.g. UAT completion, security review sign-off, data privacy review, pilot with selected underwriters, full rollout]

---

## Gap Questionnaire
_These are the only items Claude could not determine from the code and IaC.
Please fill these in before the document is finalised._

1. Who owns this solution — which team or individual is accountable for its delivery, ongoing maintenance, and budget?
2. Which regulatory or data protection requirements apply to sending customer personal and health data to external AI providers (Google Gemini, Anthropic Claude)? Has a data privacy impact assessment been completed?
3. What is the target go-live date, and are there any hard deadlines driven by business or regulatory commitments?
4. How frequently is the underlying customer data updated, and what is the process for keeping the SQLite databases current?
5. What authentication and access control is planned — who should be permitted to use this tool, and how will that be enforced?
6. What are the data retention requirements for chat history (PostgreSQL) and customer data (SQLite files), particularly given the sensitivity of health and financial information?
7. Is the underwriter's acceptance or rejection of the AI recommendation being logged anywhere for audit or model improvement purposes?
8. What was the previous process for conducting underwriting assessments — how long did it take, and what systems were used?
9. Is there a disaster recovery or business continuity requirement? For example, if the service goes down, what is the acceptable maximum downtime?

---
_Draft auto-generated by AI Delivery Bot · 2026-04-09 09:56 UTC · Source: kylodeng/underwriting_chatbot-main v0.1.0_
