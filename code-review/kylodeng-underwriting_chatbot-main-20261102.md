# Code Review Report
**Source:** kylodeng/underwriting_chatbot-main
**Context:** 20261102
**Generated:** 2026-11-02 18:08 UTC
**Score:** 62/100 | **Recommendation:** `REQUEST_CHANGES`

## Summary
Well-structured AI delivery workflow toolchain with good separation of concerns, but contains several security and reliability issues including hardcoded email addresses, missing error handling, and workflow permission concerns.

## Findings
| Severity | Category | File | Line | Issue | Recommendation |
|---|---|---|---|---|---|
| HIGH | security | `.github/scripts/shared.py` | 12 | Personal email addresses are hardcoded as default values for NOTIFY_EMAIL and SENDER_EMAIL, leaking PII into source code. | Remove hardcoded email defaults and require them to be set exclusively via repository secrets or environment variables with no fallback. |
| HIGH | security | `.github/workflows/tool1_code_review.yml` | 23 | NOTIFY_EMAIL and SENDER_EMAIL are hardcoded directly in workflow YAML files, exposing personal email addresses in the repository. | Move email addresses to GitHub Actions secrets or repository variables and reference them via secrets context. |
| HIGH | security | `.github/scripts/shared.py` | 9 | ANTHROPIC_API_KEY is accessed with a hard crash on missing key using direct dict access instead of a safe accessor, which could expose the key name in error logs. | Use os.environ.get with an explicit error message that does not echo secret values, or validate all required secrets at startup with a single sanitised error. |
| HIGH | security | `.github/workflows/tool5_uat.yml` | 1 | The on.create trigger fires for every branch and tag creation, not just release branches, which could allow unintended workflow execution and resource abuse. | Replace the bare on.create trigger with on.push with a branches filter such as release/** to restrict execution to intended release branches only. |
| HIGH | security | `.github/workflows/tool1_code_review.yml` | 1 | No explicit permissions block is defined on any workflow, so the GITHUB_TOKEN receives the repository default permissions which may be overly broad. | Add a top-level permissions block to each workflow granting only the minimum required scopes, for example permissions: contents: read and pull-requests: write. |
| MEDIUM | security | `.github/scripts/tool5_uat.py` | 1 | The uat_results_path workflow input allows a caller-controlled file path to be read from the output repo, creating a potential path traversal risk. | Validate and sanitise the uat_results_path input against an allowlist pattern before using it to construct API requests or file reads. |
| MEDIUM | correctness | `.github/scripts/shared.py` | 9 | Using os.environ with direct key access for all three API keys will raise an unhandled KeyError at import time if any secret is missing, with no user-friendly message. | Validate all required environment variables at startup in a dedicated check function that raises a descriptive RuntimeError listing which variables are absent. |
| MEDIUM | maintainability | `.github/scripts/shared.py` | 16 | The MODEL value claude-sonnet-4-6 is hardcoded as a module-level constant with no override mechanism, making model version updates require code changes. | Read MODEL from an environment variable with the current value as the default to allow runtime overrides without code changes. |
| MEDIUM | performance | `.github/scripts/shared.py` | 26 | A new anthropic.Anthropic client instance is created on every call_claude invocation, adding unnecessary object initialisation overhead in loops. | Instantiate the Anthropic client once at module level or use a module-level cached instance to avoid repeated initialisation. |
| MEDIUM | correctness | `.github/scripts/tool1_code_review.py` | 1 | The extract_json function is described as robustly extracting JSON but the implementation is truncated in the review, making it impossible to verify error handling completeness. | Ensure extract_json wraps json.loads in a try-except block and returns a structured fallback dict with an error field rather than raising an unhandled exception. |
| MEDIUM | security | `.github/workflows/tool3_business_docs.yml` | 44 | The PROJECT_NAME environment variable is set directly from github.event.repository.name or user input without sanitisation and passed to a Python script. | Sanitise the PROJECT_NAME value by stripping shell-special characters in the workflow step before exporting it to GITHUB_ENV. |
| MEDIUM | correctness | `.github/scripts/tool2_tech_docs.py` | 1 | SYSTEM_ARCH prompt string appears truncated in the provided code, which may cause incomplete architecture document generation at runtime. | Ensure the full SYSTEM_ARCH prompt string is complete and terminated correctly in the source file. |
| LOW | maintainability | `.github/scripts/shared.py` | 1 | The get_repo_files docstring is truncated, leaving developers without complete documentation for the function signature and return value. | Complete the docstring to document all parameters, return type, and any exceptions that may be raised. |
| LOW | maintainability | `.github/workflows/tool2_tech_docs.yml` | 1 | The pip install step in every workflow uses unpinned package versions for anthropic and requests, which can cause non-reproducible builds. | Pin dependency versions using a requirements.txt file and reference it with pip install -r requirements.txt to ensure reproducible builds. |
| LOW | maintainability | `.github/scripts/tool3_business_docs.py` | 1 | SYSTEM prompt string for business docs is truncated, suggesting the full prompt template is not represented in the reviewed code. | Ensure the complete prompt is present and add a unit test that validates prompt completeness by checking for all required section headers. |
| LOW | security | `.github/scripts/shared.py` | 20 | The GH_HEADERS dict is a module-level mutable global containing a live bearer token, making it accessible to any code that imports shared.py. | Construct GH_HEADERS inside a helper function that reads from the environment at call time rather than storing the token in a module-level dict. |

## IaC Findings
- No workflow-level permissions blocks are defined, meaning GITHUB_TOKEN operates with repository-default permissions across all five workflows.
- The on.create trigger in tool5_uat.yml is not scoped to release branches, allowing the workflow to fire on any branch or tag creation.
- All workflows use ubuntu-latest as the runner, which is a floating reference that can silently change the OS version and introduce breaking changes.
- No concurrency groups are defined on any workflow, allowing multiple parallel runs on the same PR which could cause race conditions when writing to the output repo.
- The OUTPUT_REPO is hardcoded as ai-delivery-outputs in both shared.py and all workflow env blocks rather than being a single source-of-truth repository variable.

## Positive Observations
- Secrets are correctly sourced from GitHub Actions secrets rather than being hardcoded in code logic.
- Shared utility module pattern avoids duplication of Claude API and GitHub API boilerplate across all five tools.
- clean_json helper correctly handles markdown code fence stripping from LLM responses.
- Workflow triggers are well-designed with PR, schedule, and manual dispatch options covering the main use cases.
- Claude prompts include explicit output format schemas with type constraints, reducing the chance of unparseable responses.
- Audit logging is included as a first-class concern via write_audit_entry in all tools.
- The paths-ignore filter on tool2 workflow correctly prevents documentation-only commits from triggering re-generation loops.
- Use of FORCE_JAVASCRIPT_ACTIONS_TO_NODE24 demonstrates awareness of actions runtime version management.

---
_Auto-generated by AI Delivery Bot (claude-sonnet-4-6)_
