# Code Review Report
**Source:** kylodeng/Insurance-Training-Bot-main
**Context:** 20260713
**Generated:** 2026-07-13 10:58 UTC
**Score:** 62/100 | **Recommendation:** `REQUEST_CHANGES`

## Summary
The repository implements a multi-tool AI-powered delivery automation system using Claude and GitHub Actions, with generally sound structure but several security, maintainability, and correctness concerns that should be addressed before production use.

## Findings
| Severity | Category | File | Line | Issue | Recommendation |
|---|---|---|---|---|---|
| HIGH | security | `.github/scripts/shared.py` | 12 | The NOTIFY_EMAIL and SENDER_EMAIL values are hardcoded with a real employee email address directly in source code, which leaks PII and organisational details publicly. | Move all email addresses to GitHub secrets or repository variables and reference them via environment variables without defaults in source code. |
| HIGH | security | `.github/workflows/tool1_code_review.yml` | 20 | The NOTIFY_EMAIL and SENDER_EMAIL values are hardcoded with a real personal email address in every workflow file, exposing PII in the public repository. | Replace hardcoded email addresses in all workflow files with GitHub Actions secrets such as secrets.NOTIFY_EMAIL and secrets.SENDER_EMAIL. |
| HIGH | security | `.github/scripts/shared.py` | 8 | ANTHROPIC_API_KEY and GH_TOKEN are accessed with direct dict-style os.environ access that raises KeyError and crashes with no helpful error message if secrets are missing. | Use os.environ.get with explicit None checks and raise a descriptive RuntimeError listing which required secrets are absent before any API calls are made. |
| HIGH | security | `.github/workflows/tool1_code_review.yml` | 1 | The GH_TOKEN secret is exposed as a plain environment variable at the job level, making it available to all steps including any third-party actions in the workflow. | Scope the GH_TOKEN environment variable only to the specific step that requires it, or use the built-in GITHUB_TOKEN with minimum required permissions. |
| HIGH | correctness | `.github/scripts/shared.py` | 1 | The shared.py file is truncated mid-function in the get_repo_files docstring, meaning the actual implementation of several critical shared utilities is missing from the review. | Ensure the complete source files are provided for review; truncated code cannot be properly assessed for security or correctness. |
| MEDIUM | security | `.github/workflows/tool2_tech_docs.yml` | 1 | All five workflow files pass SENDGRID_API_KEY as a plain environment variable at the job level, making it available to every step including checkout and setup actions. | Pass the SENDGRID_API_KEY only to the specific script execution step that requires it to minimise the blast radius of a supply-chain compromise. |
| MEDIUM | security | `.github/scripts/tool1_code_review.py` | 1 | The tool1_code_review.py file is truncated mid-function in extract_json, so it is impossible to verify whether Claude responses are safely parsed or whether injection via crafted diff content is possible. | Ensure JSON parsing from Claude responses uses try-except blocks and validates the schema before using any returned values in API calls or file writes. |
| MEDIUM | maintainability | `.github/scripts/shared.py` | 18 | The MODEL constant is hardcoded as a string literal claude-sonnet-4-6 rather than being sourced from an environment variable, making model upgrades require code changes. | Add a MODEL environment variable with os.environ.get so the model can be overridden per workflow run without modifying source code. |
| MEDIUM | correctness | `.github/workflows/tool4_auto_testing.yml` | 1 | The TEST_MODE environment variable assignment is truncated with an incomplete expression referencing inputs.test_mode, which will cause a syntax error in the workflow. | Complete the environment variable expression to correctly default to generate when the workflow is not triggered by a manual dispatch. |
| MEDIUM | security | `.github/scripts/tool5_uat.py` | 1 | The tool5_uat.py imports csv and io modules suggesting it processes uploaded test result CSV files, but no input sanitisation or size limits are visible before passing content to Claude. | Add file size limits, content-type validation, and strip or escape CSV content before including it in Claude prompts to prevent prompt injection via crafted test result files. |
| MEDIUM | performance | `.github/scripts/shared.py` | 31 | A new anthropic.Anthropic client is instantiated on every call to call_claude rather than being created once at module level, adding unnecessary overhead for workflows that make multiple calls. | Instantiate the Anthropic client once at module level and reuse it across all call_claude invocations. |
| MEDIUM | maintainability | `.github/workflows/deploy.yml` | 1 | The deploy workflow pins Python to 3.13 while all AI tool workflows pin to 3.12, creating an inconsistency that could cause dependency resolution differences between test and production environments. | Standardise on a single Python version across all workflows, or extract the version into a shared workflow-level variable. |
| MEDIUM | correctness | `.github/scripts/tool3_business_docs.py` | 1 | The SYSTEM prompt template references format placeholders such as project_name, version, and date that appear to require Python str.format substitution, but it is unclear whether this substitution is actually performed before sending to Claude. | Verify that the system prompt string is formatted with actual values before being passed to call_claude, and add a unit test to confirm placeholder substitution. |
| LOW | maintainability | `.github/scripts/tool1_code_review.py` | 1 | Multiple scripts import from shared using sys.path.insert manipulation rather than a proper package structure, making the codebase fragile to directory changes. | Convert the scripts directory into a proper Python package with an __init__.py or use a pyproject.toml with the scripts installed as an editable package. |
| LOW | maintainability | `.github/workflows/tool1_code_review.yml` | 1 | pip install anthropic requests is used in all AI tool workflows without version pinning, meaning a breaking upstream release could silently break all five tools simultaneously. | Pin dependency versions in all workflow install steps or use a requirements.txt with locked versions to ensure reproducible builds. |
| LOW | security | `.github/scripts/tool2_tech_docs.py` | 1 | The architecture document system prompt instructs Claude to flag overly broad IAM roles but the instruction is truncated, so the completeness of the security guidance given to Claude cannot be verified. | Ensure the full system prompt is present in source control and not truncated, and add a test that verifies the prompt string contains all required security-review instructions. |

## IaC Findings
- The Azure App Service deploy workflow does not set any environment variables or application settings for the deployed app, so secrets management at runtime is unverified.
- There is no infrastructure-as-code visible in the repository for the Azure App Service resources, meaning the compute environment is not version-controlled or reproducible.
- The deploy workflow does not include a smoke test or health check step after deployment, so failed deployments may go undetected until user impact occurs.
- No staging or pre-production environment is defined in the workflow; code deploys directly to what appears to be a production App Service on every push to main.
- The Azure webapps-deploy action does not specify a slot for deployment, preventing blue-green or canary deployment strategies that would reduce downtime risk.
- There is no workflow step to roll back the Azure deployment if post-deployment tests fail, creating a risk of prolonged outages from bad releases.

## Positive Observations
- Secrets are correctly sourced from GitHub Actions secrets rather than being hardcoded as credential values in workflow files.
- The clean_json utility in shared.py defensively handles markdown fences that Claude may add around JSON responses, reducing parse failures.
- The Claude system prompts are well-structured with explicit output format schemas, severity enumerations, and strict rules that reduce hallucination risk.
- The deploy workflow correctly gates deployment jobs on the test job completing successfully using the needs keyword.
- Workflow triggers are thoughtfully designed with scheduled runs, PR triggers, and manual dispatch options covering multiple automation scenarios.
- The use of a shared utility module avoids code duplication across five tool scripts and centralises GitHub and email API logic.
- The UAT tool correctly separates generate and analyse modes, allowing the same tool to support both test pack creation and results analysis.
- The architecture document prompt explicitly instructs Claude to be honest about security gaps and missing encryption rather than producing overly positive output.
- The actions/checkout action is pinned to v4 and other actions use versioned references, reducing supply-chain risk compared to using latest tags.

---
_Auto-generated by AI Delivery Bot (claude-sonnet-4-6)_
