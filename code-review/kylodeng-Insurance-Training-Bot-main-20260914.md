# Code Review Report
**Source:** kylodeng/Insurance-Training-Bot-main
**Context:** 20260914
**Generated:** 2026-09-14 14:42 UTC
**Score:** 62/100 | **Recommendation:** `REQUEST_CHANGES`

## Summary
The codebase implements a multi-tool AI-powered delivery pipeline with reasonable structure but contains several security, maintainability, and correctness concerns that should be addressed before wider adoption.

## Findings
| Severity | Category | File | Line | Issue | Recommendation |
|---|---|---|---|---|---|
| HIGH | security | `.github/scripts/shared.py` | 10 | API keys and tokens are read from environment variables at module import time without validation, meaning any import failure exposes which secrets are missing via unhandled KeyError exceptions. | Wrap secret reads in a helper that raises a descriptive, sanitised error rather than exposing the variable name in raw tracebacks, and validate all required secrets at startup with a clear error message. |
| HIGH | security | `.github/workflows/tool1_code_review.yml` | 22 | NOTIFY_EMAIL and SENDER_EMAIL are hardcoded to a personal email address directly in the workflow YAML committed to the repository, leaking PII into version control history. | Move email addresses to GitHub repository secrets or variables and reference them via the secrets context to avoid embedding PII in source control. |
| HIGH | security | `.github/workflows/tool2_tech_docs.yml` | 18 | Same PII leakage pattern as tool1 workflow with personal email hardcoded in YAML. | Replace hardcoded email values with GitHub secrets references such as secrets.NOTIFY_EMAIL across all workflow files. |
| HIGH | security | `.github/workflows/tool3_business_docs.yml` | 21 | Personal email address kylo.deng@capco.com is hardcoded in the workflow environment configuration. | Use repository or organisation-level secrets to store email addresses and reference them consistently across all workflow files. |
| HIGH | security | `.github/scripts/shared.py` | 18 | The GH_TOKEN is interpolated directly into an HTTP Authorization header string at module load time, meaning the token value lives in memory as a plain string in a globally shared dict for the lifetime of the process. | Build the Authorization header lazily per-request inside the function that makes the API call to limit the token exposure window. |
| HIGH | correctness | `.github/scripts/shared.py` | 10 | ANTHROPIC_API_KEY and GH_TOKEN use hard-indexed os.environ access which will raise a bare KeyError with no context if the secret is not set, causing cryptic CI failures. | Use os.environ.get with an explicit check and raise a ValueError with a human-readable message listing the missing variable name. |
| HIGH | security | `.github/scripts/tool5_uat.py` | None | The script imports base64 and requests at the top level alongside shared imports, suggesting manual base64 encoding of content being sent to GitHub API, which can mask injection of malicious content if inputs are not sanitised. | Ensure all content written to the GitHub API via base64-encoded blobs is sanitised and validated before encoding, and document the encoding purpose explicitly. |
| MEDIUM | security | `.github/scripts/tool1_code_review.py` | None | The extract_json function strips markdown fences from Claude responses before parsing, but there is no size or depth limit on the parsed JSON, creating a potential denial-of-service vector if Claude returns a very large response. | Enforce a maximum response size check before calling json.loads and set a reasonable max_tokens ceiling appropriate to the expected output size. |
| MEDIUM | security | `.github/workflows/deploy.yml` | None | The deploy workflow uses azure/webapps-deploy@v3 without pinning to a specific commit SHA, allowing a compromised action version to execute arbitrary code in the deployment context. | Pin all third-party GitHub Actions to their full commit SHA rather than a mutable version tag to prevent supply chain attacks. |
| MEDIUM | security | `.github/workflows/tool1_code_review.yml` | None | The workflow triggers on pull_request events including from forks, and injects secrets into the environment, which exposes secrets to untrusted code running in PR workflows from external contributors. | Use pull_request_target with explicit head SHA checkout only after review, or restrict secret access using environment protection rules and required reviewers. |
| MEDIUM | maintainability | `.github/scripts/shared.py` | 20 | The MODEL constant is hardcoded to a specific Claude model string rather than being configurable via environment variable, making model upgrades require a code change and redeployment. | Read the model name from an environment variable with the current value as default so it can be overridden without code changes. |
| MEDIUM | correctness | `.github/scripts/tool3_business_docs.py` | None | The SYSTEM prompt template uses Python format-style placeholders such as {project_name} and {version} inside a plain string, which will be passed verbatim to Claude if string formatting is not explicitly applied before use. | Apply str.format or f-string substitution on the system prompt before passing it to call_claude to ensure dynamic values are correctly injected. |
| MEDIUM | correctness | `.github/workflows/tool4_auto_testing.yml` | None | The TEST_MODE environment variable value is truncated mid-expression at the end of the visible file content, suggesting the workflow file may be malformed or incomplete. | Ensure the complete workflow YAML is committed and validate it with a YAML linter and the GitHub Actions schema validator before merging. |
| MEDIUM | performance | `.github/scripts/shared.py` | 27 | A new Anthropic client is instantiated on every call to call_claude rather than being reused, adding unnecessary object creation and connection overhead for workloads that make multiple sequential Claude calls. | Instantiate the Anthropic client once at module level or use a module-level singleton pattern to reuse the connection across calls. |
| MEDIUM | maintainability | `.github/scripts/shared.py` | None | The get_repo_files docstring is truncated mid-sentence indicating incomplete documentation in the committed file. | Complete all docstrings and enforce documentation completeness in the CI pipeline using a tool such as pydocstyle or interrogate. |
| MEDIUM | maintainability | `.github/scripts/tool2_tech_docs.py` | None | The SYSTEM_ARCH prompt string is truncated mid-word in the visible content, indicating the file may be incomplete or truncated in the repository. | Verify and restore the complete file content and add a CI step that checks file integrity to prevent partial commits. |
| LOW | maintainability | `.github/scripts/tool4_auto_testing.py` | None | The SYSTEM_GAP prompt JSON structure is truncated mid-definition, suggesting the string literal is not properly closed in the source file. | Complete the string literal, validate all multi-line strings in CI, and consider storing large prompts in separate text files to avoid truncation issues. |
| LOW | maintainability | `.github/scripts/tool5_uat.py` | None | The SYSTEM_ANALYSE return JSON structure is truncated, indicating the prompt definition is incomplete in the committed code. | Restore the complete prompt string and add a syntax validation step to CI that imports all Python scripts to catch truncated string literals. |
| LOW | maintainability | `.github/workflows/tool1_code_review.yml` | None | The final workflow step definition is truncated mid-line with the run command cut off, indicating an incomplete workflow file. | Complete the workflow step and add YAML schema validation to the CI pipeline to catch malformed workflow files before they are merged. |
| LOW | performance | `.github/workflows/deploy.yml` | None | Both deploy-api and deploy-frontend jobs independently run uv export to generate requirements.txt, duplicating work that could be shared via a build artifact. | Extract requirements generation into the test job as an artifact upload and download it in both deploy jobs to avoid redundant work. |

## IaC Findings
- Azure App Service deployments use publish profiles stored as secrets which is acceptable but certificate rotation and expiry are not managed in the pipeline.
- No infrastructure-as-code files are visible in the repository, meaning the Azure App Service configuration is entirely manual and not reproducible from code.
- There is no environment separation visible between staging and production deployments; all pushes to main deploy directly to named production app services.
- No health check or smoke test step exists after deployment to validate the service is responding before the workflow completes successfully.
- The deploy workflow has no rollback mechanism defined for failed deployments, creating risk of prolonged outages if a bad build is deployed.
- No resource tagging strategy is visible for the Azure resources, which will complicate cost attribution and governance auditing.
- The output repository ai-delivery-outputs has no access control policy defined in the visible configuration, potentially allowing any workflow actor to write audit logs.

## Positive Observations
- Secrets are correctly sourced from GitHub Actions secrets context rather than being hardcoded as literal values in workflow files.
- The clean_json utility function defensively handles Claude returning markdown-fenced JSON, improving robustness of AI response parsing.
- Workflows use actions/checkout@v4 and actions/setup-python@v5 which are relatively current major versions.
- The deploy workflow correctly gates deployment jobs on the test job passing via the needs directive.
- Tool workflows use FORCE_JAVASCRIPT_ACTIONS_TO_NODE24 to ensure consistent Node.js runtime for JavaScript actions.
- The codebase shows good separation of concerns with shared utilities isolated in shared.py and each tool in its own script.
- The Claude system prompts include explicit output format constraints and validation rules which reduces unpredictable model behaviour.
- The architecture document generation prompt explicitly instructs Claude to flag missing encryption and overly broad IAM, demonstrating security awareness.
- Workflow schedules are staggered across different days and times to avoid resource contention.
- The UAT tool supports two distinct operational modes (generate and analyse) making it versatile for different pipeline stages.

---
_Auto-generated by AI Delivery Bot (claude-sonnet-4-6)_
