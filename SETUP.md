# AI Delivery POC — Setup Guide

This guide takes you from zero to all 5 tools running in GitHub Actions in about 30 minutes.

---

## What you're setting up

| # | Tool | Trigger | Output |
|---|---|---|---|
| 1 | Code Review | PR open / weekly cron / manual | Review report + PR inline comments |
| 2 | Tech Documentation | Merge to main / weekly / manual | README, ARCHITECTURE, RUNBOOK |
| 3 | Business Documentation | Release tag / manual | Solution Overview + Gap Questionnaire |
| 4 | Auto Testing | PR open / weekly / manual | Generated test files + coverage report |
| 5 | UAT Facilitation | Release branch / manual | UAT test pack (generate) or defect report (analyse) |

All outputs go to `ai-delivery-outputs` repo. Every run is emailed to kylo.deng@capco.com and logged to the audit trail.

---

## Step 1 — Create the two GitHub repos

### Repo A: Source repo (the code to analyse)
```bash
# If you already have a repo, skip this.
# To use the sample files in this POC:
gh repo create ai-delivery-source --public --clone
cd ai-delivery-source
cp -r /path/to/poc/ai-delivery-source/* .
git add . && git commit -m "Initial POC setup" && git push
```

### Repo B: Output repo
```bash
gh repo create ai-delivery-outputs --public
# Clone it and push the seed files:
cd ai-delivery-outputs
cp -r /path/to/poc/ai-delivery-outputs/* .
git add . && git commit -m "Initial output repo setup" && git push
```

---

## Step 2 — Get your API keys

### Anthropic API key
1. Go to https://console.anthropic.com
2. Settings → API Keys → Create Key
3. Copy the key (starts with `sk-ant-...`)

### SendGrid API key
1. Go to https://app.sendgrid.com (free account is fine)
2. Settings → API Keys → Create API Key
3. Choose "Restricted Access" → enable "Mail Send"
4. Copy the key (starts with `SG.`)
5. **Verify your sender email:**
   - Settings → Sender Authentication → Single Sender Verification
   - Add `noreply@ai-delivery.capco.com` (or any email you control)
   - Verify it via the email SendGrid sends you

### GitHub fine-grained PAT (Personal Access Token)
The workflows need to write to the `ai-delivery-outputs` repo.

1. GitHub → Settings → Developer settings → Personal access tokens → Fine-grained tokens
2. Click "Generate new token"
3. Set:
   - **Resource owner:** your GitHub username
   - **Repository access:** Select repositories → add BOTH `ai-delivery-source` and `ai-delivery-outputs`
   - **Permissions:**
     - Contents: Read and Write
     - Issues: Read and Write  
     - Pull requests: Read and Write
     - Metadata: Read (auto-selected)
4. Generate and copy the token (starts with `github_pat_...`)

---

## Step 3 — Add secrets to the source repo

Go to: `github.com/{your-username}/ai-delivery-source` → Settings → Secrets and variables → Actions

Add these 3 secrets:

| Secret name | Value |
|---|---|
| `ANTHROPIC_API_KEY` | Your Anthropic key (`sk-ant-...`) |
| `SENDGRID_API_KEY` | Your SendGrid key (`SG.`) |
| `GH_TOKEN` | Your fine-grained PAT (`github_pat_...`) |

> ⚠️ The built-in `GH_TOKEN` in Actions only has access to the current repo.
> You MUST use your PAT as `GH_TOKEN` so the scripts can write to `ai-delivery-outputs`.
> Name it `GH_TOKEN` in secrets — the workflow env picks it up automatically.

---

## Step 4 — Test each tool

### Tool 1 — Code Review (immediate test)
1. Go to `ai-delivery-source` → Actions → "Tool 1 — Code Review"
2. Click "Run workflow" → mode: `repo` → Run
3. After ~60 seconds, check:
   - `ai-delivery-outputs/code-review/` for the report
   - Your email for the notification
   - `ai-delivery-outputs/audit/audit_log.md` for the log entry

### Tool 2 — Tech Documentation
1. Actions → "Tool 2 — Tech Documentation" → Run workflow
2. Outputs: `ai-delivery-outputs/tech-docs/{owner}-{repo}/`

### Tool 3 — Business Documentation
1. Actions → "Tool 3 — Business Documentation" → Run workflow
2. Inputs: Project name = "Data Ingestion Pipeline", version = "0.1.0"
3. Outputs: `ai-delivery-outputs/business-docs/`

### Tool 4 — Auto Testing
1. Actions → "Tool 4 — Auto Testing" → Run workflow → mode: `generate`
2. Outputs: `ai-delivery-outputs/auto-tests/{owner}-{repo}/`

### Tool 5 — UAT (generate mode)
1. Actions → "Tool 5 — UAT Facilitation" → Run workflow
2. Mode: `generate`, version: `0.1.0`
3. Outputs: `ai-delivery-outputs/uat/{owner}-{repo}/v0.1.0/`

### Tool 5 — UAT (analyse mode)
1. After testers fill in `UAT_RESULTS_SHEET.csv` in the output repo
2. Run Tool 5 with mode: `analyse`
3. Set results path: `uat/{owner}-{repo}/v0.1.0/UAT_RESULTS_SHEET.csv`

---

## Step 5 — Point at your real repos

The workflows use these env vars to know which repo to analyse:

```yaml
SOURCE_REPO_OWNER:  ${{ github.repository_owner }}   # auto-detected
SOURCE_REPO_NAME:   ${{ github.event.repository.name }}  # auto-detected
```

To run against a *different* repo than the one hosting the workflow:
1. Add the workflow files to that repo's `.github/workflows/`
2. Add the scripts to `.github/scripts/`
3. Add the 3 secrets to that repo
4. Run — it will analyse itself and write outputs to `ai-delivery-outputs`

---

## Scheduled triggers (auto-runs)

| Tool | Schedule | Day/Time |
|---|---|---|
| Code Review | `0 8 * * 1` | Mondays 08:00 UTC |
| Tech Docs | `0 6 * * 0` | Sundays 06:00 UTC |
| Auto Testing | `0 7 * * 3` | Wednesdays 07:00 UTC |

Code Review also triggers automatically on every PR opened or updated.
UAT and Business Docs only run on-demand or on release events.

---

## Troubleshooting

### "Resource not accessible by integration"
→ Your `GH_TOKEN` secret is the built-in one, not your PAT. Re-add it with the PAT value.

### Email not arriving
→ Check SendGrid activity feed. Verify your sender domain/email. Check spam folder.

### "ModuleNotFoundError: No module named 'anthropic'"
→ Ensure the workflow step `pip install anthropic requests` ran before the script step.

### Claude returning non-JSON
→ Increase `max_tokens` in `shared.py` → `call_claude()`. Some reviews of large files hit the limit.

### Output repo write failing
→ Confirm the PAT has "Contents: Read and Write" permission on `ai-delivery-outputs`.

---

## Cost estimates (approximate)

| Tool | Claude tokens per run | Approx. cost |
|---|---|---|
| Code Review (PR) | ~8,000 | ~$0.03 |
| Code Review (weekly scan) | ~25,000 | ~$0.10 |
| Tech Documentation | ~30,000 | ~$0.12 |
| Business Documentation | ~20,000 | ~$0.08 |
| Auto Testing (5 files) | ~40,000 | ~$0.16 |
| UAT Generate | ~20,000 | ~$0.08 |
| UAT Analyse | ~8,000 | ~$0.03 |

Based on Claude Sonnet pricing (~$3/M input, ~$15/M output tokens). All 5 tools running weekly ≈ $2-5/month.

---

## File structure reference

```
ai-delivery-source/
├── .github/
│   ├── workflows/
│   │   ├── tool1_code_review.yml
│   │   ├── tool2_tech_docs.yml
│   │   ├── tool3_business_docs.yml
│   │   ├── tool4_auto_testing.yml
│   │   └── tool5_uat.yml
│   └── scripts/
│       ├── shared.py              ← common utilities (GitHub, Claude, email, audit)
│       ├── tool1_code_review.py
│       ├── tool2_tech_docs.py
│       ├── tool3_business_docs.py
│       ├── tool4_auto_testing.py
│       └── tool5_uat.py
├── src/
│   └── data_pipeline.py          ← sample Python (has intentional flaws to review)
├── infra/
│   └── main.tf                   ← sample Terraform (has intentional IaC issues)
├── synthetic_data/
│   └── customers_sample.csv      ← synthetic test data
└── tests/                        ← empty — Tool 4 will generate these

ai-delivery-outputs/
├── audit/
│   ├── audit_log.json
│   └── audit_log.md
├── code-review/
├── tech-docs/
├── business-docs/
├── auto-tests/
└── uat/
```
