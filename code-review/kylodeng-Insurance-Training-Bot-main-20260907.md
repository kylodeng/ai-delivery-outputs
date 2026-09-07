# Code Review Report
**Source:** kylodeng/Insurance-Training-Bot-main
**Context:** 20260907
**Generated:** 2026-09-07 13:57 UTC
**Score:** 62/100 | **Recommendation:** `REQUEST_CHANGES`

## Summary
The codebase implements a multi-tool AI-assisted delivery pipeline with generally sound structure, but contains several security and maintainability concerns including hardcoded email addresses, missing error handling, lack of input validation, and overly broad secret exposure in CI workflows.

## Findings
| Severity | Category | File | Line | Issue | Recommendation |
|---|---|---|---|---|---|
| HIGH | security | `.github/scripts/shared.py` | 11 | Hardcoded personal email addresses are embedded directly in source code making rotation or multi-user support require code changes. | Move NOTIFY_EMAIL and SENDER_EMAIL to required environment variables with no hardcoded defaults, or manage them via repository secrets. |
| HIGH | security | `.github/scripts/shared.py` | 7 | API keys are accessed via os.environ with bracket notation which raises KeyError at import time but provides no structured error message, and all three secrets are loaded unconditionally even when not all tools need them. | Load secrets lazily within the functions that need them, and use os.environ.get with explicit validation and a descriptive error message when they are absent. |
| HIGH | security | `.github/workflows/tool1_code_review.yml` | None | All three API secrets (ANTHROPIC_API_KEY, GH_TOKEN, SENDGRID_API_KEY) are exported as plain environment variables at the job level, making them accessible to every step including third-party actions. | Scope each secret to only the specific step that requires it by moving the env block to that step rather than the job level. |
| HIGH | security | `.github/workflows/tool2_tech_docs.yml` | None | Same broad secret exposure pattern as tool1 workflow applies to tool2, tool3, tool4, and tool5 workflows. | Apply step-level secret scoping across all workflow files to limit blast radius if a third-party action is compromised. |
| HIGH | security | `.github/scripts/tool5_uat.py` | None | CSV input from test results is passed to Claude without any sanitisation, creating a prompt-injection risk if test data contains adversarial content. | Sanitise or escape CSV content before embedding it into Claude prompts, and impose a maximum input length. |
| HIGH | correctness | `.github/scripts/tool1_code_review.py` | None | The extract_json function and Claude response parsing lack fallback handling, so a malformed Claude response will cause an unhandled exception and fail the entire workflow silently. | Wrap JSON parsing in a try-except block and return a structured error object so the workflow can post a meaningful failure comment on the PR. |
| MEDIUM | security | `.github/workflows/tool1_code_review.yml` | None | The GH_TOKEN secret is exposed to PR workflows triggered by pull_request events which could allow a forked PR to exfiltrate the token via a malicious workflow step. | Use pull_request_target with explicit permissions restriction, or replace GH_TOKEN with the built-in GITHUB_TOKEN scoped to read-only where possible. |
| MEDIUM | security | `.github/workflows/tool3_business_docs.yml` | None | The project_name and release_version workflow dispatch inputs are injected into shell commands via environment variables without quoting validation, creating potential shell injection if inputs contain special characters. | Validate that inputs match expected patterns such as alphanumeric and dots before using them, and always double-quote variable references in shell steps. |
| MEDIUM | maintainability | `.github/scripts/shared.py` | 16 | The MODEL constant is hardcoded to a specific Claude model version string, meaning model updates require a code change and redeployment. | Source the MODEL value from an environment variable with the current version as a documented default. |
| MEDIUM | maintainability | `.github/scripts/tool4_auto_testing.py` | None | The TEST_MODE environment variable reference in the workflow is cut off mid-expression suggesting the workflow YAML is incomplete or truncated. | Complete the environment variable expression and add a CI lint step such as actionlint to catch YAML syntax issues before merge. |
| MEDIUM | performance | `.github/scripts/shared.py` | 24 | A new Anthropic client is instantiated on every call to call_claude, which adds unnecessary object creation overhead for workflows that call Claude multiple times. | Instantiate the Anthropic client once at module level or use a module-level singleton pattern. |
| MEDIUM | correctness | `.github/scripts/tool2_tech_docs.py` | None | The SYSTEM_ARCH prompt is truncated mid-sentence at Mark un indicating the system prompt is incomplete, which will produce inconsistent Claude outputs. | Restore the complete system prompt text and add a unit test that validates prompt strings are non-empty and do not end mid-word. |
| MEDIUM | correctness | `.github/scripts/tool5_uat.py` | None | The SYSTEM_ANALYSE prompt JSON template is truncated mid-field at pass_rate which means the Claude instruction is incomplete and will produce unpredictable structured output. | Restore the complete prompt and add a startup assertion or test that all prompt constants are syntactically complete. |
| MEDIUM | security | `.github/scripts/shared.py` | None | The get_repo_files function fetches up to 20 files without any size limit per file, potentially allowing very large files to exhaust memory or inflate Claude API costs. | Add a per-file size cap such as 100KB and a total payload size limit before passing content to Claude. |
| LOW | maintainability | `.github/workflows/deploy.yml` | None | Both deploy-api and deploy-frontend jobs duplicate the checkout and uv setup steps with no shared composite action. | Extract common setup steps into a reusable composite action to reduce duplication and simplify future maintenance. |
| LOW | maintainability | `.github/scripts/shared.py` | None | The get_repo_files docstring is truncated mid-word at Fetch tex suggesting documentation is incomplete. | Complete all docstrings and enforce documentation completeness with a pre-commit hook such as pydocstyle. |
| LOW | iac | `.github/workflows/deploy.yml` | None | Azure App Service deployment uses a publish profile secret which is a long-lived credential rather than a short-lived federated identity token. | Replace publish profile authentication with Azure OIDC federated credentials using azure/login action to avoid storing long-lived secrets. |
| LOW | maintainability | `.github/workflows/tool1_code_review.yml` | None | Dependencies are installed with bare pip install anthropic requests without version pinning, which can cause non-reproducible workflow runs. | Pin exact versions in a requirements file or use a lockfile managed by uv to ensure reproducible installs. |

## IaC Findings
- Azure App Service deployments rely on long-lived publish profile credentials instead of short-lived OIDC tokens, increasing the blast radius of a secret leak.
- No explicit permissions block is defined on any workflow job, meaning jobs run with the default overly broad GITHUB_TOKEN permissions.
- There is no environment protection rule or manual approval gate configured before the deploy jobs, allowing any push to main to immediately trigger production deployment.
- Workflow runs do not pin third-party action versions to a commit SHA, making them vulnerable to tag-mutable supply chain attacks on actions such as astral-sh/setup-uv.
- No resource tagging strategy is visible in the deployment configuration, which will hinder cost allocation and compliance auditing on Azure.

## Positive Observations
- Secrets are correctly sourced from GitHub Actions secrets rather than hardcoded API keys in workflow files.
- The clean_json utility defensively strips markdown fences from Claude responses, showing awareness of LLM output variability.
- Workflows use fetch-depth 0 for PR diff access which is the correct approach for code review tooling.
- The five-tool separation of concerns is well-structured with a shared utility module avoiding code duplication.
- Workflow triggers use appropriate event types including pull_request, schedule, and workflow_dispatch giving good operational flexibility.
- The Claude prompt design is detailed and includes explicit output format constraints which improves reliability of structured responses.
- The deploy workflow correctly gates deployments behind passing tests using the needs dependency.
- Use of modern tooling such as uv for dependency management and Python 3.13 in the deploy workflow demonstrates up-to-date practices.

---
_Auto-generated by AI Delivery Bot (claude-sonnet-4-6)_
