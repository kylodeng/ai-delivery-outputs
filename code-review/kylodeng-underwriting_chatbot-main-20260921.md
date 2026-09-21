# Code Review Report
**Source:** kylodeng/underwriting_chatbot-main
**Context:** 20260921
**Generated:** 2026-09-21 15:02 UTC
**Score:** 62/100 | **Recommendation:** `REQUEST_CHANGES`

## Summary
Well-structured AI delivery automation toolkit with good use of secrets management, but contains hardcoded email addresses, missing error handling, overly broad GitHub token permissions, and several security concerns that should be addressed before production use.

## Findings
| Severity | Category | File | Line | Issue | Recommendation |
|---|---|---|---|---|---|
| HIGH | security | `.github/scripts/shared.py` | 12 | Hardcoded personal email address kylo.deng@capco.com is embedded directly in source code as a default value for NOTIFY_EMAIL. | Remove all hardcoded email defaults from source code and require them to be set exclusively via repository secrets or environment variables with no fallback. |
| HIGH | security | `.github/scripts/shared.py` | 8 | ANTHROPIC_API_KEY is accessed with direct dict-style os.environ lookup which raises KeyError and may expose the key name in stack traces logged to CI. | Use os.environ.get with explicit error handling that logs a safe message without leaking variable names or values. |
| HIGH | security | `.github/workflows/tool1_code_review.yml` | 21 | GH_TOKEN secret is passed as a plain environment variable to all steps including pip install, meaning any compromised dependency could read it. | Scope the GH_TOKEN environment variable only to the specific step that requires it rather than the entire job environment. |
| HIGH | security | `.github/workflows/tool5_uat.yml` | None | The on.create trigger fires for every branch and tag creation, not just release branches, potentially running expensive and sensitive Claude API calls on unintended events. | Replace the bare on.create trigger with a conditional check in the job steps that filters for branch names matching a release pattern such as release or refs/heads/release. |
| HIGH | security | `.github/scripts/tool5_uat.py` | None | UAT results CSV data is read and passed directly to Claude without sanitisation, which could enable prompt injection if a tester embeds malicious instructions in test result fields. | Sanitise CSV field values by stripping or escaping characters that could be interpreted as prompt instructions before concatenating them into the Claude API call. |
| MEDIUM | security | `.github/workflows/tool1_code_review.yml` | None | No permissions block is defined on the workflow or job, so the GITHUB_TOKEN implicitly receives default read-write permissions on the repository. | Add an explicit permissions block at the job level granting only the minimum required permissions such as contents read and pull-requests write. |
| MEDIUM | security | `.github/workflows/tool2_tech_docs.yml` | None | No permissions block is defined, leaving the implicit GITHUB_TOKEN with broad default permissions for a workflow that triggers on every push to main. | Add a permissions block restricting the token to contents write and pull-requests none for this documentation generation workflow. |
| MEDIUM | security | `.github/scripts/shared.py` | 17 | The GH_HEADERS dictionary is constructed at module import time, meaning the token is stored in a long-lived module-level variable accessible to any code that imports shared. | Build the headers dictionary inside each function that needs it or use a function that retrieves the token freshly from the environment each time. |
| MEDIUM | correctness | `.github/scripts/shared.py` | 8 | Using os.environ with bracket syntax for all three API keys means a missing secret causes an unhandled KeyError that produces an unhelpful CI failure message. | Add an explicit startup validation function that checks all required environment variables and raises a descriptive ValueError listing which ones are missing. |
| MEDIUM | security | `.github/scripts/tool1_code_review.py` | None | PR diff content from external contributors is passed directly to Claude without any size limit enforcement, enabling denial-of-service via extremely large PRs exhausting API quota. | Add a maximum diff size check before calling Claude and truncate or reject inputs that exceed a defined byte threshold. |
| MEDIUM | maintainability | `.github/scripts/shared.py` | 15 | The MODEL constant is hardcoded as claude-sonnet-4-6 with no environment variable override, making model version updates require a code change and redeployment. | Read the model name from an environment variable with the current value as the default so it can be overridden without code changes. |
| MEDIUM | correctness | `.github/scripts/tool1_code_review.py` | None | The extract_json function is described as robustly handling Claude responses but the implementation is truncated in the review, making it impossible to verify error handling completeness. | Ensure extract_json wraps json.loads in a try-except and raises a descriptive exception with the raw Claude response included for debugging. |
| MEDIUM | security | `.github/workflows/tool4_auto_testing.yml` | None | The workflow triggers on pull_request path filters including root-level py, js, and ts files, meaning a malicious PR author could trigger AI-powered test generation on files they control. | Add a workflow approval requirement for PRs from first-time contributors or fork repositories to prevent abuse of API quota and data exfiltration via crafted source files. |
| LOW | maintainability | `.github/workflows/tool1_code_review.yml` | 22 | The personal email kylo.deng@capco.com is hardcoded in six separate workflow files, making it difficult to update the notification recipient without editing every file. | Store the notification email as a single repository-level variable or secret referenced by all workflows to allow centralised management. |
| LOW | maintainability | `.github/scripts/tool2_tech_docs.py` | None | The SYSTEM_ARCH prompt string appears to be truncated mid-sentence ending with Mark un, suggesting incomplete prompt content was committed. | Review and complete the SYSTEM_ARCH prompt string to ensure the architecture document instructions are fully specified. |
| LOW | maintainability | `.github/scripts/tool3_business_docs.py` | None | The SYSTEM prompt template string appears truncated at Go-live and milest suggesting the prompt was not fully committed to the repository. | Restore the complete prompt content and add a unit test that validates all expected section headings are present in each system prompt. |
| LOW | performance | `.github/scripts/shared.py` | 26 | A new anthropic.Anthropic client is instantiated on every call to call_claude rather than being reused, adding unnecessary overhead for workflows that make multiple sequential calls. | Create the Anthropic client once at module level or pass it as a parameter to call_claude to allow reuse across multiple calls. |

## IaC Findings
- No workflow-level or job-level permissions blocks are defined meaning all five workflows run with implicit broad GITHUB_TOKEN permissions violating least privilege.
- The on.create trigger in tool5_uat.yml has no branch name filter making it fire on all branch and tag creation events including automated dependency update branches.
- No concurrency groups are defined on any workflow meaning multiple simultaneous runs triggered by rapid pushes will race and may produce duplicate outputs or conflicting commits to the output repo.
- The pip install step across all workflows has no version pinning for the anthropic and requests packages meaning builds are not reproducible and could silently break on new releases.
- No timeout-minutes is set on any job meaning a hung Claude API call or GitHub API rate limit could leave a runner occupied indefinitely consuming Actions minutes.
- The output repo ai-delivery-outputs is referenced by name without verifying its existence or write access at workflow start, causing obscure failures late in the run.
- No secret scanning or SAST step is included in any workflow despite the tool1 code review purpose implying security review is left entirely to Claude with no deterministic baseline check.

## Positive Observations
- All sensitive credentials are correctly sourced from GitHub Actions secrets rather than being hardcoded in workflow files.
- The clean_json utility function correctly handles Claude markdown fence wrapping which is a common real-world failure mode.
- Workflows use pinned major versions of actions such as actions/checkout@v4 reducing supply chain risk.
- The five-tool architecture is well decomposed with clear single responsibilities per script and shared utilities centralised in shared.py.
- FORCE_JAVASCRIPT_ACTIONS_TO_NODE24 is consistently set across all workflows showing awareness of runtime compatibility.
- The UAT tool correctly separates generate and analyse modes with distinct system prompts suited to each task.
- The code review tool instructs Claude to prioritise security findings including hardcoded secrets and overly permissive IAM which aligns well with the tool purpose.
- Scheduled triggers are staggered across different days and times preventing concurrent API quota exhaustion.
- The output repo pattern correctly isolates generated artefacts from the source repository.

---
_Auto-generated by AI Delivery Bot (claude-sonnet-4-6)_
