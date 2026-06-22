# Code Review Report
**Source:** kylodeng/underwriting_chatbot-main
**Context:** 20260622
**Generated:** 2026-06-22 13:41 UTC
**Score:** 62/100 | **Recommendation:** `REQUEST_CHANGES`

## Summary
The codebase implements a well-structured multi-tool AI delivery pipeline using Claude and GitHub Actions, but contains several security and maintainability concerns including hardcoded email addresses, missing error handling, and potential secret exposure risks.

## Findings
| Severity | Category | File | Line | Issue | Recommendation |
|---|---|---|---|---|---|
| HIGH | security | `.github/scripts/shared.py` | 12 | Hardcoded personal email address kylo.deng@capco.com is embedded directly in source code as a default value for NOTIFY_EMAIL and SENDER_EMAIL. | Remove hardcoded email defaults and require them to be set exclusively via repository secrets or organisation-level environment variables. |
| HIGH | security | `.github/scripts/shared.py` | 9 | ANTHROPIC_API_KEY, GH_TOKEN, and SENDGRID_API_KEY are accessed via os.environ with hard bracket indexing, which will raise an unhandled KeyError and may expose partial stack trace information in CI logs if a secret is missing. | Use os.environ.get with explicit None checks and raise a sanitised ConfigurationError that does not echo secret names or values to stdout. |
| HIGH | security | `.github/workflows/tool1_code_review.yml` | 22 | The hardcoded NOTIFY_EMAIL value kylo.deng@capco.com is stored in plaintext in the workflow YAML which is committed to version control and potentially public. | Replace all hardcoded email addresses in workflow env blocks with references to repository or organisation secrets such as secrets.NOTIFY_EMAIL. |
| HIGH | security | `.github/workflows/tool5_uat.yml` | 1 | The workflow triggers on the create event without any branch filter, meaning it fires on every tag and branch creation including potentially attacker-controlled branch names. | Add a branch filter such as branches starting with release to the create trigger to limit the attack surface for workflow injection. |
| HIGH | security | `.github/workflows/tool1_code_review.yml` | 47 | The PR_NUMBER is sourced directly from github.event.pull_request.number and injected into an environment variable without sanitisation, creating a potential workflow injection risk. | Validate that PR_NUMBER is a pure integer before using it and avoid directly interpolating untrusted github context values into shell run steps. |
| MEDIUM | security | `.github/scripts/tool5_uat.py` | 1 | The tool imports csv and io for processing UAT result sheets but there is no visible size limit or content validation on the CSV data fetched from the output repository before processing. | Add a maximum file size check and validate CSV structure before parsing to prevent denial-of-service or prompt injection via a maliciously crafted results file. |
| MEDIUM | security | `.github/scripts/tool1_code_review.py` | 1 | The code review tool reads repository files and passes them directly to Claude, meaning a malicious file in a PR could contain prompt injection content designed to manipulate the AI response. | Sanitise or truncate file content before embedding it in Claude prompts and consider adding a maximum token budget per file to limit injection surface. |
| MEDIUM | correctness | `.github/scripts/shared.py` | 30 | The call_claude function creates a new Anthropic client on every invocation, which is inefficient and could exhaust connection pools under concurrent use. | Instantiate the Anthropic client once at module level or use a singleton pattern to reuse the client across calls. |
| MEDIUM | maintainability | `.github/scripts/shared.py` | 17 | The MODEL constant is hardcoded to claude-sonnet-4-6 with no environment variable override, making it impossible to switch models without a code change. | Read the model name from an environment variable with the current value as a sensible default to allow runtime configuration. |
| MEDIUM | correctness | `.github/scripts/tool1_code_review.py` | 1 | The extract_json function attempts to robustly parse Claude responses but the shared clean_json utility already does partial stripping, creating duplicated and potentially conflicting logic. | Consolidate JSON extraction into a single shared utility function to avoid divergent parsing behaviour across tools. |
| MEDIUM | maintainability | `.github/workflows/tool2_tech_docs.yml` | 1 | The workflow has no timeout-minutes set on the job, meaning a hung Claude API call or network issue could cause the job to run until the GitHub default 6-hour timeout, consuming runner minutes. | Add timeout-minutes at the job level, for example 15 minutes, to ensure failed runs are terminated promptly. |
| MEDIUM | security | `.github/workflows/tool3_business_docs.yml` | 1 | The workflow_dispatch input project_name and release_version are passed directly to an environment variable and then to a Python script without any input sanitisation. | Validate that release_version matches a semantic version pattern and that project_name contains only alphanumeric and safe characters before use. |
| LOW | maintainability | `.github/scripts/tool2_tech_docs.py` | 1 | The SYSTEM_ARCH prompt string is visibly truncated in the provided code snippet ending with Mark un, suggesting the full system prompt may be incomplete. | Ensure the complete system prompt is present in the file and add a unit test or assertion to verify minimum prompt length at startup. |
| LOW | maintainability | `.github/scripts/shared.py` | 1 | The get_repo_files function docstring is truncated mid-sentence with Fetch tex suggesting incomplete documentation. | Complete all docstrings and consider enforcing docstring completeness via a linter such as pydocstyle in CI. |
| LOW | performance | `.github/workflows/tool1_code_review.yml` | 35 | pip install anthropic requests is run without pinned versions and without a requirements file, meaning builds are not reproducible and could silently break on dependency updates. | Create a requirements.txt with pinned versions and use pip install -r requirements.txt to ensure reproducible builds. |
| LOW | maintainability | `.github/scripts/tool3_business_docs.py` | 1 | The SYSTEM prompt uses Python-style format placeholders such as project_name and version inside a triple-quoted string, but there is no visible call to str.format or f-string substitution in the snippet. | Verify that all placeholder substitutions are performed before the prompt is sent to Claude and add a test to catch missing substitutions. |

## IaC Findings
- No Terraform, CloudFormation, CDK, or other IaC files are present in the provided code, so infrastructure security posture cannot be assessed.
- GitHub Actions workflows do not specify permissions blocks at the job level, meaning jobs may inherit overly broad default token permissions including write access to contents and pull requests.
- No CODEOWNERS file or required reviewer rules are referenced, meaning the AI-generated outputs could be merged to the output repository without human review.
- The output repository ai-delivery-outputs is referenced by name but no branch protection rules or access controls for it are defined in the provided configuration.

## Positive Observations
- Secrets are correctly sourced from GitHub Actions secrets rather than hardcoded in workflow files for sensitive API keys.
- The clean_json utility function provides a sensible defence against Claude wrapping responses in markdown fences.
- All five tools follow a consistent modular pattern sharing common utilities from shared.py which improves maintainability.
- Workflows use pinned major versions of actions such as actions/checkout@v4 reducing supply chain risk.
- The FORCE_JAVASCRIPT_ACTIONS_TO_NODE24 flag is consistently set across all workflows showing awareness of runner compatibility.
- The tool prompts include explicit rules about not inventing information and flagging unknowns with TODO markers.
- The UAT tool correctly separates generation and analysis modes reducing complexity per invocation.
- The use of anthropic as an official SDK rather than raw HTTP calls is a good practice for maintainability and correctness.

---
_Auto-generated by AI Delivery Bot (claude-sonnet-4-6)_
