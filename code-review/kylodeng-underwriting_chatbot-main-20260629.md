# Code Review Report
**Source:** kylodeng/underwriting_chatbot-main
**Context:** 20260629
**Generated:** 2026-06-29 12:41 UTC
**Score:** 62/100 | **Recommendation:** `REQUEST_CHANGES`

## Summary
The codebase implements a well-structured AI-powered delivery toolchain but contains several security concerns including hardcoded email addresses, missing error handling, and workflow security gaps that should be addressed before production use.

## Findings
| Severity | Category | File | Line | Issue | Recommendation |
|---|---|---|---|---|---|
| HIGH | security | `.github/scripts/shared.py` | 11 | Hardcoded personal email address kylo.deng@capco.com is embedded directly in source code as a default value for NOTIFY_EMAIL and SENDER_EMAIL. | Remove hardcoded email defaults and require these to be explicitly set as repository secrets or environment variables with no fallback default. |
| HIGH | security | `.github/workflows/tool1_code_review.yml` | 21 | The NOTIFY_EMAIL and SENDER_EMAIL values are hardcoded in every workflow YAML file, leaking a personal email address into version control. | Move email addresses to GitHub Actions secrets or organisation-level variables and reference them via secrets context instead of plaintext. |
| HIGH | security | `.github/workflows/tool5_uat.yml` | None | The on.create trigger fires for every branch and tag creation without any filtering, potentially exposing secrets to untrusted code on arbitrary branches. | Add a branch filter such as branches starting with release to restrict the trigger scope and prevent secret exposure on attacker-controlled branches. |
| HIGH | security | `.github/scripts/shared.py` | 7 | API keys are accessed via os.environ with hard bracket notation meaning a missing secret causes an unhandled KeyError that could expose environment state in logs. | Use os.environ.get with explicit error messages and raise a clear SystemExit or custom exception with a safe message when required secrets are absent. |
| HIGH | security | `.github/workflows/tool1_code_review.yml` | None | The pull_request trigger combined with a script that reads PR content and calls external APIs could be abused via pull request injection if PR titles or body content are passed unsanitised to Claude. | Use pull_request_target with explicit permission scoping or sanitise all PR-sourced inputs before passing them to external API calls. |
| MEDIUM | security | `.github/scripts/shared.py` | None | The GH_TOKEN secret is placed in a request header and the requests library is used without timeout parameters, risking token exposure in hung connections or verbose error logs. | Add explicit timeout values to all requests calls and ensure exception handlers do not log the full request headers containing the bearer token. |
| MEDIUM | correctness | `.github/scripts/shared.py` | None | The get_repo_files function signature is visible but the implementation is truncated, making it impossible to verify whether pagination, rate limiting, or error handling are implemented correctly. | Ensure the function handles GitHub API pagination, respects rate limit headers, and raises descriptive exceptions on non-200 responses. |
| MEDIUM | maintainability | `.github/scripts/shared.py` | 14 | The MODEL constant is hardcoded as claude-sonnet-4-6 with no environment variable override, making model version updates require a code change and redeployment. | Read the model name from an environment variable with the current value as a default to allow runtime overrides without code changes. |
| MEDIUM | security | `.github/workflows/tool3_business_docs.yml` | None | The workflow_dispatch input project_name is passed directly into the Python script via environment variable without input validation, enabling potential injection via the GitHub Actions UI. | Validate and sanitise workflow_dispatch inputs in the Python script before using them in file paths, API calls, or document content. |
| MEDIUM | correctness | `.github/scripts/tool5_uat.py` | None | The SYSTEM_ANALYSE prompt is truncated in the provided code, meaning the JSON structure returned by Claude is undefined and the downstream parsing logic may silently fail. | Ensure the complete prompt is present in the source file and add schema validation on the parsed JSON response before using any fields. |
| MEDIUM | performance | `.github/scripts/shared.py` | 23 | A new anthropic.Anthropic client is instantiated on every call_claude invocation, creating unnecessary overhead when multiple Claude calls are made within a single workflow run. | Instantiate the Anthropic client once at module level or use a module-level singleton to reuse the connection across multiple calls. |
| MEDIUM | maintainability | `.github/scripts/tool4_auto_testing.py` | None | The SYSTEM_GAP prompt is truncated in the provided code, making it impossible to verify the complete JSON schema expected from Claude responses. | Ensure no script files are truncated in version control and add a JSON schema validation step after parsing Claude responses in all tool scripts. |
| LOW | maintainability | `.github/workflows/tool1_code_review.yml` | 14 | The FORCE_JAVASCRIPT_ACTIONS_TO_NODE24 environment variable is set in every workflow but the workflows use only Python scripts, making this setting unnecessary. | Remove the FORCE_JAVASCRIPT_ACTIONS_TO_NODE24 variable from all workflows or document why it is needed if third-party JavaScript actions are in use. |
| LOW | maintainability | `.github/scripts/tool2_tech_docs.py` | None | The SYSTEM_ARCH prompt is truncated mid-sentence in the provided code, suggesting file truncation issues that could cause silent runtime failures. | Audit all script files for completeness and add a startup assertion or unit test that verifies required prompt constants are non-empty strings. |
| LOW | correctness | `.github/scripts/tool1_code_review.py` | None | The extract_json function body is truncated, so it is unclear whether it handles all edge cases such as nested JSON objects, escaped characters, or Claude refusing to respond. | Ensure extract_json includes fallback handling for complete parse failures and logs the raw Claude response at debug level before raising an exception. |

## IaC Findings
- All five workflows run on ubuntu-latest which is a floating label that can change the underlying OS version without notice, potentially breaking dependencies.
- No permissions block is defined at the job level in any workflow, meaning jobs inherit the default GITHUB_TOKEN permissions which may be broader than needed.
- pip install runs without pinned versions for anthropic and requests, allowing supply chain attacks via dependency version bumps.
- No concurrency group is configured on any workflow, allowing multiple simultaneous runs to race when writing to the output repository.
- The schedule triggers run on main branch code without a separate approval gate, meaning any merged code automatically gains scheduled execution rights.
- There is no step to verify the integrity of the checked-out code before passing it to an external AI API, risking data exfiltration of sensitive source files.
- No timeout-minutes is set on any job, allowing runaway Claude API calls or hung requests to consume GitHub Actions minutes indefinitely.
- The OUTPUT_REPO value is hardcoded as ai-delivery-outputs in every workflow env block rather than being a single organisation-level variable.

## Positive Observations
- Secrets are correctly sourced from GitHub Actions secrets context rather than hardcoded API keys in workflow files.
- A dedicated shared.py module centralises common utilities promoting DRY principles across all five tool scripts.
- The clean_json helper defensively strips markdown fences from Claude responses, showing awareness of LLM output variability.
- Workflow triggers are well-designed with multiple invocation modes including PR events, scheduled cron jobs, and manual dispatch.
- The Claude prompt engineering is thorough with explicit output schemas, strict rules, and severity classifications defined inline.
- Using fetch-depth 0 in the checkout step ensures full git history is available for accurate diff analysis.
- Output is written to a separate output repository rather than committing back to the source repo, reducing blast radius.
- The UAT tool supports both generation and analysis modes in a single script, demonstrating good reuse of shared infrastructure.
- The code review prompt explicitly prioritises the most impactful security issues such as hardcoded secrets and overly permissive IAM.
- Audit logging is abstracted into a shared write_audit_entry function providing consistent observability across all tools.

---
_Auto-generated by AI Delivery Bot (claude-sonnet-4-6)_
