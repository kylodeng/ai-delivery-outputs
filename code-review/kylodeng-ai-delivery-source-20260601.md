# Code Review Report
**Source:** kylodeng/ai-delivery-source
**Context:** 20260601
**Generated:** 2026-06-01 13:34 UTC
**Score:** 62/100 | **Recommendation:** `REQUEST_CHANGES`

## Summary
A well-structured AI-powered CI/CD toolchain that correctly uses environment secrets but contains several medium-to-high severity issues including hardcoded email addresses, missing error handling, overly broad workflow permissions, and potential secret exposure risks.

## Findings
| Severity | Category | File | Line | Issue | Recommendation |
|---|---|---|---|---|---|
| HIGH | security | `.github/scripts/shared.py` | 13 | Hardcoded personal email address kylo.deng@capco.com is embedded directly in source code as a default fallback for NOTIFY_EMAIL and SENDER_EMAIL. | Remove all hardcoded email defaults and require these values to be explicitly set as repository secrets or environment variables with no fallback. |
| HIGH | security | `.github/scripts/shared.py` | 9 | Using os.environ with direct key access for ANTHROPIC_API_KEY, GH_TOKEN, and SENDGRID_API_KEY will raise unhandled KeyError exceptions if secrets are missing, potentially leaking partial stack traces. | Use os.environ.get with explicit validation and raise a descriptive error that does not echo the key name or any partial secret values. |
| HIGH | security | `.github/workflows/tool1_code_review.yml` | None | No permissions block is defined on the workflow or job, so the GITHUB_TOKEN receives the repository default permissions which may be overly broad. | Add an explicit permissions block scoped to the minimum required, for example permissions: contents-read and pull-requests-write only. |
| HIGH | security | `.github/workflows/tool5_uat.yml` | None | The workflow triggers on the create event without a branch filter, meaning it fires for every tag creation as well as every branch creation including potentially attacker-controlled branch names. | Add a branch filter such as branches starting with release to restrict the create trigger to intended release branches only. |
| HIGH | security | `.github/workflows/tool1_code_review.yml` | None | The pull_request trigger with types including synchronize means the workflow runs against potentially untrusted fork code that has access to repository secrets via the env block. | Use pull_request_target instead of pull_request for fork PRs and ensure secrets are not exposed to untrusted code paths, or restrict triggers to internal contributors only. |
| MEDIUM | security | `.github/scripts/tool5_uat.py` | None | User-supplied uat_results_path input from workflow_dispatch is used to construct a file path in the output repo without visible sanitisation, creating a potential path traversal risk. | Validate and sanitise the uat_results_path input against an allowlist pattern before using it to construct any file or API path. |
| MEDIUM | security | `.github/scripts/shared.py` | None | The GH_TOKEN is interpolated directly into an HTTP Authorization header string and stored as a module-level global, increasing the risk of accidental logging or exposure. | Construct GH_HEADERS lazily inside functions rather than at module level, and ensure request libraries do not log headers at any log level. |
| MEDIUM | correctness | `.github/scripts/shared.py` | None | The get_repo_files function signature is visible in the truncated code but its implementation is cut off, making it impossible to verify error handling for GitHub API rate limits or 404 responses. | Ensure get_repo_files handles HTTP errors explicitly including 401, 403, 404, and 429 with appropriate retry logic for rate limiting. |
| MEDIUM | maintainability | `.github/scripts/shared.py` | 17 | The Claude model name claude-sonnet-4-6 is hardcoded as a module-level constant with no way to override it via environment variable, making model version upgrades require code changes. | Read the model name from an environment variable such as CLAUDE_MODEL with the current name as a documented default. |
| MEDIUM | security | `.github/scripts/tool1_code_review.py` | None | The extract_json function attempts to parse raw Claude output without a size or content limit, making it possible for a maliciously crafted or excessively large response to cause memory or denial of service issues. | Add a maximum length check on the raw Claude response before parsing and raise an explicit error if the response exceeds a reasonable threshold. |
| MEDIUM | correctness | `.github/scripts/tool4_auto_testing.py` | None | Generated test files are written to the output repo without any validation that the generated code is syntactically valid, meaning broken tests could be silently committed. | Run a syntax check such as py_compile for Python or a lint pass on the generated test file before writing it to the output repository. |
| MEDIUM | maintainability | `.github/workflows/tool2_tech_docs.yml` | None | All five workflow files duplicate the same env block including hardcoded email addresses, repo names, and secret references with no shared reusable workflow or composite action. | Extract the common environment variables into a reusable workflow or a composite action to eliminate repetition and reduce the risk of inconsistency. |
| MEDIUM | security | `.github/scripts/tool3_business_docs.py` | None | User-supplied project_name and release_version inputs from workflow_dispatch are injected into the Claude prompt without sanitisation, which could allow prompt injection via crafted input values. | Sanitise and length-limit workflow_dispatch inputs before interpolating them into LLM prompts, and treat all user inputs as untrusted. |
| LOW | maintainability | `.github/scripts/tool2_tech_docs.py` | None | The SYSTEM_ARCH prompt string is visibly truncated in the reviewed code suggesting incomplete implementation of the architecture document generation instructions. | Ensure all system prompt strings are complete and stored in versioned prompt files rather than inline Python strings to make them easier to review and update. |
| LOW | performance | `.github/scripts/shared.py` | 27 | A new anthropic.Anthropic client instance is created on every call to call_claude rather than being reused, adding unnecessary initialisation overhead for workflows that make multiple calls. | Instantiate the Anthropic client once at module level or use a module-level singleton to reuse the connection across multiple calls. |
| LOW | correctness | `.github/scripts/shared.py` | 38 | The clean_json function assumes the first line is always the opening fence marker and splits on the first newline, which will silently corrupt responses that start with a newline before the fence. | Use a regex-based approach to reliably extract content between triple-backtick fences regardless of surrounding whitespace. |

## IaC Findings
- No permissions block is defined on any workflow file, so GITHUB_TOKEN defaults to the repository-level permission setting which is often write-all.
- The tool5_uat.yml workflow triggers on the create event without branch name filtering, causing unintended executions on tag creation events.
- None of the workflow jobs define a timeout-minutes value, meaning runaway Claude API calls or network hangs could consume GitHub Actions minutes indefinitely.
- Dependencies are installed with bare pip install anthropic requests without pinned versions or a requirements file, making builds non-deterministic and vulnerable to supply chain attacks.
- No concurrency group is defined on any workflow, so rapid PR synchronisation events or tag pushes could queue many parallel runs simultaneously increasing cost and API rate limit risk.
- The output repository name ai-delivery-outputs is hardcoded in every workflow env block rather than being a repository-level variable, making it difficult to change across all workflows simultaneously.

## Positive Observations
- All sensitive credentials are correctly sourced from GitHub Actions secrets rather than being hardcoded.
- The use of a shared utility module avoids code duplication across the five tool scripts for common operations like Claude calls, GitHub API access, and email sending.
- Workflow triggers are well considered with pull_request, schedule, and workflow_dispatch all supported giving good operational flexibility.
- The Claude prompt for code review explicitly instructs the model not to include code snippets or newlines in JSON string values, reducing parse failures.
- The clean_json helper demonstrates awareness that LLMs may wrap responses in markdown fences and handles this gracefully.
- The UAT tool supports two distinct modes generate and analyse within a single script, which is a clean design for a multi-phase workflow.
- Using FORCE_JAVASCRIPT_ACTIONS_TO_NODE24 across all workflows ensures consistent Node runtime behaviour for third-party actions.

---
_Auto-generated by AI Delivery Bot (claude-sonnet-4-6)_
