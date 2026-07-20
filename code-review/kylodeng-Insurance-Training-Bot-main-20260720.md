# Code Review Report
**Source:** kylodeng/Insurance-Training-Bot-main
**Context:** 20260720
**Generated:** 2026-07-20 10:49 UTC
**Score:** 58/100 | **Recommendation:** `REQUEST_CHANGES`

## Summary
The repository implements a multi-tool AI delivery pipeline with generally sound structure, but contains hardcoded email addresses, missing error handling, overly broad secret exposure in workflows, and no dependency pinning, warranting changes before merge.

## Findings
| Severity | Category | File | Line | Issue | Recommendation |
|---|---|---|---|---|---|
| HIGH | security | `.github/scripts/shared.py` | 10 | Hardcoded personal email addresses (kylo.deng@capco.com) are embedded directly in source code as default values for NOTIFY_EMAIL and SENDER_EMAIL. | Remove hardcoded email defaults and require them to be supplied exclusively via environment variables or GitHub secrets with no fallback. |
| HIGH | security | `.github/workflows/tool1_code_review.yml` | 19 | The NOTIFY_EMAIL and SENDER_EMAIL values are hardcoded in plaintext in the workflow env block across all five workflow files, exposing a personal email address in version control. | Move email addresses to GitHub repository secrets or organisation variables and reference them via secrets context instead of hardcoding them. |
| HIGH | security | `.github/scripts/shared.py` | 7 | API keys (ANTHROPIC_API_KEY, GH_TOKEN, SENDGRID_API_KEY) are assigned at module level via direct dictionary access, causing an unhandled KeyError crash that may leak partial environment state in logs. | Use os.environ.get with explicit validation and raise a clear descriptive error if required secrets are absent, to avoid exposing stack traces. |
| HIGH | security | `.github/workflows/tool1_code_review.yml` | 13 | All three sensitive API keys (ANTHROPIC_API_KEY, GH_TOKEN, SENDGRID_API_KEY) are injected as environment variables at the job level, making them available to every step including third-party actions. | Scope secret environment variables to only the specific step that requires them to minimise the blast radius of a compromised action. |
| HIGH | security | `.github/workflows/tool5_uat.py` | None | The tool5_uat.py script imports base64 and csv but processes external CSV data (completed test sheets) without any input validation or size limits, creating potential for resource exhaustion. | Add input validation, maximum file size checks, and sanitise all CSV fields before processing to prevent malformed input attacks. |
| HIGH | correctness | `.github/scripts/tool1_code_review.py` | None | The extract_json function is referenced but its implementation is truncated in the provided code, meaning the actual JSON extraction logic cannot be verified for correctness or safety. | Ensure the full implementation is present and add defensive checks for malformed or oversized Claude responses before attempting JSON parsing. |
| MEDIUM | security | `.github/workflows/deploy.yml` | None | The deploy workflow uses actions/checkout@v4 and other actions without pinning to specific commit SHAs, making the pipeline vulnerable to tag-mutable supply chain attacks. | Pin all GitHub Actions to their full commit SHA rather than mutable version tags (e.g. actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683). |
| MEDIUM | security | `.github/workflows/tool1_code_review.yml` | None | The GITHUB_RUN_URL env variable is constructed from github.server_url and github.repository which could be attacker-controlled in a fork pull request context. | Restrict the pull_request trigger to trusted contributors only or use pull_request_target with explicit permission checks to prevent privilege escalation from forks. |
| MEDIUM | maintainability | `.github/scripts/shared.py` | 15 | The MODEL constant is hardcoded as claude-sonnet-4-6 which will require a code change to update when a new model version is needed. | Move the model name to an environment variable with the current value as the default so it can be updated without code changes. |
| MEDIUM | correctness | `.github/scripts/shared.py` | None | The get_repo_files function description is truncated and the full implementation is missing, making it impossible to verify file fetching logic, error handling, or rate limit management. | Ensure complete implementations are committed and add explicit handling for GitHub API rate limits and HTTP error responses. |
| MEDIUM | performance | `.github/scripts/shared.py` | 24 | A new anthropic.Anthropic client is instantiated on every call to call_claude, creating unnecessary connection overhead in workflows that make multiple sequential Claude calls. | Instantiate the Anthropic client once at module level or pass it as a parameter to avoid repeated initialisation costs. |
| MEDIUM | correctness | `.github/scripts/shared.py` | 24 | The call_claude function has no error handling for API failures, rate limit errors, or network timeouts, causing unhandled exceptions to propagate and fail the entire workflow silently. | Add try-except blocks with retry logic for transient errors and explicit handling for anthropic.APIError and anthropic.RateLimitError. |
| MEDIUM | security | `.github/workflows/tool3_business_docs.yml` | None | The workflow_dispatch inputs for project_name and release_version are used directly in shell commands without sanitisation, creating potential for shell injection via malicious input values. | Validate and sanitise workflow dispatch inputs before using them in shell commands, and prefer passing them as environment variables rather than inline shell interpolation. |
| LOW | maintainability | `.github/workflows/tool4_auto_testing.yml` | None | The TEST_MODE environment variable definition appears to be truncated mid-value in the workflow file, suggesting incomplete code was committed. | Review and complete the truncated workflow definition to ensure the default value fallback logic is correctly implemented. |
| LOW | maintainability | `.github/scripts/tool2_tech_docs.py` | None | The SYSTEM_ARCH prompt string is truncated mid-sentence in the provided code, which would cause incomplete instructions to be sent to Claude. | Ensure the full system prompt is present in the committed file and add a unit test that validates prompt strings are non-empty and complete. |
| LOW | maintainability | `.github/workflows/deploy.yml` | None | Both deploy-api and deploy-frontend jobs duplicate the checkout, uv setup, and requirements generation steps with no reuse mechanism. | Extract the shared setup steps into a reusable composite action or use a matrix strategy to reduce duplication and maintenance burden. |

## IaC Findings
- The deploy.yml deploys to Azure App Service but there is no infrastructure-as-code present in the repository to define or version the App Service configuration, scaling rules, or networking.
- No environment separation (dev, staging, prod) is evident in the deployment workflow, meaning every push to main deploys directly to what appears to be a production service.
- The Azure webapp deploy action uses publish-profile authentication which embeds credentials rather than using federated identity (OIDC) with Azure Workload Identity, which is the current security best practice.
- There are no resource tags defined for the Azure resources, making cost allocation, governance, and automated policy enforcement impossible.
- No health check or smoke test step exists after deployment to validate the deployed application is functioning before the workflow completes successfully.
- There is no rollback mechanism defined in the deployment workflow if post-deployment validation fails.

## Positive Observations
- Secrets are correctly stored in GitHub Actions secrets and never hardcoded as raw values for API keys.
- The shared.py utility module promotes good code reuse across all five tool scripts.
- The clean_json helper defensively strips markdown fences from Claude responses, handling a known LLM formatting issue.
- Workflow triggers are well-designed with multiple modes including schedule, PR, and manual dispatch for operational flexibility.
- The Claude prompt in tool1 enforces strict output schema rules including severity levels and categories, reducing hallucination risk.
- The deploy workflow correctly gates deployment jobs on the test job passing via the needs dependency.
- Tool4 and Tool5 correctly instruct Claude to use mocks for external services rather than real calls in generated tests.
- The UAT tool implements a two-mode architecture (generate and analyse) which is a sound separation of concerns.

---
_Auto-generated by AI Delivery Bot (claude-sonnet-4-6)_
