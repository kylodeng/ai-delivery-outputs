# Code Review Report
**Source:** kylodeng/ai-delivery-source
**Context:** 20260413
**Generated:** 2026-04-13 09:53 UTC
**Score:** 62/100 | **Recommendation:** `REQUEST_CHANGES`

## Summary
The codebase implements a well-structured multi-tool AI delivery pipeline but contains several security and maintainability concerns including hardcoded email addresses, missing error handling, and insufficient secret validation.

## Findings
| Severity | Category | File | Line | Issue | Recommendation |
|---|---|---|---|---|---|
| HIGH | security | `.github/scripts/shared.py` | 11 | Hardcoded personal email address kylo.deng@capco.com is embedded directly in source code as a default value for NOTIFY_EMAIL and SENDER_EMAIL. | Remove all default email values from code and require them to be set exclusively via environment variables or GitHub secrets with no fallback defaults. |
| HIGH | security | `.github/scripts/shared.py` | 8 | API keys are accessed via os.environ with hard bracket notation which raises a bare KeyError with no meaningful message if the secret is missing, potentially leaking variable names in logs. | Use os.environ.get with explicit validation and a safe error message, or add a startup check function that validates all required secrets are present before any API calls are made. |
| HIGH | security | `.github/workflows/tool1_code_review.yml` | 22 | Personal email address kylo.deng@capco.com is hardcoded directly in the workflow YAML files and would be exposed in the public repository. | Replace hardcoded email addresses in all workflow YAML files with a GitHub secret such as secrets.NOTIFY_EMAIL to prevent personal data exposure. |
| HIGH | security | `.github/workflows/tool5_uat.yml` | None | The workflow_dispatch input uat_results_path accepts a user-supplied file path that is likely used to read files from the output repo without any path traversal validation. | Validate and sanitise the uat_results_path input to restrict it to an expected prefix pattern before using it in any file read or API call. |
| MEDIUM | security | `.github/workflows/tool1_code_review.yml` | None | The pull_request trigger combined with inputs.pr_number accepting arbitrary user input could allow a malicious PR to influence the review target if the script uses that value without validation. | Validate that PR_NUMBER is a positive integer and restrict its use to read-only GitHub API calls scoped to the triggering repository only. |
| MEDIUM | security | `.github/scripts/tool5_uat.py` | None | CSV data from uat_results_path is parsed without any size or content validation, which could cause memory exhaustion or injection if the file is unexpectedly large or malformed. | Add a file size check and content validation before parsing the CSV, and enforce a maximum row count to prevent resource exhaustion. |
| MEDIUM | maintainability | `.github/scripts/shared.py` | 15 | The MODEL constant is hardcoded to claude-sonnet-4-6 with no way to override it via environment variable, making model upgrades require code changes. | Read the model name from an environment variable with the current value as default, for example os.environ.get('CLAUDE_MODEL', 'claude-sonnet-4-6'). |
| MEDIUM | correctness | `.github/scripts/tool1_code_review.py` | None | The extract_json function attempts to robustly parse Claude responses but the code is truncated in the review, making it impossible to verify all edge cases are handled. | Ensure extract_json wraps all json.loads calls in try-except blocks and logs the raw response before raising to aid debugging. |
| MEDIUM | security | `.github/scripts/shared.py` | 17 | The GH_TOKEN is embedded directly into a module-level headers dictionary meaning any code importing shared.py has full access to the token from the global GH_HEADERS object. | Build the Authorization header lazily inside each function call or use a session object to limit token exposure surface area. |
| MEDIUM | performance | `.github/scripts/shared.py` | 24 | A new Anthropic client is instantiated on every call_claude invocation rather than being reused, which adds unnecessary overhead for workflows that make multiple Claude calls. | Instantiate the Anthropic client once at module level or use a module-level singleton to reuse the connection across calls. |
| MEDIUM | correctness | `.github/scripts/tool2_tech_docs.py` | None | The SYSTEM_ARCH prompt string is truncated mid-sentence in the review, suggesting the actual file may have incomplete prompt instructions that could degrade Claude output quality. | Audit all truncated prompt strings in the codebase to ensure they are complete and test the prompts independently before relying on them in production workflows. |
| LOW | maintainability | `.github/scripts/shared.py` | 32 | The get_repo_files docstring is truncated with the word Fetch cut off, indicating incomplete inline documentation throughout the shared utility module. | Complete all docstrings with parameter descriptions, return types, and exception documentation to improve maintainability. |
| LOW | maintainability | `.github/scripts/tool4_auto_testing.py` | None | The SYSTEM_GAP prompt JSON schema definition is truncated in the review, which could mean the generated gap analysis JSON has an incomplete schema causing parsing failures. | Verify the complete SYSTEM_GAP prompt is present in the source file and add a JSON schema validation step after parsing Claude responses. |
| LOW | security | `.github/workflows/tool2_tech_docs.yml` | None | The workflow runs pip install without pinned dependency versions meaning a supply chain compromise of the anthropic or requests packages could silently affect all tools. | Pin dependency versions in a requirements.txt file and use pip install -r requirements.txt with hashes, or use a lockfile approach such as pip-compile. |

## IaC Findings
- All five workflows use runs-on ubuntu-latest which is a floating label and could change behaviour unexpectedly when GitHub updates the runner image, consider pinning to a specific runner version.
- No permissions block is defined in any workflow file meaning jobs run with the default GITHUB_TOKEN permissions which may be broader than necessary for each tools specific needs.
- There is no concurrency group defined in any workflow meaning multiple simultaneous runs of the same workflow could race and produce duplicate or conflicting output repo commits.
- The output repo ai-delivery-outputs is referenced as a plain string constant with no validation that it exists or that the GH_TOKEN has write access before attempting to write files.
- No timeout-minutes is set on any job meaning a hung Claude API call or network issue could leave a workflow running indefinitely and consuming Actions minutes.
- The tool5_uat.yml workflow triggers on the create event which fires for both branch and tag creation, potentially running UAT generation on every tag push unintentionally.
- Dependencies are installed directly with pip install anthropic requests with no caching step, causing redundant downloads on every workflow run and increasing execution time.

## Positive Observations
- Secrets are consistently sourced from environment variables and GitHub Actions secrets rather than being hardcoded as literal values in logic code.
- Each workflow tool is cleanly separated into its own script and workflow file following a consistent single-responsibility pattern.
- The clean_json utility function defensively strips markdown fences from Claude responses, handling a common LLM output formatting issue.
- Workflow triggers are well-designed with appropriate combinations of push, pull_request, schedule, and workflow_dispatch events for each use case.
- The UAT tool supports two distinct modes, generate and analyse, providing a complete end-to-end UAT lifecycle within a single tool.
- The FORCE_JAVASCRIPT_ACTIONS_TO_NODE24 environment variable is consistently set across all workflows demonstrating awareness of runtime compatibility.
- Claude prompts include explicit rules about not inventing information and using TODO markers for gaps, reducing hallucination risk in generated documents.
- The shared.py module centralises all GitHub API, Claude API, and email concerns, avoiding duplication across the five tool scripts.

---
_Auto-generated by AI Delivery Bot (claude-sonnet-4-6)_
