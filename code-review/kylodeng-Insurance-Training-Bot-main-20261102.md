# Code Review Report
**Source:** kylodeng/Insurance-Training-Bot-main
**Context:** 20261102
**Generated:** 2026-11-02 17:47 UTC
**Score:** 62/100 | **Recommendation:** `REQUEST_CHANGES`

## Summary
The codebase implements a multi-tool AI-powered delivery workflow system with reasonable structure but contains several security, maintainability, and correctness concerns that should be addressed before broader adoption.

## Findings
| Severity | Category | File | Line | Issue | Recommendation |
|---|---|---|---|---|---|
| HIGH | security | `.github/scripts/shared.py` | 10 | Hardcoded fallback email addresses for NOTIFY_EMAIL and SENDER_EMAIL expose internal contact information directly in source code. | Remove hardcoded email defaults and require these values to be supplied exclusively via environment variables or secrets. |
| HIGH | security | `.github/workflows/tool1_code_review.yml` | 22 | The NOTIFY_EMAIL and SENDER_EMAIL values are hardcoded as plaintext in every workflow YAML file, leaking an internal email address in the public repository. | Move email addresses to GitHub Actions secrets or organisation-level variables and reference them via the secrets context. |
| HIGH | security | `.github/scripts/shared.py` | 8 | API keys are accessed with os.environ bracket notation which raises an unhandled KeyError at import time if secrets are missing, potentially exposing partial stack traces in CI logs. | Use os.environ.get with explicit validation and a clear error message, or validate all required secrets at startup before use. |
| HIGH | security | `.github/workflows/tool1_code_review.yml` | 19 | ANTHROPIC_API_KEY, GH_TOKEN, and SENDGRID_API_KEY are passed as plain environment variables at the job level, making them available to all steps including any third-party actions. | Scope secrets to only the specific steps that require them to limit the blast radius of a compromised third-party action. |
| HIGH | security | `.github/workflows/tool1_code_review.yml` | 57 | The PR_NUMBER environment variable is set directly from github.event.pull_request.number without sanitisation, creating a potential injection vector in the shell run step. | Validate that PR_NUMBER is a pure integer before setting it in the environment, or use GitHub Actions expression contexts safely. |
| MEDIUM | security | `.github/scripts/tool5_uat.py` | None | The script imports csv and io for processing test result sheets but no input validation or size limits are applied to uploaded CSV content before parsing. | Add input validation, maximum file size checks, and sanitise CSV fields before passing them to Claude or writing to output repositories. |
| MEDIUM | security | `.github/workflows/tool3_business_docs.yml` | 38 | The project_name workflow dispatch input is passed directly into an environment variable and subsequently into prompts without sanitisation, risking prompt injection attacks. | Sanitise all user-supplied workflow dispatch inputs by stripping special characters before interpolating them into Claude prompts. |
| MEDIUM | correctness | `.github/scripts/shared.py` | None | The get_repo_files function docstring is truncated mid-sentence indicating the file was cut off, meaning reviewers cannot verify its full behaviour or error handling. | Ensure the complete source of shared.py is committed and review the function for missing error handling on GitHub API responses. |
| MEDIUM | correctness | `.github/scripts/tool1_code_review.py` | None | The extract_json function body is truncated, so it is impossible to verify whether it handles all Claude response variants or has bare except clauses. | Commit the complete file and ensure extract_json catches specific exceptions rather than using bare or broad except clauses. |
| MEDIUM | correctness | `.github/scripts/tool2_tech_docs.py` | None | The SYSTEM_ARCH prompt string is truncated mid-word, suggesting the committed file is incomplete and the workflow may fail at runtime. | Commit the complete script and add a CI step that lints Python files to catch syntax errors before deployment. |
| MEDIUM | correctness | `.github/scripts/tool5_uat.py` | None | The SYSTEM_ANALYSE JSON schema string is truncated, meaning the Claude prompt is malformed and will produce unpredictable or broken JSON responses at runtime. | Commit the complete prompt string and add a unit test that validates all system prompt constants are syntactically complete. |
| MEDIUM | correctness | `.github/workflows/tool4_auto_testing.yml` | None | The TEST_MODE environment variable expression is truncated suggesting the workflow YAML is malformed and will fail to parse in GitHub Actions. | Commit the complete workflow file and validate all YAML files with a linter such as actionlint in CI. |
| MEDIUM | maintainability | `.github/scripts/shared.py` | 17 | The MODEL constant is hardcoded as a specific Claude model version string, meaning all five tools must be updated manually when the model is deprecated. | Move the model identifier to an environment variable or a single configuration file so it can be updated without touching source code. |
| MEDIUM | performance | `.github/scripts/shared.py` | 26 | A new Anthropic client is instantiated on every call to call_claude rather than being shared as a module-level singleton, causing unnecessary overhead at scale. | Instantiate the Anthropic client once at module level and reuse it across all calls to call_claude. |
| MEDIUM | correctness | `.github/scripts/shared.py` | 34 | The clean_json function assumes Claude wraps JSON in a single code fence and uses rsplit with no validation, which will silently corrupt responses containing legitimate backticks. | Use a regex to reliably extract the content between the outermost code fences and raise a clear error if extraction fails. |
| LOW | maintainability | `.github/workflows/tool1_code_review.yml` | 1 | NOTIFY_EMAIL and SENDER_EMAIL are duplicated verbatim across at least five workflow files creating a maintenance burden when contact details change. | Define shared email values in GitHub Actions organisation variables and reference them centrally to avoid duplication. |
| LOW | maintainability | `.github/workflows/deploy.yml` | None | The deploy workflow has no environment protection rules or manual approval gates before deploying to Azure App Service on every push to main. | Add a GitHub Actions environment with required reviewers for the deploy-api and deploy-frontend jobs to prevent accidental production deployments. |
| LOW | iac | `.github/workflows/deploy.yml` | 38 | The Azure webapps-deploy action does not specify a deployment slot, meaning every push deploys directly to the production slot with no blue-green or canary capability. | Configure a staging slot for initial deployment and add a slot swap step after smoke tests pass to enable zero-downtime releases. |
| LOW | iac | `.github/workflows/deploy.yml` | None | There is no step to verify the Azure App Service health endpoint after deployment, so a broken deployment would not be detected until a user reports it. | Add a post-deployment smoke test step that polls the application health endpoint and fails the workflow if it does not return HTTP 200. |

## IaC Findings
- Azure App Service deployments in deploy.yml do not specify a deployment slot, deploying directly to production on every merge.
- No infrastructure-as-code files such as Bicep, Terraform, or ARM templates are present in the repository meaning infrastructure drift cannot be detected.
- The GitHub Actions workflows do not pin third-party actions such as azure/webapps-deploy to a specific SHA, creating a supply chain risk if the action tag is moved.
- There is no explicit permissions block in any workflow file, so jobs run with default broad repository permissions rather than least-privilege.
- No resource tagging strategy is enforced for Azure resources making cost attribution and environment identification difficult.

## Positive Observations
- Secrets are correctly stored as GitHub Actions secrets rather than hardcoded values for API keys.
- The deploy workflow correctly gates both deploy jobs on the test job passing before proceeding.
- The call_claude function enforces a configurable max_tokens limit preventing runaway API costs.
- Workflow triggers are well-designed with support for push, schedule, and manual dispatch modes covering realistic operational needs.
- The clean_json utility centralises Claude response normalisation rather than duplicating stripping logic in every tool script.
- The use of uv for dependency management and lock files in the deploy workflow promotes reproducible builds.
- Tool scripts share a common interface through shared.py promoting consistency and reducing duplication across all five tools.
- The SYSTEM prompts include explicit output format constraints which reduce the risk of malformed Claude responses.

---
_Auto-generated by AI Delivery Bot (claude-sonnet-4-6)_
