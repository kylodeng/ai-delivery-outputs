# UAT Test Pack — kylodeng/Insurance-Training-Bot-main v0.1.0
**Generated:** 2026-04-08 05:50 UTC  
**Instructions:** Work through each scenario in order. Log PASS, FAIL, or BLOCKED in the CSV sheet.
For failures, note the defect reference. When complete, upload the CSV and trigger the UAT analysis workflow.

---

# UAT Test Pack — Insurance Training Bot v0.1.0
**Project:** kylodeng/Insurance-Training-Bot-main
**Version:** 0.1.0
**Prepared by:** UAT Test Manager (AI-generated)
**Date:** 2025
**Status:** Draft — pending user story confirmation

---

## Preamble & Assumptions

> ⚠️ **[TESTER: No formal user stories were provided. All scenarios have been derived from code context, repository structure, and synthetic data samples. Validate each scenario against the actual Product Owner acceptance criteria before execution.]**

### Identified Features Under Test

| Feature ID | Feature Name | Source Evidence |
|---|---|---|
| F-01 | Document Ingestion & Annotation Pipeline | `data/Insurance-product-info/*.annot.json` files |
| F-02 | Claude AI Code Review Tool (Tool 1) | `.github/scripts/tool1_code_review.py` |
| F-03 | Technical Documentation Generation (Tool 2) | `.github/scripts/tool2_tech_docs.py` |
| F-04 | GitHub API Integration | `shared.py` — `get_repo_files`, `get_pr_diff` |
| F-05 | Email Notification (SendGrid) | `shared.py` — `send_email`, `email_html` |
| F-06 | Audit Logging | `shared.py` — `write_audit_entry` |
| F-07 | Output File Writing to Repository | `shared.py` — `write_output_file` |
| F-08 | JSON Parsing & Cleaning of Claude Responses | `shared.py` — `clean_json`; `tool1` — `extract_json` |

### Environment Prerequisites
- Test environment with GitHub Actions runner access
- Valid `ANTHROPIC_API_KEY`, `GH_TOKEN`, `SENDGRID_API_KEY` in env
- Output repo (`ai-delivery-outputs`) accessible under test org
- Synthetic insurance PDF annotation files present in `data/Insurance-product-info/`
- SendGrid sender identity verified for `kylo.deng@capco.com`

---

## FEATURE F-01: Document Ingestion & Annotation Pipeline

===SCENARIO===
ID: UAT-F01-001
TITLE: Successful ingestion and annotation of a valid insurance product brochure PDF
TYPE: POSITIVE
PERSONA: Data Engineer (full pipeline access)
PRE-CONDITIONS:
- Pipeline environment is running and all required env vars are set
- `data/Insurance-product-info/Generations-II/` directory exists in repo
- Valid `Generations-II_PB_EN.pdf` is present in the source directory
- Output repo `ai-delivery-outputs` is accessible and writable
TEST DATA: Source file — `Generations-II_PB_EN.pdf`; Expected output — `Generations-II_PB_EN.pdf.annot.json` with `product_name: "Generations II"`, `doc_type: "product_brochure"`, `linked_product: "Generations II"`
STEPS:
1. Navigate to the GitHub Actions pipeline for the annotation workflow
2. Trigger the pipeline manually (workflow_dispatch) targeting the `Generations-II_PB_EN.pdf` file
3. Wait for the workflow run to complete (observe logs)
4. Navigate to `data/Insurance-product-info/Generations-II/` in the output repo
5. Open `Generations-II_PB_EN.pdf.annot.json` and inspect the `doc` object
EXPECTED RESULT: The annotation JSON file is created with `product_name: "Generations II"`, `doc_type: "product_brochure"`, `linked_product: "Generations II"`, a non-empty `summary` field, and a `pages` array with at least one entry
PASS CRITERIA: PASS if `annot.json` file exists AND all five fields (`product_name`, `doc_type`, `linked_product`, `summary`, `pages`) are present and non-empty; FAIL if any field is missing or empty
ESTIMATED TIME: 10 minutes
NOTES: The cover/title page is expected to have `relevant: false` and a non-empty `skip_reason`. Verify that subsequent pages have correctly assessed `relevant` flags. [TESTER: confirm which workflow file triggers the annotation pipeline]

===SCENARIO===
ID: UAT-F01-002
TITLE: Supplementary document (hospital list) annotated with correct doc_type and linked_product
TYPE: POSITIVE
PERSONA: Data Engineer (full pipeline access)
PRE-CONDITIONS:
- Pipeline environment is running and all required env vars are set
- `List of designated hospitals in mainland China.pdf` exists in `data/Insurance-product-info/`
- Output repo is accessible
TEST DATA: Source file — `List of designated hospitals in mainland China.pdf`; Expected `doc_type: "supplementary"`, `linked_product: "health_products"`, summary containing "January 2024"
STEPS:
1. Trigger the annotation pipeline for `List of designated hospitals in mainland China.pdf`
2. Wait for the workflow to complete
3. Open `List of designated hospitals in mainland China.pdf.annot.json`
4. Inspect `doc.doc_type`, `doc.linked_product`, and `doc.summary`
5. Inspect the `pages` array and verify at least one page has `relevant: true`
EXPECTED RESULT: `doc_type` equals `"supplementary"`, `linked_product` equals `"health_products"`, `summary` references the January 2024 update date, and at least one page entry has `relevant: true` with an empty `skip_reason`
PASS CRITERIA: PASS if `doc_type == "supplementary"` AND `linked_product == "health_products"` AND `summary` is non-empty; FAIL otherwise
ESTIMATED TIME: 8 minutes
NOTES: Verify that the pipeline correctly distinguishes between `product_brochure` and `supplementary` doc types. [TESTER: confirm classification logic source — is this Claude-determined or rule-based?]

===SCENARIO===
ID: UAT-F01-003
TITLE: Annotation pipeline rejects a non-PDF / unsupported file type
TYPE: NEGATIVE
PERSONA: Data Analyst (standard upload permissions, no admin)
PRE-CONDITIONS:
- Pipeline is running
- A non-PDF file (e.g., `.docx` or `.xlsx`) is placed in `data/Insurance-product-info/`
TEST DATA: File: `test_invalid_upload.docx` (empty or 1KB synthetic Word document); User: Data Analyst account
STEPS:
1. As Data Analyst, attempt to trigger the annotation pipeline with `test_invalid_upload.docx` as the target file
2. Observe workflow logs or UI error response
3. Check that no `.annot.json` file is created for the invalid file
EXPECTED RESULT: The pipeline returns an error indicating unsupported file type; no `.annot.json` output file is created; an appropriate error is logged
PASS CRITERIA: PASS if no `.annot.json` is created AND an error/rejection message is recorded in logs; FAIL if a partial or malformed annotation file is created
ESTIMATED TIME: 5 minutes
NOTES: [TESTER: verify whether the pipeline enforces file type validation before calling Claude, or whether Claude's response handles this. Confirm error messaging format.]

===SCENARIO===
ID: UAT-F01-004
TITLE: Unauthorised user (Business User / read-only) cannot trigger the annotation pipeline
TYPE: NEGATIVE
PERSONA: Business User (read-only, report access)
PRE-CONDITIONS:
- Business User account exists with read-only repository permissions
- Annotation pipeline workflow exists in `.github/workflows/`
TEST DATA: Business User GitHub account — `test-business-user@capco.com`; Target repo: `kylodeng/Insurance-Training-Bot-main`
STEPS:
1. Log in to GitHub as the Business User account
2. Navigate to the Actions tab of `kylodeng/Insurance-Training-Bot-main`
3. Attempt to manually trigger (workflow_dispatch) the annotation pipeline
4. Observe whether the trigger button is available or blocked
5. Attempt to trigger via GitHub API: `POST /repos/kylodeng/Insurance-Training-Bot-main/actions/workflows/{workflow_id}/dispatches` with the Business User token
EXPECTED RESULT: Step 4 — the workflow_dispatch trigger button is not visible or is disabled for read-only users. Step 5 — API returns `HTTP 403 Forbidden`
PASS CRITERIA: PASS if Business User cannot trigger the pipeline by both UI and API methods; FAIL if pipeline is triggered successfully
ESTIMATED TIME: 5 minutes
NOTES: GitHub's native permission model should enforce this. [TESTER: confirm the repo visibility settings and branch protection rules for the Actions tab]

===SCENARIO===
ID: UAT-F01-005
TITLE: Annotation pipeline handles an empty PDF (zero content pages) without crashing
TYPE: BOUNDARY
PERSONA: Data Engineer (full pipeline access)
PRE-CONDITIONS:
- Pipeline is running
- An empty PDF file (valid PDF wrapper, 0 content pages) has been prepared
TEST DATA: File: `test_empty.pdf` (valid PDF metadata, zero content pages, file size ~1KB); placed in `data/Insurance-product-info/`
STEPS:
1. Trigger the annotation pipeline targeting `test_empty.pdf`
2. Monitor workflow run logs in GitHub Actions
3. Check whether the workflow completes, errors gracefully, or crashes
4. If a `.annot.json` is created, inspect the `pages` array
EXPECTED RESULT: The pipeline either produces an annotation JSON with an empty `pages` array and a populated `doc` block noting no content, OR exits gracefully with a logged error — it must NOT produce an unhandled exception / crash the runner
PASS CRITERIA: PASS if workflow run completes (success or handled failure) without unhandled exception; FAIL if runner crashes or workflow status is `failed` due to unhandled exception
ESTIMATED TIME: 8 minutes
NOTES: [TESTER: verify Claude's behaviour when given an empty document — does it hallucinate content or return an appropriate null/empty response?]

---

## FEATURE F-02: Claude AI Code Review Tool (Tool 1)

===SCENARIO===
ID: UAT-F02-001
TITLE: Code review triggered on PR open returns valid JSON report with all required fields
TYPE: POSITIVE
PERSONA: Data Engineer (full pipeline access)
PRE-CONDITIONS:
- A new Pull Request has been opened in the target repository
- `ANTHROPIC_API_KEY`, `GH_TOKEN`, `SENDGRID_API_KEY` are all set in GitHub Actions secrets
- `tool1_code_review.py` workflow is active and configured to trigger on PR open
TEST DATA: PR #TEST-001 — synthetic PR containing a Python file with one hardcoded password: `password = "Sunlife2024!"` and one bare `except:` clause; PR author: `test-data-engineer@capco.com`
STEPS:
1. Open a new Pull Request containing the synthetic test file described in TEST DATA
2. Wait for the `tool1_code_review` workflow to trigger automatically
3. Observe the workflow run in the Actions tab
4. Navigate to the PR and inspect automated comments posted by the bot
5. Navigate to the output repo (`ai-delivery-outputs`) and locate the generated report file
6. Open the report file and validate the JSON structure
EXPECTED RESULT: The workflow completes successfully; a PR comment is posted containing review findings; the output repo contains a JSON file with fields: `summary`, `score` (0–100 integer), `merge_recommendation` (one of: APPROVE/REQUEST_CHANGES/BLOCK), `findings` array (each with `severity`, `category`, `file`, `line`, `issue`, `recommendation`), `positive_observations` array, `iac_findings` array
PASS CRITERIA: PASS if all seven top-level JSON fields are present, `score` is an integer between 0 and 100, `merge_recommendation` is one of the three permitted values, and at least one finding flags the hardcoded password as CRITICAL or HIGH severity; FAIL otherwise
ESTIMATED TIME: 12 minutes
NOTES: Hardcoded password `"Sunlife2024!"` must be detected. Bare `except:` clause should appear as a finding. [TESTER: confirm the exact output file naming convention in the output repo]

===SCENARIO===
ID: UAT-F02-002
TITLE: Code review on a PR with no code changes (empty diff) handles gracefully
TYPE: BOUNDARY
PERSONA: Data Engineer (full pipeline access)
PRE-CONDITIONS:
- `tool1_code_review.py` workflow is active
- A PR is created with only a whitespace or comment-only change (effectively empty meaningful diff)
TEST DATA: PR #TEST-002 — single file change: `# updated comment only` added to `README.md`; diff size: <50 characters
STEPS:
1. Open a Pull Request containing only a comment/whitespace change
2. Wait for the code review workflow to trigger
3. Observe workflow run logs
4. Check the PR for any automated comment
5. Check output repo for a generated report
EXPECTED RESULT: The workflow completes without error; the report is generated with a high score (≥80) and `merge_recommendation: "APPROVE"`; findings array may be empty or contain only LOW severity items; no crash occurs due to empty diff
PASS CRITERIA: PASS if workflow completes successfully and output JSON is valid; FAIL if workflow errors/crashes or produces malformed JSON
ESTIMATED TIME: 8 minutes
NOTES: `get_pr_diff` truncates at 30,000 characters — this boundary test is at the other extreme (near-zero). [TESTER: verify behaviour when `get_pr_diff` returns an empty string]

===SCENARIO===
ID: UAT-F02-003
TITLE: Code review correctly flags CRITICAL severity for hardcoded API key in diff
TYPE: POSITIVE
PERSONA: Data Engineer (full pipeline access)
PRE-CONDITIONS:
- `tool1_code_review.py` workflow is active
- PR contains a file with a hardcoded API key pattern
TEST DATA: PR #TEST-003 — file `config.py` containing `ANTHROPIC_API_KEY = "sk-ant-test1234567890abcdef"`; PR diff is under 30,000 characters
STEPS:
1. Open a Pull Request containing `config.py` with the hardcoded key
2. Allow the code review workflow to trigger and complete
3. Open the generated JSON report in the output repo
4. Inspect the `findings` array for the hardcoded secret entry
5. Verify the PR comment mentions the security issue
EXPECTED RESULT: At least one finding in the `findings` array has `severity: "CRITICAL"`, `category: "security"`, references `config.py`, and describes a hardcoded API key/secret issue
PASS CRITERIA: PASS if a CRITICAL severity security finding exists referencing the hardcoded key file; FAIL if the finding is absent or severity is below HIGH
ESTIMATED TIME: 10 minutes
NOTES: The SYSTEM prompt explicitly prioritises "hardcoded secrets" as highest priority. Regression risk if Claude model version changes — note `MODEL = "claude-sonnet-4-6"`.

===SCENARIO===
ID: UAT-F02-004
TITLE: Code review workflow blocked for user without repository write permissions
TYPE: NEGATIVE
PERSONA: Business User (read-only, report access)
PRE-CONDITIONS:
- Business User account has read-only access to the repository
- `tool1_code_review.py` is configured to post PR comments using `GH_TOKEN`
TEST DATA: Business User account: `test-business-user@capco.com`; attempting to trigger workflow_dispatch for `tool1_code_review` workflow
STEPS:
1. Authenticate as Business User via GitHub API
2. Attempt `POST /repos/kylodeng/Insurance-Training-Bot-main/actions/workflows/tool1_code_review/dispatches`
3. Observe the HTTP response code and body
4. Verify no report is generated in the output repo
EXPECTED RESULT: GitHub API returns `HTTP 403 Forbidden`; no workflow run is created; no report file appears in `ai-delivery-outputs`
PASS CRITERIA: PASS if API returns 403 and no report is generated; FAIL if the workflow runs
ESTIMATED TIME: 5 minutes
NOTES: [TESTER: confirm GH_TOKEN permissions scope — if it is a repo-scoped PAT, verify it cannot be used by external callers]

===SCENARIO===
ID: UAT-F02-005
TITLE: Code review gracefully handles Claude API returning malformed / non-JSON response
TYPE: NEGATIVE
PERSONA: Data Engineer (full pipeline access)
PRE-CONDITIONS:
- `tool1_code_review.py` workflow is active
- A mock or stub is configured to return a plain-text (non-JSON) response from the Claude API call [TESTER: verify test environment supports API m

---
_Auto-generated by AI Delivery Bot_
