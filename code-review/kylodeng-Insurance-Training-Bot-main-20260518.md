# Code Review Report
**Source:** kylodeng/Insurance-Training-Bot-main
**Context:** 20260518
**Generated:** 2026-05-18 11:42 UTC
**Score:** 62/100 | **Recommendation:** `REQUEST_CHANGES`

## Summary
The codebase implements a multi-tool AI delivery pipeline with reasonable structure, but contains several security concerns including hardcoded email addresses, missing error handling, and insufficient secret validation.

## Findings
| Severity | Category | File | Line | Issue | Recommendation |
|---|---|---|---|---|---|
| HIGH | security | `.github/scripts/shared.py` | 11 | Hardcoded personal email addresses are embedded directly in source code for NOTIFY_EMAIL and SENDER_EMAIL defaults, leaking PII into version control. | Remove all hardcoded email defaults from source code and require them exclusively via repository secrets or environment variables with no fallback. |
| HIGH | security | `.github/scripts/shared.py` | 8 | ANTHROPIC_API_KEY, GH_TOKEN, and SENDGRID_API_KEY are accessed with direct dict-style access which raises KeyError but provides no validation that the values are non-empty strings. | Add explicit non-empty validation for all secrets at startup and raise a descriptive error if any secret is missing or blank to prevent silent failures with empty credentials. |
| HIGH | security | `.github/workflows/tool1_code_review.yml` | 28 | Personal email address kylo.deng@capco.com is hardcoded in workflow environment variables and committed to the repository, exposing PII in version control history. | Replace all hardcoded email addresses in workflow YAML files with GitHub secrets such as secrets.NOTIFY_EMAIL and secrets.SENDER_EMAIL. |
| HIGH | security | `.github/workflows/tool2_tech_docs.yml` | 22 | Personal email address is again hardcoded in this workflow file, repeating the PII exposure pattern across multiple workflow definitions. | Centralise email configuration in repository-level secrets and reference them consistently across all workflow files. |
| HIGH | security | `.github/workflows/tool3_business_docs.yml` | 24 | Hardcoded personal email address appears again in tool3 workflow, indicating a systemic pattern of embedding PII across all workflow files. | Audit all five workflow files and replace every hardcoded email with a secrets reference, then rotate any email-based credentials that may have been exposed. |
| HIGH | security | `.github/workflows/deploy.yml` | 1 | The deploy workflow exposes Azure publish profiles via secrets but there is no environment protection rule or manual approval gate before deploying to production on push to main. | Add a GitHub environment with required reviewers for the deploy-api and deploy-frontend jobs to enforce manual approval before any production deployment. |
| MEDIUM | security | `.github/scripts/shared.py` | 23 | The Anthropic client is instantiated inside call_claude on every invocation, which could inadvertently log or expose the API key in verbose debug output or exception tracebacks. | Instantiate the Anthropic client once at module level after validating the key, and ensure exception handlers do not propagate raw exception messages containing credentials. |
| MEDIUM | correctness | `.github/scripts/shared.py` | 24 | call_claude has no error handling for API failures, rate limiting, network timeouts, or malformed responses, meaning any transient fault will crash the entire workflow. | Wrap the API call in a try-except block with retry logic for transient errors and explicit handling for anthropic.APIError to surface actionable failure messages. |
| MEDIUM | correctness | `.github/scripts/tool1_code_review.py` | 1 | The extract_json function is referenced but its full implementation is truncated in the provided code, making it impossible to verify robustness of JSON parsing from Claude responses. | Ensure extract_json wraps json.loads in a try-except catching json.JSONDecodeError and returns a structured error dict rather than raising an unhandled exception. |
| MEDIUM | security | `.github/workflows/tool1_code_review.yml` | 50 | The workflow uses pip install anthropic requests without pinned versions, which is a supply-chain risk allowing malicious package versions to be silently introduced. | Pin all dependency versions in the pip install commands or use a requirements file with hashed dependencies and verify integrity on each run. |
| MEDIUM | maintainability | `.github/workflows/tool1_code_review.yml` | 1 | The dependency installation command pip install anthropic requests is duplicated across all five workflow files with no shared reusable workflow or composite action. | Extract common setup steps into a reusable composite GitHub Action or a shared workflow to eliminate duplication and simplify future dependency updates. |
| MEDIUM | security | `.github/scripts/tool5_uat.py` | 1 | tool5_uat.py imports csv and io for processing test result sheets but there is no visible input validation or sanitisation of the CSV content before it is passed to Claude or written to outputs. | Validate and sanitise all CSV inputs before processing, enforcing maximum row counts and field lengths to prevent prompt injection via crafted test result files. |
| MEDIUM | performance | `.github/scripts/shared.py` | 18 | MODEL is hardcoded as claude-sonnet-4-6 with no environment variable override, meaning changing the model requires a code change and redeployment rather than a configuration update. | Make the model name configurable via an environment variable with the current value as a default so it can be updated without code changes. |
| LOW | maintainability | `.github/scripts/tool2_tech_docs.py` | 1 | The SYSTEM_ARCH docstring is visibly truncated mid-sentence in the provided code, suggesting incomplete implementation that may produce inconsistent architecture documents. | Complete the SYSTEM_ARCH prompt string and add a unit test that verifies all required system prompt sections are present before deployment. |
| LOW | maintainability | `.github/scripts/tool3_business_docs.py` | 1 | The SYSTEM prompt for business docs is truncated mid-sentence at the milestones section, indicating the full prompt is not captured in version control. | Store long prompt templates as separate text or YAML files in the repository to avoid accidental truncation and enable easier review and versioning. |
| LOW | maintainability | `.github/workflows/tool4_auto_testing.yml` | 1 | The TEST_MODE environment variable definition is visibly truncated at the end of the provided file content, suggesting the workflow is incomplete. | Ensure the complete workflow file is committed to the repository and add a linting step such as actionlint to validate all workflow files on every PR. |

## IaC Findings
- The deploy.yml workflow deploys to Azure App Service with no environment protection rules, meaning a direct push to main triggers an unreviewed production deployment.
- There are no resource tagging requirements visible in any IaC or workflow configuration, making cost attribution and governance difficult in the Azure environment.
- No health check or smoke test step exists after either Azure deployment job, so failed deployments may not be detected until user impact occurs.
- The Azure App Service deployment uses publish-profile authentication which is a long-lived credential; consider migrating to federated OIDC identity for keyless authentication.
- There is no rollback step or deployment slot swap strategy defined in deploy.yml, meaning a bad deployment to main has no automated recovery path.

## Positive Observations
- All sensitive credentials are sourced from GitHub secrets rather than hardcoded in the workflow job steps, which is the correct pattern for secret management.
- The deploy workflow correctly gates production deployment behind a passing test job using the needs directive.
- The clean_json utility function defensively handles Claude returning markdown-fenced JSON, improving robustness of AI response parsing.
- Workflow triggers are well-designed with pull_request, schedule, and workflow_dispatch options providing flexible automation coverage.
- The shared.py module correctly centralises all common utilities, reducing duplication across the five tool scripts.
- FORCE_JAVASCRIPT_ACTIONS_TO_NODE24 is set consistently across workflows, ensuring forward compatibility with GitHub Actions runtime changes.
- The code review system prompt includes explicit rules to prioritise hardcoded secrets and overly permissive IAM, showing good security awareness in the tool design.
- The UAT tool correctly separates generation and analysis modes with distinct system prompts, following a clean separation of concerns.
- Using uv for Python dependency management in the deploy workflow is a modern and performant choice compared to plain pip.
- The audit logging abstraction in shared.py provides a consistent mechanism to track all AI tool invocations for compliance purposes.

---
_Auto-generated by AI Delivery Bot (claude-sonnet-4-6)_
