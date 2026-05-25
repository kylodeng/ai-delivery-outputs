# Code Review Report
**Source:** kylodeng/ai-delivery-source
**Context:** 20260525
**Generated:** 2026-05-25 11:51 UTC
**Score:** 62/100 | **Recommendation:** `REQUEST_CHANGES`

## Summary
This CI/CD automation codebase is well-structured with clear separation of concerns across five AI-powered tools, but contains several security and maintainability concerns that should be addressed before broader adoption.

## Findings
| Severity | Category | File | Line | Issue | Recommendation |
|---|---|---|---|---|---|
| HIGH | security | `.github/workflows/tool1_code_review.yml` | None | Hardcoded email address kylo.deng@capco.com is committed to source control in every workflow file, exposing PII and creating a maintenance burden. | Move all email addresses to GitHub secrets or repository variables and reference them as secrets.NOTIFY_EMAIL. |
| HIGH | security | `.github/scripts/shared.py` | 15 | NOTIFY_EMAIL and SENDER_EMAIL default to a hardcoded personal email address in source code, meaning the address is exposed if the repo is ever made public. | Remove the hardcoded default values entirely and require the environment variables to be explicitly set, raising a clear error if they are absent. |
| HIGH | security | `.github/workflows/tool5_uat.yml` | None | The user_stories input accepts free-text pasted content that is passed directly to Claude, creating a prompt injection risk where a malicious actor could manipulate AI outputs. | Sanitise or validate the user_stories input before passing it to Claude, and apply a maximum length constraint on the input field. |
| HIGH | security | `.github/workflows/tool5_uat.yml` | None | The uat_results_path input accepts an arbitrary path in the output repo, which could be exploited to read or overwrite sensitive files via path traversal. | Validate the uat_results_path input against a strict allowlist pattern such as a regex anchored to the expected directory structure before use. |
| HIGH | security | `.github/scripts/shared.py` | 10 | API keys are accessed at module import time with direct dictionary-style access, meaning any import failure or missing secret will cause an unhandled KeyError that may leak partial environment state in logs. | Use a helper function that validates all required secrets at startup and raises a descriptive error without printing the key values. |
| MEDIUM | security | `.github/workflows/tool1_code_review.yml` | None | The GH_TOKEN secret is passed as a plain environment variable to all workflow steps, giving every step access to a potentially broad-scoped token. | Scope permissions explicitly using the permissions key in each job and use GITHUB_TOKEN with minimal required scopes instead of a personal access token where possible. |
| MEDIUM | security | `.github/scripts/tool1_code_review.py` | None | The extract_json function parses Claude responses as JSON without schema validation, meaning a malformed or adversarially crafted AI response could cause unexpected behaviour downstream. | Validate the parsed JSON against a strict schema using jsonschema or pydantic before using any fields from the response. |
| MEDIUM | security | `.github/scripts/tool5_uat.py` | None | CSV data from an external path is parsed using the csv and io modules without sanitisation, which could lead to CSV injection if the file contains formula-injection payloads. | Strip leading =, +, -, and @ characters from all CSV cell values before processing or passing to downstream systems. |
| MEDIUM | correctness | `.github/workflows/tool5_uat.yml` | None | The workflow triggers on the create event without filtering to only release branches, meaning it fires on every tag and branch creation including feature branches. | Add a job-level conditional such as if: startsWith(github.ref, refs/heads/release/) to restrict execution to intended branch patterns. |
| MEDIUM | correctness | `.github/workflows/tool2_tech_docs.yml` | None | The workflow triggers on every push to main but there is no concurrency group defined, so parallel pushes can cause multiple documentation generation jobs to race and produce inconsistent outputs. | Add a concurrency block with cancel-in-progress: true to ensure only one documentation generation run executes at a time. |
| MEDIUM | maintainability | `.github/scripts/shared.py` | 16 | The MODEL constant is hardcoded as claude-sonnet-4-6 in source code, requiring a code change and deployment to update the model version. | Move the model name to an environment variable with a sensible default so it can be overridden without code changes. |
| MEDIUM | performance | `.github/scripts/shared.py` | 25 | A new Anthropic client is instantiated on every call to call_claude, which incurs unnecessary object creation overhead in workflows that make multiple sequential calls. | Create the Anthropic client once at module level or pass it as a parameter to avoid repeated instantiation. |
| MEDIUM | maintainability | `.github/scripts/tool2_tech_docs.py` | None | The SYSTEM_ARCH prompt string is truncated mid-sentence in the provided code, suggesting incomplete implementation that could produce unpredictable AI outputs. | Complete the system prompt and add a unit test or assertion that verifies the prompt meets a minimum length and contains all required section headings. |
| MEDIUM | maintainability | `.github/scripts/tool3_business_docs.py` | None | The SYSTEM prompt template contains literal placeholder tokens such as {project_name} and {date} that appear to require string formatting before use, but there is no visible formatting call in the provided excerpt. | Use explicit str.format or an f-string with clearly documented substitution points, and add a test to verify all placeholders are replaced before the prompt is sent to Claude. |
| LOW | maintainability | `.github/scripts/tool4_auto_testing.py` | None | The SYSTEM_GAP prompt JSON schema is truncated in the provided code, which may cause Claude to return incomplete or inconsistent JSON structures. | Ensure all prompt strings are complete and store them in dedicated prompt files or constants that are validated at startup. |
| LOW | maintainability | `.github/scripts/shared.py` | None | The get_repo_files function docstring is truncated mid-sentence, indicating incomplete documentation that will hinder future maintainers. | Complete all docstrings and enforce documentation completeness with a linting tool such as pydocstyle in CI. |
| LOW | correctness | `.github/workflows/tool4_auto_testing.yml` | None | The path filter for pull_request triggers includes *.py and *.js at the repo root but misses nested directories other than src/, so changes to scripts or config files may not trigger the workflow. | Review and expand the paths filter to cover all relevant source directories, or use a paths-ignore approach if the intent is to exclude only documentation. |

## IaC Findings
- No explicit permissions block is defined in any workflow job, meaning the GITHUB_TOKEN receives default read/write permissions broader than necessary.
- All five workflows use runs-on: ubuntu-latest without pinning to a specific runner version, which can cause unexpected behaviour when GitHub updates the runner image.
- There is no timeout-minutes set on any job, meaning a hung Claude API call or network issue could allow a job to consume runner minutes until the GitHub default six-hour limit.
- The output repository ai-delivery-outputs is referenced by name with no branch protection or access control configuration visible, making it a potential exfiltration target if the GH_TOKEN is compromised.
- No artifact retention or cleanup policy is defined for outputs written to the output repository, which may result in unbounded storage growth and exposure of historical code review findings.
- Concurrency groups are absent from all five workflows, creating race conditions when multiple events trigger simultaneously on busy repositories.

## Positive Observations
- Secrets are consistently sourced from GitHub secrets rather than hardcoded values for API keys.
- Each tool is cleanly separated into its own script and corresponding workflow file, making the codebase easy to navigate.
- The shared.py module correctly centralises common functionality such as Claude invocation, GitHub API calls, and email sending to avoid duplication.
- The clean_json helper defensively strips markdown fences from Claude responses, acknowledging a real-world API behaviour.
- All workflows pin action versions using major version tags such as actions/checkout@v4, reducing supply chain risk.
- The UAT tool sensibly supports two distinct modes (generate and analyse) in a single workflow, keeping the interface coherent.
- Workflow files set FORCE_JAVASCRIPT_ACTIONS_TO_NODE24 to ensure consistent Node.js runtime behaviour.
- The code review tool uses a structured JSON schema for Claude responses, making downstream parsing deterministic.
- The business documentation tool explicitly instructs Claude to flag unknowns with TODO markers rather than hallucinating content.

---
_Auto-generated by AI Delivery Bot (claude-sonnet-4-6)_
