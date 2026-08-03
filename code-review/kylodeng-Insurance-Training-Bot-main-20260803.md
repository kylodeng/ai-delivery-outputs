# Code Review Report
**Source:** kylodeng/Insurance-Training-Bot-main
**Context:** 20260803
**Generated:** 2026-08-03 11:19 UTC
**Score:** 62/100 | **Recommendation:** `REQUEST_CHANGES`

## Summary
The codebase implements a multi-tool AI delivery pipeline with reasonable structure, but contains several security and maintainability concerns including hardcoded email addresses, missing error handling, and incomplete secret validation.

## Findings
| Severity | Category | File | Line | Issue | Recommendation |
|---|---|---|---|---|---|
| HIGH | security | `.github/scripts/shared.py` | 11 | Hardcoded personal email address kylo.deng@capco.com is embedded directly in source code as a default value for NOTIFY_EMAIL and SENDER_EMAIL. | Remove hardcoded email defaults and require these values to be explicitly set as repository secrets or environment variables with no fallback. |
| HIGH | security | `.github/scripts/shared.py` | 7 | API keys are accessed via os.environ with direct key access which raises KeyError but provides no validation that secrets are non-empty, allowing accidental use of blank secrets. | Add explicit validation that each secret is non-empty and non-whitespace after retrieval, raising a descriptive error if any are blank or missing. |
| HIGH | security | `.github/workflows/tool1_code_review.yml` | 23 | Personal email address kylo.deng@capco.com is hardcoded in workflow environment variables, leaking an individuals email in public repository metadata. | Replace all hardcoded email addresses in workflow files with a repository secret or organisation-level variable such as secrets.NOTIFY_EMAIL. |
| HIGH | security | `.github/workflows/tool2_tech_docs.yml` | 19 | Personal email address is hardcoded in the workflow env block, repeating the same exposure across multiple workflow files. | Centralise notification email configuration as an organisation secret and reference it consistently across all workflow files. |
| MEDIUM | security | `.github/workflows/tool1_code_review.yml` | 56 | The GH_TOKEN secret is exposed as a plain environment variable across all workflow jobs, granting broader scope than may be necessary for each individual tool. | Scope GitHub token permissions explicitly using the permissions key in each job and use the minimum required scopes per workflow. |
| MEDIUM | security | `.github/scripts/tool5_uat.py` | 1 | The script imports csv and io for processing test result data but there is no visible sanitisation or size-limiting of uploaded CSV content before processing. | Add input validation including file size limits and content-type checks before parsing any externally supplied CSV data passed to Claude. |
| MEDIUM | correctness | `.github/scripts/shared.py` | 1 | The get_repo_files function docstring is truncated mid-sentence indicating the implementation is incomplete or was accidentally cut off. | Complete the function implementation and ensure all public functions have complete docstrings before merging. |
| MEDIUM | correctness | `.github/scripts/tool2_tech_docs.py` | 1 | The SYSTEM_ARCH prompt string is visibly truncated mid-sentence with Mark which means the architecture document generation prompt is incomplete. | Restore the complete prompt string and add a test that validates prompt strings are non-empty and meet a minimum length threshold. |
| MEDIUM | correctness | `.github/scripts/tool5_uat.py` | 1 | The SYSTEM_ANALYSE prompt JSON schema definition is truncated mid-field which will likely cause malformed prompts and unpredictable Claude responses. | Complete all prompt strings and add unit tests that assert each SYSTEM prompt is a valid complete string before deployment. |
| MEDIUM | maintainability | `.github/scripts/shared.py` | 14 | The MODEL constant is hardcoded to claude-sonnet-4-6 with no environment variable override, making it hard to switch models without a code change. | Read the model name from an environment variable with claude-sonnet-4-6 as the documented default to allow model updates without code changes. |
| MEDIUM | performance | `.github/scripts/shared.py` | 23 | A new anthropic.Anthropic client is instantiated on every call to call_claude, which is wasteful when multiple Claude calls are made in a single script run. | Instantiate the Anthropic client once at module level or use a module-level singleton to avoid repeated initialisation overhead. |
| MEDIUM | security | `.github/workflows/tool4_auto_testing.yml` | 1 | The TEST_MODE environment variable value is truncated in the provided snippet suggesting the workflow file may have encoding or truncation issues that could cause silent failures. | Validate all workflow YAML files with a linter such as actionlint in CI to catch truncation and syntax errors before they reach production. |
| LOW | maintainability | `.github/workflows/deploy.yml` | 1 | The deploy workflow pins Python to 3.13 while all tool workflows use 3.12, creating an inconsistency that could mask compatibility issues. | Standardise on a single Python version across all workflows and define it as a repository-level variable or workflow input. |
| LOW | maintainability | `.github/scripts/tool1_code_review.py` | 1 | Multiple scripts use bare wildcard-style imports from shared via from shared import which makes it difficult to track what each script actually depends on. | Keep explicit named imports as already done but consider grouping shared constants and functions into clearly namespaced submodules for larger-scale maintainability. |
| LOW | correctness | `.github/scripts/shared.py` | 31 | The clean_json function only strips a single level of markdown fencing and will silently return malformed JSON if Claude returns nested or unusual formatting. | Use a regex-based extraction that captures the first valid JSON object or array, and raise a clear exception if no valid JSON is found. |

## IaC Findings
- Azure App Service deployment uses publish-profile authentication which embeds long-lived credentials; consider migrating to federated OIDC identity for keyless deployment.
- No environment separation is visible in the deploy workflow meaning the same workflow deploys to what appears to be a single production environment with no staging gate.
- The output repository ai-delivery-outputs has a hardcoded name with no environment suffix, risking outputs from different environments being written to the same repository.
- No resource tagging strategy is visible in any IaC or workflow configuration, which will make cost attribution and governance difficult in Azure.
- There is no explicit timeout set on any workflow job, meaning a hung Claude API call or deployment could consume runner minutes indefinitely.
- The workflow does not implement concurrency controls, so simultaneous PRs could trigger parallel code review jobs that overwrite each other s output files.

## Positive Observations
- API keys and tokens are correctly sourced from environment variables rather than being hardcoded as literal strings in logic code.
- The shared.py module cleanly centralises all external integrations reducing duplication across the five tool scripts.
- Workflow files correctly use secrets references for all sensitive credentials like ANTHROPIC_API_KEY and SENDGRID_API_KEY.
- The deploy workflow correctly gates deployment jobs with needs: test ensuring tests must pass before any deployment occurs.
- Claude prompts include explicit output format constraints and rules which improves response reliability and reduces post-processing failures.
- The clean_json utility function proactively handles a known Claude formatting quirk showing defensive coding awareness.
- Workflow triggers are well-designed using appropriate event combinations including schedule, push, pull_request, and workflow_dispatch.
- The FORCE_JAVASCRIPT_ACTIONS_TO_NODE24 flag is consistently set across all workflows showing awareness of runner compatibility requirements.

---
_Auto-generated by AI Delivery Bot (claude-sonnet-4-6)_
