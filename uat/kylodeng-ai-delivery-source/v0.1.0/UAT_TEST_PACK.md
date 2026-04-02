# UAT Test Pack — kylodeng/ai-delivery-source v0.1.0
**Generated:** 2026-04-02 12:45 UTC  
**Instructions:** Work through each scenario in order. Log PASS, FAIL, or BLOCKED in the CSV sheet.
For failures, note the defect reference. When complete, upload the CSV and trigger the UAT analysis workflow.

---

# UAT Test Pack — ai-delivery-source v0.1.0

**Project:** kylodeng/ai-delivery-source
**Version:** 0.1.0
**Prepared by:** UAT Test Manager (AI-generated)
**Date:** [TESTER: insert date]
**Status:** Draft — pending user story confirmation

---

> ⚠️ **NOTE:** No formal user stories were provided. All scenarios are inferred from code context (shared.py, tool1_code_review.py, tool2_tech_docs.py) and synthetic data. [TESTER: validate all scenarios against actual acceptance criteria before execution.]

---

## Feature Coverage Map

| Feature | Tool | Scenarios Generated |
|---|---|---|
| F1 — Code Review (PR Trigger) | tool1_code_review.py | UAT-F1-001 to UAT-F1-007 |
| F2 — Code Review (Manual/Cron Trigger) | tool1_code_review.py | UAT-F2-001 to UAT-F2-004 |
| F3 — Technical Documentation Generation | tool2_tech_docs.py | UAT-F3-001 to UAT-F3-006 |
| F4 — Audit Logging | shared.py | UAT-F4-001 to UAT-F4-004 |
| F5 — Email Notification | shared.py | UAT-F5-001 to UAT-F5-004 |
| F6 — Output File Writing (GitHub) | shared.py | UAT-F6-001 to UAT-F6-004 |
| F7 — Customer Data Validation | synthetic data | UAT-F7-001 to UAT-F7-006 |

---

===SCENARIO===
ID: UAT-F1-001
TITLE: Code review triggered on new PR open — valid diff returns structured JSON report
TYPE: POSITIVE
PERSONA: Data Engineer (full pipeline access)
PRE-CONDITIONS:
- GitHub repository kylodeng/ai-delivery-source is accessible
- A pull request is open with at least one modified Python file
- ANTHROPIC_API_KEY, GH_TOKEN, SENDGRID_API_KEY environment secrets are configured in GitHub Actions
- tool1_code_review.py workflow is enabled
TEST DATA: PR containing a change to a Python file with a bare `except:` clause and a hardcoded string resembling a password (e.g. `password = "Welcome1"`)
STEPS:
1. Navigate to kylodeng/ai-delivery-source on GitHub
2. Create a new branch from main called `test/uat-f1-001`
3. Commit a Python file containing: `password = "Welcome1"` and a bare `except: pass` block
4. Open a Pull Request from `test/uat-f1-001` targeting `main`
5. Observe GitHub Actions — confirm `tool1_code_review` workflow triggers automatically
6. Wait for workflow to complete (allow up to 5 minutes)
7. Inspect the PR comments section
8. Navigate to the output repo `ai-delivery-outputs` and locate the generated report file
EXPECTED RESULT: Workflow completes successfully; a PR comment is posted containing a code review summary; the output repo contains a JSON/markdown report with `severity: CRITICAL` or `HIGH` findings for hardcoded password and bare except; `merge_recommendation` is `REQUEST_CHANGES` or `BLOCK`; score is between 0–100
PASS CRITERIA: PR comment is present AND output file exists in ai-delivery-outputs AND JSON report is valid AND at least one finding with severity CRITICAL or HIGH references the hardcoded password
ESTIMATED TIME: 15
NOTES: Workflow trigger is `pull_request` event (open/sync). Ensure branch protection does not block the Actions runner. Score field must be an integer per SYSTEM prompt rules.

===SCENARIO===
ID: UAT-F1-002
TITLE: Code review on PR with clean, well-structured code returns APPROVE recommendation
TYPE: POSITIVE
PERSONA: Data Engineer (full pipeline access)
PRE-CONDITIONS:
- GitHub repository accessible with Actions enabled
- ANTHROPIC_API_KEY, GH_TOKEN, SENDGRID_API_KEY secrets configured
- tool1_code_review.py workflow enabled
TEST DATA: PR containing a simple, clean Python utility function with type hints, docstrings, proper error handling using specific exception types, no hardcoded credentials
STEPS:
1. Create branch `test/uat-f1-002` from main
2. Commit a clean Python file (e.g. a well-documented function with try/except ValueError)
3. Open Pull Request targeting `main`
4. Wait for `tool1_code_review` workflow to complete
5. Inspect PR comment for review content
6. Check `merge_recommendation` field in output JSON
EXPECTED RESULT: PR comment posted with positive review; `merge_recommendation` is `APPROVE`; `positive_observations` array is non-empty; score is >= 70; no CRITICAL or HIGH findings
PASS CRITERIA: `merge_recommendation` == `APPROVE` AND `score` >= 70 AND `positive_observations` list contains at least one entry
ESTIMATED TIME: 12
NOTES: Claude model is `claude-sonnet-4-6` — response quality may vary. Re-run once if result is borderline.

===SCENARIO===
ID: UAT-F1-003
TITLE: Code review JSON response structure is valid and complete against schema
TYPE: POSITIVE
PERSONA: Data Analyst (standard upload permissions, no admin)
PRE-CONDITIONS:
- A PR code review workflow run has completed successfully (can reuse output from UAT-F1-001)
- Output file is accessible in ai-delivery-outputs repo
TEST DATA: Output JSON from UAT-F1-001 run
STEPS:
1. Navigate to ai-delivery-outputs GitHub repository
2. Locate the most recent code review report file (path pattern: [TESTER: verify output path convention from write_output_file calls])
3. Download or view the raw JSON content
4. Validate the following fields are present: `summary`, `score`, `merge_recommendation`, `findings`, `positive_observations`, `iac_findings`
5. For each item in `findings`, confirm fields: `severity`, `category`, `file`, `line`, `issue`, `recommendation`
6. Confirm `severity` values are only: CRITICAL, HIGH, MEDIUM, LOW
7. Confirm `category` values are only: security, performance, maintainability, correctness, iac
8. Confirm `merge_recommendation` is one of: APPROVE, REQUEST_CHANGES, BLOCK
EXPECTED RESULT: All required top-level fields present; all findings conform to allowed enum values; score is an integer 0–100; no markdown fences or extra text wrapping the JSON
PASS CRITERIA: JSON parses without error AND all enum fields contain only permitted values AND score is integer 0–100
ESTIMATED TIME: 10
NOTES: `extract_json` function in tool1_code_review.py strips markdown fences — confirm this is working if raw Claude output is inspected in logs.

===SCENARIO===
ID: UAT-F1-004
TITLE: Code review triggered on PR sync (new commit pushed to existing open PR)
TYPE: POSITIVE
PERSONA: Data Engineer (full pipeline access)
PRE-CONDITIONS:
- An open PR exists (e.g. from UAT-F1-001)
- Workflow trigger includes PR `synchronize` event
TEST DATA: Additional commit pushed to `test/uat-f1-001` branch modifying a different file
STEPS:
1. On existing open PR branch `test/uat-f1-001`, add a new commit modifying a second Python file
2. Push the commit to the remote branch
3. Observe GitHub Actions — confirm `tool1_code_review` workflow re-triggers
4. Wait for completion
5. Verify a new PR comment is posted (not replacing the original)
6. Verify a new output file or updated file exists in ai-delivery-outputs
EXPECTED RESULT: Workflow re-triggers on push to open PR; new review comment posted; output repo updated
PASS CRITERIA: Second workflow run completes with status `success` AND new PR comment timestamp is after the push event
ESTIMATED TIME: 12
NOTES: [TESTER: confirm whether workflow is configured for both `opened` and `synchronize` PR event types in the workflow YAML]

===SCENARIO===
ID: UAT-F1-005
TITLE: Code review fails gracefully when PR diff is empty
TYPE: NEGATIVE
PERSONA: Data Engineer (full pipeline access)
PRE-CONDITIONS:
- GitHub Actions workflow enabled
- Ability to trigger workflow manually or with an empty-diff PR
TEST DATA: PR with no file changes (e.g. only whitespace change or a PR opened with identical branches) — or manual dispatch with empty diff input
STEPS:
1. Create branch `test/uat-f1-005` from main with no additional commits (identical to main)
2. Attempt to open a PR from `test/uat-f1-005` to `main`
3. If GitHub prevents empty PRs, trigger workflow via manual dispatch with blank diff parameter
4. Observe workflow run outcome
5. Check for error handling — confirm no unhandled exception crashes the runner
6. Check whether a graceful message is posted to PR or output repo
EXPECTED RESULT: Workflow either skips gracefully with a logged message or posts a comment indicating no diff was found; workflow run does not fail with an unhandled Python exception; no malformed JSON written to output repo
PASS CRITERIA: Workflow run status is `success` or `skipped` (NOT `failure` due to unhandled exception) AND no corrupted output file is written
ESTIMATED TIME: 10
NOTES: Current code does `raw[:30000]` slice on diff — an empty string should not crash `extract_json`. [TESTER: verify error handling for empty diff in tool1_code_review.py — not visible in provided code snippet]

===SCENARIO===
ID: UAT-F1-006
TITLE: Unauthorised user cannot trigger code review workflow via API without valid GH_TOKEN
TYPE: NEGATIVE
PERSONA: Business User (read-only, report access)
PRE-CONDITIONS:
- Business User account has read-only access to kylodeng/ai-delivery-source
- Business User does not have `write` or `admin` repo permissions
TEST DATA: Business User GitHub credentials; attempt to trigger workflow dispatch via GitHub API using Business User's personal access token
STEPS:
1. Log in to GitHub as Business User (read-only account)
2. Attempt to trigger the code review workflow via GitHub UI (Actions tab → Run workflow)
3. Observe whether the "Run workflow" button is visible and enabled
4. Separately, attempt via GitHub API: `POST /repos/kylodeng/ai-delivery-source/actions/workflows/{workflow_id}/dispatches` using Business User PAT
5. Record the HTTP response code
EXPECTED RESULT: Business User cannot see or use "Run workflow" button in UI; API call returns HTTP 403 Forbidden; workflow is not triggered
PASS CRITERIA: UI does not present run option OR API returns 403 AND no workflow run is initiated
ESTIMATED TIME: 8
NOTES: This tests GitHub's native RBAC, not application-level access control. [TESTER: confirm repo permission model for Business User persona]

===SCENARIO===
ID: UAT-F1-007
TITLE: Code review handles PR diff exceeding 30,000 character truncation limit
TYPE: BOUNDARY
PERSONA: Data Engineer (full pipeline access)
PRE-CONDITIONS:
- GitHub Actions workflow enabled
- Ability to create a PR with a very large diff
TEST DATA: PR containing a large auto-generated Python file totalling > 30,000 characters of diff (e.g. a file with 1,500+ lines added)
STEPS:
1. Create branch `test/uat-f1-007` from main
2. Add a Python file with > 1,500 lines of generated code (e.g. a large data dictionary or config)
3. Open a PR from `test/uat-f1-007` to `main`
4. Wait for `tool1_code_review` workflow to complete
5. Inspect workflow logs to confirm diff was truncated at 30,000 chars (`get_pr_diff` returns `[:30000]`)
6. Verify output JSON report is still generated successfully despite truncation
7. Check whether truncation is noted anywhere in the report or PR comment
EXPECTED RESULT: Workflow completes without error despite large diff; output JSON is valid; review is based on the truncated diff (first 30,000 chars); no timeout or memory error
PASS CRITERIA: Workflow run status is `success` AND valid JSON report exists in output repo AND PR comment is posted
ESTIMATED TIME: 15
NOTES: `get_pr_diff` in shared.py explicitly slices `[:30000]`. Claude `max_tokens` is 4096 — review quality may be limited for large diffs. Consider whether truncation warning should be added to output.

===SCENARIO===
ID: UAT-F2-001
TITLE: Code review triggered via weekly cron schedule runs against repo files
TYPE: POSITIVE
PERSONA: Data Engineer (full pipeline access)
PRE-CONDITIONS:
- Weekly cron trigger is configured in the workflow YAML
- `get_repo_files` is configured to fetch up to 20 files with relevant extensions
- Output repo ai-delivery-outputs is accessible
TEST DATA: Existing files in kylodeng/ai-delivery-source repo (Python files: shared.py, tool1_code_review.py, tool2_tech_docs.py)
STEPS:
1. [TESTER: manually trigger cron-equivalent via workflow_dispatch if cron schedule cannot be awaited]
2. Navigate to GitHub Actions and trigger the weekly code review workflow manually
3. Wait for workflow completion
4. Verify output report is written to ai-delivery-outputs
5. Verify notification email is sent to NOTIFY_EMAIL (kylo.deng@capco.com)
6. Confirm report covers files from the repository (not PR diff)
EXPECTED RESULT: Workflow completes; output report references existing repo files; email notification sent; report written to ai-delivery-outputs
PASS CRITERIA: Workflow run status is `success` AND output file created in ai-delivery-outputs AND email delivery confirmed (check inbox or SendGrid activity log)
ESTIMATED TIME: 15
NOTES: `get_repo_files` fetches max 20 files filtered by extension. [TESTER: verify which extensions are passed to get_repo_files in tool1_code_review.py — not fully visible in snippet]

===SCENARIO===
ID: UAT-F2-002
TITLE: Manual workflow dispatch triggers code review with custom parameters
TYPE: POSITIVE
PERSONA: Admin (full system access)
PRE-CONDITIONS:
- Admin has write access to kylodeng/ai-delivery-source
- Workflow supports `workflow_dispatch` trigger with input parameters
- All secrets configured
TEST DATA: Manual dispatch inputs: [TESTER: verify input parameters defined in workflow YAML — e.g. target branch, PR number]
STEPS:
1. Navigate to Actions tab in kylodeng/ai-delivery-source
2. Select the code review workflow
3. Click "Run workflow"
4. Fill in any available input fields [TESTER: document actual input fields]
5. Click "Run workflow" to confirm
6. Wait for completion
7. Verify output in ai-delivery-outputs and email notification
EXPECTED RESULT: Workflow triggers immediately; completes within 5 minutes; output report and email generated
PASS CRITERIA: Workflow run status is `success` AND output file timestamp matches dispatch time
ESTIMATED TIME: 12
NOTES: [TESTER: confirm workflow_dispatch inputs — not visible in provided code snippet]

===SCENARIO===
ID: UAT-F2-003
TITLE: Code review workflow fails gracefully when ANTHROPIC_API_KEY secret is missing or invalid
TYPE: NEGATIVE
PERSONA: Admin (full system access)
PRE-CONDITIONS:
- Admin has access to repository secrets settings
- A test/staging environment or fork is available to safely modify secrets
TEST DATA: Replace ANTHROPIC_API_KEY with value `INVALID_KEY_FOR_UAT_TEST` in a fork or test environment
STEPS:
1. In a fork of kylodeng/ai-delivery-source, navigate to Settings → Secrets and variables → Actions
2. Set `ANTHROPIC_API_KEY` to `INVALID_KEY_FOR_UAT_TEST`
3. Trigger the code review workflow via manual dispatch
4. Observe workflow logs
5. Confirm the error is caught and a meaningful error message is logged
6. Confirm no partial/corrupted output file is written to ai-delivery-outputs
7. Confirm a failure notification email is sent if email-on-failure is implemented
EXPECTED RESULT: Workflow fails with a clear error message referencing API authentication failure; no corrupted JSON written to output repo; runner does not hang indefinitely
PASS CRITERIA: Workflow run status is `failure` AND error message in logs references API key or authentication AND no malformed output file exists
ESTIMATED

---
_Auto-generated by AI Delivery Bot_
