# Code Review Report
**Source:** kylodeng/underwriting_chatbot-main
**Context:** 20260803
**Generated:** 2026-08-03 11:36 UTC
**Score:** 62/100 | **Recommendation:** `REQUEST_CHANGES`

## Summary
The codebase implements a multi-tool AI delivery pipeline using Claude and GitHub Actions with generally good structure, but contains several security and maintainability issues that should be addressed before broader adoption.

## Findings
| Severity | Category | File | Line | Issue | Recommendation |
|---|---|---|---|---|---|
| HIGH | security | `.github/scripts/shared.py` | 10 | Hardcoded fallback email address kylo.deng@capco.com is embedded directly in source code, exposing a real persons email in version history. | Remove all hardcoded email defaults and require them to be set exclusively via environment variables or secrets with no fallback. |
| HIGH | security | `.github/scripts/shared.py` | 7 | ANTHROPIC_API_KEY and other secrets are read at module import time with direct dict access, causing an unhandled KeyError crash that may leak environment variable names in CI logs. | Use os.environ.get with explicit error handling that raises a descriptive exception without exposing secret names or values in stack traces. |
| HIGH | security | `.github/workflows/tool1_code_review.yml` | 19 | NOTIFY_EMAIL and SENDER_EMAIL are hardcoded PII values committed directly in the workflow YAML file visible to anyone with repository read access. | Move all email addresses to GitHub Actions secrets or repository variables and reference them via secrets context instead of plaintext env values. |
| HIGH | security | `.github/workflows/tool5_uat.yml` | None | The create event trigger fires on any branch or tag creation, meaning any contributor can trigger the UAT workflow and potentially exfiltrate repository content via Claude API calls. | Add a branch filter condition such as startsWith(github.ref, refs/heads/release) and restrict workflow permissions using the permissions key. |
| HIGH | security | `.github/scripts/tool5_uat.py` | None | UAT results are read from a user-supplied CSV path passed via workflow_dispatch input, creating a path traversal risk if the path is not validated before use. | Validate and sanitise the uat_results_path input against an allowlist pattern such as uat/owner/repo/version before constructing any file or API request path. |
| HIGH | security | `.github/scripts/shared.py` | 17 | GH_HEADERS is constructed as a module-level global containing the live Bearer token, meaning any import of shared.py immediately captures the token in memory for the process lifetime. | Construct authorization headers lazily inside functions rather than at module level to reduce the token exposure window. |
| MEDIUM | security | `.github/workflows/tool1_code_review.yml` | None | No explicit permissions block is defined on the workflow or job, so the GITHUB_TOKEN inherits the repository default which may be write-all. | Add a top-level permissions block with least-privilege values such as contents read and pull-requests write, scoped to only what each tool actually needs. |
| MEDIUM | security | `.github/workflows/tool2_tech_docs.yml` | None | No permissions block is defined, and the workflow triggers on every push to main including potentially untrusted content that gets sent verbatim to the Claude API. | Add explicit permissions, and consider sanitising or truncating repository file content before forwarding it to external AI APIs. |
| MEDIUM | security | `.github/workflows/tool3_business_docs.yml` | None | The workflow_dispatch inputs project_name and release_version are passed directly to the Python script via environment variables with no input sanitisation. | Validate workflow_dispatch inputs in the shell step using a regex allowlist before setting them as environment variables consumed by the script. |
| MEDIUM | maintainability | `.github/scripts/shared.py` | None | The shared module is truncated mid-function in the provided code, indicating incomplete implementation that could cause import errors at runtime. | Ensure all function definitions are complete and add integration tests that import shared.py to catch truncation or syntax errors in CI. |
| MEDIUM | maintainability | `.github/scripts/tool1_code_review.py` | None | The extract_json function and the rest of tool1_code_review.py are truncated mid-implementation, leaving undefined behaviour for the code review workflow. | Complete all function implementations and enforce a lint and syntax check step in CI that runs on all scripts under .github/scripts. |
| MEDIUM | correctness | `.github/scripts/shared.py` | 35 | clean_json splits on the first newline after the opening fence but does not handle the case where Claude returns a language tag such as json on the fence line, which could leave the tag in the output. | Use a regular expression such as re.sub to strip the entire opening fence line including any language identifier before parsing the JSON. |
| MEDIUM | performance | `.github/scripts/shared.py` | 24 | A new anthropic.Anthropic client is instantiated on every call_claude invocation, creating unnecessary object allocation and connection overhead for workflows that call Claude multiple times. | Create the Anthropic client once at module level or pass it as a parameter to call_claude to enable connection reuse across calls. |
| MEDIUM | correctness | `.github/scripts/tool5_uat.py` | None | The SYSTEM_ANALYSE prompt is truncated mid-JSON-schema definition, meaning the Claude prompt is incomplete and will produce unpredictable or malformed responses. | Complete the SYSTEM_ANALYSE prompt with the full expected JSON schema and add a CI step that validates all prompt strings are non-empty and well-formed. |
| MEDIUM | correctness | `.github/scripts/tool4_auto_testing.py` | None | The SYSTEM_GAP prompt string is truncated mid-object definition, leaving the missing_cases schema incomplete and causing Claude to return unpredictable JSON structures. | Complete the SYSTEM_GAP prompt definition and add a unit test that asserts all SYSTEM prompt constants are non-empty strings of sufficient length. |
| MEDIUM | maintainability | `.github/scripts/shared.py` | 14 | MODEL is hardcoded as the string claude-sonnet-4-6 with no environment variable override, making it impossible to change the model without a code commit. | Read MODEL from an environment variable with the current value as a default so it can be overridden per workflow run without code changes. |
| LOW | maintainability | `.github/scripts/tool2_tech_docs.py` | None | SYSTEM_ARCH is truncated mid-sentence, meaning the architecture documentation prompt is incomplete and will produce inconsistent outputs from Claude. | Complete all system prompt constants and add a pre-flight check in each script that asserts required prompts are fully defined before calling the Claude API. |
| LOW | maintainability | `.github/workflows/tool4_auto_testing.yml` | None | The pip install step has no version pinning for anthropic or requests, meaning dependency updates can silently break workflows. | Pin all dependencies to exact versions in a requirements.txt file and reference it with pip install -r requirements.txt in all workflow steps. |
| LOW | maintainability | `.github/scripts/tool3_business_docs.py` | None | The SYSTEM prompt string is truncated mid-section, leaving the go-live and milestones section of the business document template undefined. | Complete all prompt templates and store them in separate text or YAML files that are easier to review, version, and test independently of Python logic. |
| LOW | security | `.github/workflows/tool1_code_review.yml` | None | FORCE_JAVASCRIPT_ACTIONS_TO_NODE24 is set as a workflow env var but this is not a recognised GitHub Actions variable and has no documented effect. | Remove the undocumented environment variable to avoid confusion, or document its intended purpose with a comment in the workflow file. |

## IaC Findings
- No permissions block is defined on any of the five workflow files, meaning all jobs run with the default GITHUB_TOKEN permission scope which may be write-all depending on repository settings.
- The tool5_uat.yml create trigger has no branch or ref filter, allowing the workflow to fire on creation of any branch or tag including those created by bots or external contributors.
- No concurrency groups are configured on any workflow, allowing multiple simultaneous runs to write to the output repo concurrently and potentially corrupt output files.
- No timeout-minutes is set on any job, meaning a hung Claude API call or network issue could consume GitHub Actions minutes indefinitely.
- All five workflows use ubuntu-latest as the runner image with no pinned version, meaning the execution environment can change unexpectedly when GitHub updates the label.
- There is no artifact retention or output repo access control defined, meaning generated documents including potentially sensitive architecture details are accessible to anyone with output repo read access.
- No environment protection rules or required reviewers are configured for production-facing workflows such as tool3 which triggers on release tags.

## Positive Observations
- Secrets are correctly sourced from GitHub Actions secrets context rather than being hardcoded directly in workflow files for API keys.
- A dedicated shared.py utility module avoids code duplication across the five tool scripts and centralises API client configuration.
- Workflow triggers are appropriately scoped with path filters and schedule crons to avoid unnecessary CI runs.
- The Claude prompt templates enforce structured JSON output with explicit schema definitions, reducing parsing fragility.
- clean_json helper defensively strips markdown fences from Claude responses, handling a common LLM output formatting issue.
- Separate workflow files per tool provide clear separation of concerns and independent trigger configurations.
- The UAT tool implements a two-mode design with generate and analyse modes, demonstrating thoughtful workflow design.
- Using actions/checkout at v4 and actions/setup-python at v5 keeps action versions reasonably current.

---
_Auto-generated by AI Delivery Bot (claude-sonnet-4-6)_
