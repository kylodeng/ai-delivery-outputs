# Code Review Report
**Source:** kylodeng/Insurance-Training-Bot-main
**Context:** 20261123
**Generated:** 2026-11-23 19:48 UTC
**Score:** 62/100 | **Recommendation:** `REQUEST_CHANGES`

## Summary
The codebase implements a multi-tool AI-assisted delivery pipeline with reasonable structure, but contains several security concerns including hardcoded email addresses, missing error handling, and workflow security gaps that should be addressed before production use.

## Findings
| Severity | Category | File | Line | Issue | Recommendation |
|---|---|---|---|---|---|
| HIGH | security | `.github/scripts/shared.py` | 13 | Personal email address kylo.deng@capco.com is hardcoded as a default for NOTIFY_EMAIL and SENDER_EMAIL, leaking PII into source control. | Remove all hardcoded email defaults and require them to be set exclusively via GitHub Actions secrets or environment variables with no fallback. |
| HIGH | security | `.github/scripts/shared.py` | 9 | ANTHROPIC_API_KEY and GH_TOKEN are accessed with os.environ[] which raises KeyError and may expose secret names in unhandled exception stack traces. | Wrap secret retrieval in a startup validation function that exits cleanly with a non-revealing error message if required secrets are absent. |
| HIGH | security | `.github/workflows/tool1_code_review.yml` | None | The workflow triggers on pull_request events which in a public repository can allow untrusted forks to execute workflows with access to repository secrets. | Change the pull_request trigger to pull_request_target with explicit head SHA pinning, or restrict secret access using environment protection rules. |
| HIGH | security | `.github/workflows/tool5_uat.py` | None | The file imports base64 and csv but CSV data from external sources is parsed without sanitisation, risking CSV injection if test results contain formula-triggering characters. | Sanitise all CSV cell values by stripping or escaping leading =, +, -, and @ characters before processing or writing outputs. |
| MEDIUM | security | `.github/workflows/tool1_code_review.yml` | None | GH_TOKEN is exposed as a plain environment variable at the job level, making it available to all steps including any third-party actions. | Scope the GH_TOKEN environment variable only to the specific step that requires it rather than setting it at the job level. |
| MEDIUM | security | `.github/workflows/tool3_business_docs.yml` | None | The workflow_dispatch input project_name is interpolated directly into environment variables without sanitisation, allowing potential injection via crafted input values. | Validate and sanitise all workflow_dispatch inputs before using them in env blocks or shell steps. |
| MEDIUM | maintainability | `.github/scripts/shared.py` | 20 | The MODEL constant is hardcoded as claude-sonnet-4-6 with no mechanism to override it at runtime, making model upgrades require code changes. | Read the model name from an environment variable with the current value as a default to allow runtime overrides without code changes. |
| MEDIUM | correctness | `.github/scripts/tool1_code_review.py` | None | The extract_json function is defined but its full implementation is truncated in the provided snippet, so error handling completeness cannot be verified. | Ensure extract_json raises a descriptive exception with the raw Claude response when no valid JSON can be extracted, rather than silently returning None or an empty dict. |
| MEDIUM | correctness | `.github/workflows/tool4_auto_testing.yml` | None | The TEST_MODE environment variable assignment is truncated mid-value with gene which will cause a YAML parse error or incorrect value at runtime. | Complete the environment variable value to its intended full string such as generate and validate the YAML file parses correctly before merging. |
| MEDIUM | performance | `.github/scripts/shared.py` | 32 | A new Anthropic client instance is created on every call to call_claude, adding unnecessary object initialisation overhead in high-frequency invocations. | Instantiate the Anthropic client once at module level or use a module-level singleton to reuse the connection across calls. |
| MEDIUM | maintainability | `.github/workflows/deploy.yml` | None | Both deploy-api and deploy-frontend jobs duplicate identical checkout and uv setup steps with no shared reusable workflow or composite action. | Extract the common setup steps into a reusable composite action or workflow to reduce duplication and simplify future maintenance. |
| LOW | maintainability | `.github/scripts/tool2_tech_docs.py` | None | The SYSTEM_ARCH prompt string is truncated mid-sentence at Mark un making the architecture document instructions incomplete. | Complete the SYSTEM_ARCH prompt string to include all intended instructions and add a test that validates prompt strings are non-empty and syntactically complete. |
| LOW | maintainability | `.github/scripts/tool3_business_docs.py` | None | The SYSTEM prompt template uses Python-style format placeholders such as project_name and version but it is unclear whether these are safely interpolated or left as literal strings. | Use a named templating approach such as str.format_map or Jinja2 with explicit variable injection rather than relying on implicit placeholder replacement. |
| LOW | correctness | `.github/scripts/shared.py` | None | The get_repo_files function docstring is truncated mid-sentence at Fetch tex, leaving its behaviour and return type undocumented. | Complete the docstring to describe parameters, return type, exception behaviour, and any rate-limit or pagination considerations. |

## IaC Findings
- Azure App Service deploy steps in deploy.yml do not pin the azure/webapps-deploy action to a specific commit SHA, leaving the pipeline vulnerable to upstream action supply-chain attacks.
- No slot-based deployment or blue-green strategy is configured for Azure App Service, meaning deployments will cause downtime if the app restarts during update.
- The workflow does not configure any Azure health check or smoke test step after deployment to verify the service is healthy before the run completes.
- There is no explicit environment protection rule or approval gate on the deploy jobs, allowing any push to main to immediately deploy to what may be a production environment.
- No resource tagging strategy is visible in the deployment configuration, which will hinder cost attribution and resource governance in Azure.

## Positive Observations
- Secrets are correctly sourced from GitHub Actions secrets rather than being hardcoded in workflow files.
- The clean_json utility defensively strips markdown fences from Claude responses, improving robustness of JSON parsing.
- Workflow files use pinned major versions of official actions such as actions/checkout@v4, reducing supply-chain risk.
- The deploy workflow correctly gates deployment jobs on the test job passing via the needs dependency.
- Separation of concerns is well-structured with shared utilities centralised in shared.py and each tool in its own script.
- Claude prompts include strict output format rules and fallback instructions such as TODO markers, improving reliability of generated content.
- The tool4 system prompt explicitly forbids real calls to external services in generated tests, enforcing safe mock-based testing.
- The UAT tool implements both generate and analyse modes in a single script, providing a complete test lifecycle workflow.

---
_Auto-generated by AI Delivery Bot (claude-sonnet-4-6)_
