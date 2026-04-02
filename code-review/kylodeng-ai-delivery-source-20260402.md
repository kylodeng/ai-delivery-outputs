# Code Review Report
**Source:** kylodeng/ai-delivery-source
**Context:** 20260402
**Generated:** 2026-04-02 12:39 UTC
**Score:** 62/100 | **Recommendation:** `REQUEST_CHANGES`

## Summary
A well-structured multi-tool AI delivery pipeline with good use of environment secrets, but containing several medium-to-high severity issues around error handling, hardcoded personal email addresses, overly broad GitHub token permissions, and missing input validation.

## Findings
| Severity | Category | File | Line | Issue | Recommendation |
|---|---|---|---|---|---|
| HIGH | security | `.github/workflows/tool1_code_review.yml` | None | GH_TOKEN is used across all workflows with no scope restriction, likely granting broad repository permissions including write access to all resources. | Replace the personal GH_TOKEN secret with a fine-grained GitHub token scoped to only the permissions each workflow requires, or use GITHUB_TOKEN with explicit minimal permissions block in each workflow. |
| HIGH | security | `.github/scripts/shared.py` | 10 | A hardcoded personal email address kylo.deng@capco.com is used as the default value for NOTIFY_EMAIL and SENDER_EMAIL, which could leak PII and makes the tool non-portable. | Remove all hardcoded personal email defaults and require NOTIFY_EMAIL and SENDER_EMAIL to be explicitly set as repository secrets or required environment variables with no default. |
| HIGH | security | `.github/scripts/shared.py` | 8 | os.environ access for ANTHROPIC_API_KEY, GH_TOKEN, and SENDGRID_API_KEY will raise an unhandled KeyError at module import time if any secret is missing, exposing internal variable names in CI logs. | Wrap secret retrieval in explicit validation with a descriptive error message, for example using os.environ.get with a None check and sys.exit, to fail safely without leaking variable names. |
| HIGH | security | `.github/workflows/tool5_uat.yml` | None | The uat_results_path workflow input accepts a free-form file path string that could be used to traverse the output repository and read or overwrite arbitrary files. | Validate and sanitise the uat_results_path input against an allowlist pattern before use, and ensure the script performing the file read rejects paths containing traversal sequences. |
| HIGH | security | `.github/workflows/tool1_code_review.yml` | None | The pr_number workflow dispatch input is passed unsanitised into shell environment variables and potentially into API calls, creating an injection risk. | Validate that pr_number is a numeric integer before exporting it to GITHUB_ENV, rejecting any non-numeric value with an explicit error. |
| MEDIUM | security | `.github/scripts/shared.py` | None | The MODEL constant is hardcoded to claude-sonnet-4-6 with no validation, meaning a dependency version change could silently alter AI behaviour across all five tools. | Pin the model identifier via an environment variable with validation against an allowlist of known safe model names, and log the model used in every audit entry. |
| MEDIUM | security | `.github/workflows/tool3_business_docs.yml` | None | The project_name and release_version workflow dispatch inputs are injected into shell environment variables without sanitisation, allowing special characters to break shell variable assignment. | Quote all input references in the run block and validate inputs match expected patterns such as alphanumeric and dots only before use. |
| MEDIUM | maintainability | `.github/scripts/shared.py` | None | The get_repo_files function docstring is truncated mid-sentence, suggesting incomplete code was provided or the function body is missing critical logic. | Complete the function docstring and ensure the full implementation including error handling for API rate limits and non-200 responses is present. |
| MEDIUM | correctness | `.github/scripts/tool1_code_review.py` | None | The extract_json function is documented as robustly handling Claude response formatting issues but the implementation is truncated and may silently fail for malformed responses. | Ensure extract_json includes a try-except around json.loads with explicit logging of the raw response on failure and raises a descriptive exception rather than returning None or empty dict. |
| MEDIUM | correctness | `.github/scripts/tool2_tech_docs.py` | None | The SYSTEM_ARCH prompt is truncated mid-sentence in the rules section, meaning the architecture document generation prompt is incomplete and may produce inconsistent output. | Complete the SYSTEM_ARCH prompt to include all intended rules, particularly around flagging overly broad IAM roles and missing encryption. |
| MEDIUM | correctness | `.github/scripts/tool5_uat.py` | None | The SYSTEM_ANALYSE JSON schema is truncated, meaning the UAT analysis mode may fail to produce a valid defect report structure or silently omit required fields. | Complete the SYSTEM_ANALYSE prompt with the full expected JSON schema and validate the Claude response against that schema before writing the output file. |
| MEDIUM | security | `.github/scripts/tool5_uat.py` | None | User-supplied UAT results CSV data is passed directly to Claude without sanitisation, which could allow prompt injection if the CSV contains adversarial content. | Sanitise CSV cell values before concatenating them into the Claude prompt, stripping or escaping sequences that could override system instructions. |
| MEDIUM | performance | `.github/scripts/shared.py` | 22 | A new Anthropic client is instantiated on every call to call_claude, which adds unnecessary overhead for workflows that make multiple sequential API calls. | Instantiate the Anthropic client once at module level or use a module-level singleton pattern to reuse the connection across multiple calls. |
| LOW | maintainability | `.github/scripts/tool4_auto_testing.py` | None | The SYSTEM_GAP prompt is truncated mid-JSON-schema, meaning the gap analysis mode has an incomplete prompt that will produce unpredictable Claude output. | Complete the SYSTEM_GAP prompt with the full JSON schema definition including all required fields before deploying this tool. |
| LOW | maintainability | `.github/workflows/tool2_tech_docs.yml` | None | The tech docs workflow triggers on every push to main with no concurrency group defined, meaning rapid successive pushes will queue multiple documentation generation runs unnecessarily. | Add a concurrency group to the workflow with cancel-in-progress set to true to avoid redundant runs on rapid pushes. |
| LOW | iac | `.github/workflows/tool1_code_review.yml` | None | No permissions block is defined at the workflow or job level, so the job inherits the default GITHUB_TOKEN permissions which may be broader than necessary. | Add an explicit permissions block to each workflow job granting only the minimum required permissions such as pull-requests write and contents read. |
| LOW | iac | `.github/workflows/tool5_uat.yml` | None | The workflow triggers on the create event without filtering for release branch patterns, meaning it will fire for every branch and tag creation including feature branches. | Add a conditional step or filter to check that the created ref matches a release branch naming pattern such as release before proceeding with UAT generation. |

## IaC Findings
- No workflow-level or job-level permissions blocks are defined across any of the five workflow files, meaning all jobs run with default token permissions that may include unnecessary write scopes.
- The GH_TOKEN secret appears to be a personal access token rather than a fine-grained token, which typically grants broad cross-repository access beyond what these workflows require.
- The tool5_uat.yml workflow triggers on the create event without branch pattern filtering, causing unnecessary workflow runs on all branch and tag creations.
- No concurrency groups are defined on any workflow, allowing duplicate runs to queue up on rapid pushes or manual dispatches.
- No timeout-minutes is set on any job, meaning a hung Claude API call or network issue could consume GitHub Actions minutes indefinitely.
- The pip install steps use unpinned dependency versions for anthropic and requests, creating a supply chain risk where a dependency update could break or compromise all five tools.
- No environment protection rules or required reviewers are configured for production deployments triggered by version tags in tool3_business_docs.yml.

## Positive Observations
- All sensitive credentials including ANTHROPIC_API_KEY, GH_TOKEN, and SENDGRID_API_KEY are correctly stored as GitHub secrets and injected via environment variables rather than hardcoded.
- The clean_json utility function in shared.py correctly handles Claude markdown fence stripping, showing awareness of LLM output variability.
- Workflow files correctly use FORCE_JAVASCRIPT_ACTIONS_TO_NODE24 for forward compatibility with GitHub Actions runner updates.
- All five tools follow a consistent architectural pattern with shared utilities, making the codebase maintainable and reducing code duplication.
- The UAT tool correctly separates generate and analyse modes, demonstrating good single-responsibility design for AI-assisted workflows.
- The code review tool system prompt includes a well-structured JSON schema contract with clear rules for severity and category enumeration, reducing hallucination risk.
- Scheduled cron triggers are staggered across different days and times, avoiding resource contention and rate limit issues.
- Use of output_repo pattern for writing AI-generated artefacts keeps generated content separate from source code, which is a sound architectural decision.

---
_Auto-generated by AI Delivery Bot (claude-sonnet-4-6)_
