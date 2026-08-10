# Code Review Report
**Source:** kylodeng/Insurance-Training-Bot-main
**Context:** 20260810
**Generated:** 2026-08-10 09:16 UTC
**Score:** 62/100 | **Recommendation:** `REQUEST_CHANGES`

## Summary
The repository implements a multi-tool AI-assisted delivery pipeline with reasonable structure but contains several security, maintainability, and operational concerns that should be addressed before production use.

## Findings
| Severity | Category | File | Line | Issue | Recommendation |
|---|---|---|---|---|---|
| HIGH | security | `.github/scripts/shared.py` | 13 | Hardcoded personal email addresses for NOTIFY_EMAIL and SENDER_EMAIL are embedded directly in source code, creating a privacy and maintenance risk. | Move all email addresses to environment variables or GitHub Actions secrets and remove hardcoded defaults from source. |
| HIGH | security | `.github/workflows/tool1_code_review.yml` | 24 | Personal email address kylo.deng@capco.com is hardcoded in workflow environment variables, leaking PII into the public repository configuration. | Replace hardcoded email values in all workflow files with GitHub Actions secrets such as secrets.NOTIFY_EMAIL and secrets.SENDER_EMAIL. |
| HIGH | security | `.github/scripts/shared.py` | 9 | API keys are accessed via os.environ with hard failure on missing keys, but there is no validation or masking logic, meaning keys could appear in exception tracebacks logged to CI. | Wrap secret access in a helper that masks values in exceptions and validates presence at startup with a clear error message rather than a raw KeyError. |
| HIGH | security | `.github/workflows/tool1_code_review.yml` | 19 | The GH_TOKEN secret is exposed as a plain environment variable across all workflow steps, giving every step access to the token even those that do not need it. | Scope the GH_TOKEN to only the specific steps that require GitHub API access rather than declaring it as a job-level environment variable. |
| HIGH | security | `.github/workflows/tool3_business_docs.yml` | 38 | The workflow_dispatch input project_name is interpolated directly into a shell echo command without sanitisation, creating a potential shell injection vector. | Quote all input interpolations in shell steps and validate inputs against an allowlist or use environment variable indirection to prevent injection. |
| MEDIUM | security | `.github/scripts/tool5_uat.py` | 1 | The tool imports csv and io for processing test result sheets but there is no input validation or size limit on the CSV data read from external sources. | Add input validation, file size limits, and sanitisation when reading CSV files to prevent resource exhaustion or injection attacks. |
| MEDIUM | correctness | `.github/scripts/shared.py` | 1 | The get_repo_files function docstring is truncated mid-sentence indicating the file is incomplete and functionality may be missing or broken. | Ensure the complete source file is present in the repository and review all truncated files for missing logic. |
| MEDIUM | correctness | `.github/scripts/tool1_code_review.py` | 1 | The extract_json function body is truncated, meaning the JSON parsing logic for Claude responses is incomplete and would cause runtime failures. | Restore the complete implementation of extract_json and all other truncated functions across the codebase. |
| MEDIUM | correctness | `.github/scripts/tool5_uat.py` | 1 | The SYSTEM_ANALYSE prompt string is truncated mid-JSON-structure, meaning the Claude prompt for defect report generation is malformed. | Restore the complete prompt string and add unit tests that verify prompt templates are non-empty and syntactically valid. |
| MEDIUM | maintainability | `.github/workflows/tool4_auto_testing.yml` | 1 | The TEST_MODE environment variable definition is truncated suggesting the workflow file is incomplete and may fail at runtime. | Ensure all workflow files are complete and add a workflow linting step using actionlint to catch syntax errors in CI. |
| MEDIUM | performance | `.github/scripts/shared.py` | 29 | A new anthropic.Anthropic client instance is created on every call_claude invocation, causing unnecessary connection overhead for workflows that make multiple Claude calls. | Instantiate the Anthropic client once at module level or pass it as a parameter to avoid repeated client creation. |
| MEDIUM | maintainability | `.github/scripts/shared.py` | 17 | The MODEL constant is hardcoded as claude-sonnet-4-6 with no environment variable override, making it impossible to switch models without a code change. | Read the model name from an environment variable with a sensible default so it can be overridden per deployment or workflow. |
| MEDIUM | security | `.github/workflows/deploy.yml` | 1 | The deploy workflow does not pin third-party actions to a specific commit SHA, making it vulnerable to supply chain attacks if an action tag is moved. | Pin all GitHub Actions to their full commit SHA rather than mutable version tags such as v4 or v3. |
| MEDIUM | iac | `.github/workflows/deploy.yml` | 1 | Both deploy-api and deploy-frontend jobs run on ubuntu-latest which is a mutable tag that can change unexpectedly between runs causing non-reproducible builds. | Pin the runner to a specific version such as ubuntu-24.04 to ensure reproducible deployments. |
| LOW | maintainability | `.github/scripts/tool2_tech_docs.py` | 1 | The SYSTEM_ARCH prompt is truncated mid-sentence, suggesting incomplete instructions are being sent to Claude which will produce inconsistent documentation output. | Restore the complete prompt and add a startup assertion that validates all prompt strings are non-empty and do not end with partial words. |
| LOW | maintainability | `.github/scripts/tool3_business_docs.py` | 1 | The SYSTEM prompt contains template placeholders such as project_name and version using Python str.format-style braces but the substitution mechanism is not visible in the provided code. | Verify that all template placeholders are consistently substituted before the prompt is sent to Claude and add a test for this logic. |
| LOW | correctness | `.github/scripts/tool4_auto_testing.py` | 1 | The SYSTEM_GAP prompt is truncated mid-JSON-structure meaning the QA gap analysis prompt is malformed and Claude will likely return an error or unpredictable output. | Restore the complete prompt and validate all prompt constants in a unit test suite. |
| LOW | maintainability | `.github/workflows/tool1_code_review.yml` | 1 | The final workflow step Run Claude code review is truncated and its run command is missing, so the workflow will fail to parse or execute. | Restore the complete workflow definition and add a YAML linting step to catch truncation or syntax issues in CI. |

## IaC Findings
- The Azure App Service deployment does not show any slot-based blue-green or canary deployment strategy, meaning deployments are applied directly to production with no rollback buffer.
- No infrastructure-as-code files such as Bicep ARM or Terraform are visible for the Azure App Services, making the infrastructure configuration opaque and unversioned.
- The deploy workflow does not include a smoke test or health check step after deployment to verify the service is healthy before considering the deployment successful.
- There is no environment protection rule or manual approval gate on the deploy jobs meaning any push to main immediately deploys to production.
- The requirements.txt is generated ephemerally during the workflow run rather than being committed, which means the exact dependency set deployed is not permanently recorded for audit purposes.
- No OIDC federated identity is used for Azure authentication; instead a long-lived publish profile secret is used which has a broader blast radius if compromised.
- Missing deployment environment declarations in the workflow means GitHub cannot enforce environment-specific secrets, reviewers, or wait timers.

## Positive Observations
- Secrets are correctly sourced from GitHub Actions secrets rather than being hardcoded as literal values in workflow files.
- The shared.py module centralises common utilities such as API clients, email sending, and audit logging promoting the DRY principle across all five tools.
- The clean_json helper defensively strips markdown fences from Claude responses which is a practical and necessary safety measure.
- Workflow triggers are well-designed using a sensible mix of pull_request, schedule, push, and workflow_dispatch events appropriate to each tools purpose.
- The deploy workflow correctly gates deployment jobs behind a passing test job using the needs directive.
- Use of uv for dependency management and lockfile-based installs in the deploy workflow promotes reproducibility.
- The UAT tool sensibly separates two distinct operational modes generate and analyse rather than combining them into a single overloaded script.
- The Claude prompts include explicit output format constraints and rules which reduces hallucination risk and makes downstream parsing more reliable.
- The FORCE_JAVASCRIPT_ACTIONS_TO_NODE24 flag is set consistently across all workflows showing awareness of Node.js runtime deprecations.

---
_Auto-generated by AI Delivery Bot (claude-sonnet-4-6)_
