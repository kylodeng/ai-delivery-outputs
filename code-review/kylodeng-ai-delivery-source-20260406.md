# Code Review Report
**Source:** kylodeng/ai-delivery-source
**Context:** 20260406
**Generated:** 2026-04-06 09:06 UTC
**Score:** 62/100 | **Recommendation:** `REQUEST_CHANGES`

## Summary
Well-structured multi-tool AI delivery pipeline with good separation of concerns, but contains several security and maintainability issues that should be addressed before production use.

## Findings
| Severity | Category | File | Line | Issue | Recommendation |
|---|---|---|---|---|---|
| HIGH | security | `.github/workflows/tool1_code_review.yml` | None | Hardcoded personal email address kylo.deng@capco.com appears in all five workflow files as NOTIFY_EMAIL, embedding a real individuals contact details in version-controlled source code. | Replace hardcoded email addresses with GitHub Actions secrets or organisation-level variables such as secrets.NOTIFY_EMAIL. |
| HIGH | security | `.github/scripts/shared.py` | 16 | NOTIFY_EMAIL and SENDER_EMAIL fall back to a hardcoded personal email address in the Python source code if environment variables are not set. | Remove the hardcoded fallback email values and require the variables to be explicitly set, raising an error if they are absent. |
| HIGH | security | `.github/scripts/shared.py` | 10 | ANTHROPIC_API_KEY, GH_TOKEN, and SENDGRID_API_KEY are accessed via os.environ with direct key access, which will raise an unhandled KeyError and may leak partial environment context in CI logs if not set. | Use os.environ.get with explicit validation and a clear error message, or use a secrets management library that prevents accidental logging of missing keys. |
| HIGH | security | `.github/workflows/tool5_uat.yml` | None | The on.create trigger fires for every branch and tag creation, not just release branches, which could cause unintended UAT pack generation and unnecessary Claude API calls triggered by any contributor. | Replace the bare on.create trigger with a push trigger filtered to branches matching release/* or use a workflow_dispatch only approach for UAT generation. |
| HIGH | security | `.github/workflows/tool1_code_review.yml` | None | The GH_TOKEN secret is passed as a plain environment variable to all workflow steps, granting the script broad GitHub API access without least-privilege scoping. | Scope the GH_TOKEN permissions block in each workflow using the permissions key to grant only the minimum required scopes such as pull-requests write and contents read. |
| MEDIUM | security | `.github/scripts/tool5_uat.py` | None | User-supplied uat_results_path input is read from workflow inputs and used to fetch file content from the output repo without path traversal or directory escape validation. | Validate the uat_results_path input against an allowlist pattern such as a regex anchored to uat/ prefix before using it in any API call. |
| MEDIUM | security | `.github/scripts/tool5_uat.py` | None | User-provided user_stories content from workflow_dispatch inputs is passed directly into the Claude prompt without sanitisation, creating a prompt injection risk. | Sanitise or clearly delimit user-supplied content in the prompt using explicit boundary markers and limit the length of accepted input. |
| MEDIUM | correctness | `.github/scripts/tool1_code_review.py` | None | The extract_json function comment says it handles common formatting issues but the implementation is truncated in the provided code, making its actual robustness unverifiable. | Ensure the full implementation is present and add a fallback that logs the raw Claude response before raising a parse error to aid debugging. |
| MEDIUM | correctness | `.github/scripts/shared.py` | None | The get_repo_files function docstring is truncated mid-sentence, indicating the implementation may also be incomplete or cut off. | Verify the full function body is committed and add a unit test that confirms the function correctly handles pagination and extension filtering. |
| MEDIUM | maintainability | `.github/scripts/tool2_tech_docs.py` | None | The SYSTEM_ARCH prompt string is truncated mid-sentence ending with Mark un, meaning the architecture document instructions are incomplete and will produce unpredictable Claude output. | Complete the SYSTEM_ARCH prompt and add a CI check or test that validates all prompt strings are non-empty and well-formed. |
| MEDIUM | maintainability | `.github/scripts/tool3_business_docs.py` | None | The SYSTEM prompt and the Go-live and milest section are truncated, meaning the business document template is incomplete. | Complete all prompt templates and consider loading them from dedicated prompt files to make them easier to review and maintain independently. |
| MEDIUM | maintainability | `.github/scripts/tool4_auto_testing.py` | None | The SYSTEM_GAP prompt JSON schema definition is truncated, so the gap analysis output format is undefined and downstream parsing will likely fail. | Complete the SYSTEM_GAP prompt with the full expected JSON schema and add a validation step that checks Claude output conforms to the schema before writing files. |
| MEDIUM | maintainability | `.github/scripts/tool5_uat.py` | None | The SYSTEM_ANALYSE prompt is truncated mid-JSON-schema definition, meaning the defect report and go/no-go recommendation output format is undefined. | Complete the SYSTEM_ANALYSE prompt and store all long prompt templates in versioned files rather than inline Python strings. |
| MEDIUM | performance | `.github/scripts/shared.py` | 25 | A new anthropic.Anthropic client is instantiated on every call_claude invocation, which is inefficient when multiple Claude calls are made within the same script execution. | Instantiate the Anthropic client once at module level or use a singleton pattern to reuse the HTTP connection across multiple calls. |
| MEDIUM | correctness | `.github/scripts/shared.py` | 27 | call_claude accesses response.content[0].text without checking that the response content list is non-empty, which will raise an IndexError if Claude returns an empty response. | Add a guard that checks len(response.content) > 0 before accessing the first element and raises a descriptive exception if the response is empty. |
| LOW | maintainability | `.github/workflows/tool2_tech_docs.yml` | None | The install dependencies step uses an unpinned pip install anthropic requests command across all workflows, which may cause non-reproducible builds if package versions change. | Add a requirements.txt file with pinned versions and use pip install -r requirements.txt in all workflows to ensure reproducible dependency installation. |
| LOW | maintainability | `.github/scripts/shared.py` | 14 | The MODEL constant is hardcoded as claude-sonnet-4-6 directly in shared.py with no ability to override it at runtime for testing or cost optimisation. | Read the model name from an environment variable with the current value as a default so it can be overridden per workflow without code changes. |
| LOW | security | `.github/workflows/tool3_business_docs.yml` | None | The PROJECT_NAME and RELEASE_VERSION values from workflow_dispatch inputs are interpolated directly into the environment using echo without quoting, risking shell injection if inputs contain special characters. | Quote all input interpolations in shell steps as echo RELEASE_VERSION='${{ inputs.release_version }}' or use the env context to pass inputs safely. |

## IaC Findings
- No permissions block is defined in any workflow file, meaning the default GITHUB_TOKEN has read and write access to all repository scopes rather than least privilege.
- The GH_TOKEN secret appears to be a personal access token rather than a fine-grained token scoped to specific repositories, increasing blast radius if compromised.
- All five workflows use runs-on ubuntu-latest with no pinned runner version, which may cause unexpected behaviour when GitHub updates the latest label.
- There is no concurrency group defined in any workflow, meaning simultaneous PR events could trigger multiple overlapping Claude API calls and output repo writes.
- The output repository ai-delivery-outputs is referenced by name only with no branch protection or commit signing requirements mentioned, leaving AI-generated content unreviewed.
- No timeout-minutes is set on any job, meaning a hung Claude API call or network issue could consume GitHub Actions minutes indefinitely.
- The on.create trigger in tool5_uat.yml is not filtered to a specific branch pattern, meaning every tag creation also triggers the UAT workflow unexpectedly.

## Positive Observations
- Secrets are consistently sourced from GitHub Actions secrets rather than being hardcoded in workflow YAML files.
- A shared utility module correctly centralises GitHub API headers, Claude API calls, and email logic avoiding duplication across five tool scripts.
- The clean_json helper defensively strips markdown fences from Claude responses which is a practical and necessary safeguard.
- Each workflow correctly uses actions/checkout@v4 and actions/setup-python@v5 with pinned major versions reducing supply chain risk.
- The UAT tool thoughtfully supports two distinct modes generate and analyse covering the full UAT lifecycle in a single workflow.
- Prompt engineering is detailed and includes explicit output format constraints, severity enumerations, and rules to prevent hallucination.
- The schedule triggers are well distributed across different days and times to avoid concurrent API usage spikes.
- Audit logging via write_audit_entry is consistently called across all five tools providing an operational trail.
- The FORCE_JAVASCRIPT_ACTIONS_TO_NODE24 flag is consistently set to ensure forward compatibility with the GitHub Actions runtime.

---
_Auto-generated by AI Delivery Bot (claude-sonnet-4-6)_
