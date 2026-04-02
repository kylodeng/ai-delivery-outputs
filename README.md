# ai-delivery-outputs

This repository receives all outputs from the AI Delivery Bot workflows running in your source repos.

## Structure

```
ai-delivery-outputs/
├── audit/
│   ├── audit_log.json        ← machine-readable full audit trail
│   └── audit_log.md          ← human-readable audit table
├── code-review/
│   └── {owner}-{repo}-{ref}.md
├── tech-docs/
│   └── {owner}-{repo}/
│       ├── INDEX.md
│       ├── README.md
│       ├── ARCHITECTURE.md
│       └── RUNBOOK.md
├── business-docs/
│   └── {owner}-{repo}/
│       ├── solution-overview-v{version}.md
│       └── gap-questionnaire-v{version}.md
├── auto-tests/
│   └── {owner}-{repo}/
│       ├── python/test_*.py
│       ├── js/*.test.ts
│       └── TEST_REPORT.md
└── uat/
    └── {owner}-{repo}/
        └── v{version}/
            ├── UAT_TEST_PACK.md
            ├── UAT_RESULTS_SHEET.csv   ← testers fill this in
            └── UAT_DEFECT_REPORT.md
```

## Audit log

The audit log tracks every run of every tool. See [audit/audit_log.md](audit/audit_log.md).

---
_Managed by AI Delivery Bot_
