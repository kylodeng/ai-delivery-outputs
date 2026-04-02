# Solution Overview: Data Ingestion Pipeline
**Version:** 0.1.0 | **Date:** 2026-04-02 | **Status:** Draft

## Executive summary
This solution automates the ingestion and validation of customer data files, replacing what was likely a manual or ad-hoc process for moving data from a raw intake area into a clean, structured format ready for downstream use. It is primarily relevant to data engineering and operations teams at Capco. The business value is faster, more consistent data processing with a traceable record of what was accepted or rejected.

## Business context
**Problem statement:** Customer data arriving as CSV files needs to be validated, cleaned, and converted into an efficient storage format before it can be used by downstream systems or analysts. Without automation, this is slow, error-prone, and difficult to scale.

**Affected users / teams:** Data engineering teams, data operations, and any downstream consumers of customer data (e.g. analytics, CRM, reporting teams).

**Current pain points:** [TODO: what was the manual/legacy process — was data being processed by hand, via scripts run locally, or through a different pipeline tool?]

## What this solution does
When a new customer data file (in CSV format) is dropped into a designated intake area in cloud storage, the system automatically picks it up, checks each record against a set of rules (for example: required fields must be present, email addresses must be valid, ages must be within an acceptable range), and saves the clean records into a separate storage area in a more efficient format (Parquet). Any records that fail validation are flagged and set aside. A count of how many records were accepted and how many were rejected is returned for each file processed.

## What it does NOT do (out of scope)
- **Does not send notifications or alerts** if a file fails processing or if a high proportion of records are rejected — there is no alerting mechanism in place.
- **Does not handle files other than CSVs** — other formats (JSON, XML, Excel, etc.) are not supported.
- **Does not paginate when listing files** — if there are more files in the intake area than a single API response returns, the extras will be silently missed.
- **Does not store or report on rejected records** — failed rows are counted but not written anywhere for review or remediation.
- **Does not support multiple environments** with separate security controls — the current setup defaults to a single `dev` environment with no production-grade hardening.

## Data handled
| Data type | Sensitivity | Retention | Storage location |
|---|---|---|---|
| Customer records (ID, email, age, country code) | High — contains personal data (PII) | [TODO: what is the required retention period?] | AWS S3 (`capco-data-landing-{env}` and `capco-data-processed-{env}`) |
| Raw CSV files (input) | High — PII | [TODO] | AWS S3 landing bucket (`raw/` prefix) |
| Processed Parquet files (output) | High — PII | [TODO] | AWS S3 processed bucket (`processed/` prefix) |
| Database credentials | Critical — secret | Not persisted (but currently hardcoded — see Risks) | AWS Lambda environment variable (insecure) |
| AWS access credentials | Critical — secret | Not persisted (but currently hardcoded — see Risks) | Hardcoded in source code (insecure) |

## Stakeholders
| Role | Name | Responsibility |
|---|---|---|
| Solution owner | [TODO] | Accountable for delivery and budget |
| Business sponsor | [TODO] | Strategic direction |
| Tech lead | [TODO] | Technical decisions |
| Key users | [TODO] | Day-to-day usage and data operations |

## Risks and dependencies
- **Credentials hardcoded in source code:** AWS access keys and a database password are currently written directly into the code and infrastructure configuration. This is a critical security risk — anyone with access to the repository can see these credentials.
- **No encryption on storage:** The intake (landing) storage bucket has no encryption configured. Customer PII stored here is unprotected at rest.
- **Overly broad access permissions:** The automated process has been granted unrestricted access to all storage across the account, far beyond what it needs. If compromised, it could read or delete unrelated data.
- **No public access block on landing bucket:** The landing S3 bucket lacks a public access block, meaning it could potentially be made public by misconfiguration.
- **No disaster recovery or backup:** There is no evidence of cross-region replication, versioning, or backup configuration for either storage bucket.
- **Single AWS region (`us-east-1`):** If that region experiences an outage, the pipeline is unavailable with no failover.
- **Silent data loss on large file sets:** The file listing step does not paginate, so only the first page of files (up to 1,000 by default) will ever be processed if the intake area is large.
- **No monitoring or alerting:** There is no mechanism to alert the team if the pipeline stops working, processes no files, or rejects an unusually high number of records.
- **No tests currently exist:** The test directory is empty. There is no automated verification that the pipeline behaves correctly.
- **Infrastructure resource tagging is missing:** Resources deployed to AWS have no tags, making cost attribution and governance difficult.

## Success metrics
[TODO: Confirm with the business, but candidate metrics include:]
- **Processing success rate:** Percentage of incoming records that pass validation and are written to the processed bucket — target to be agreed (e.g. >95%).
- **Pipeline reliability:** Percentage of files triggered for processing that complete without error — target to be agreed (e.g. >99%).
- **Processing latency:** Time from file arrival in the landing bucket to availability in the processed bucket — target to be agreed (e.g. under 5 minutes).

## Go-live and milestones
[TODO: Target date and key milestones to be confirmed with the project team.]

---

## Gap Questionnaire
_These are the only items Claude could not determine from the code and IaC.
Please fill these in before the document is finalised._

1. What was the previous process for ingesting and validating customer data files — was this done manually, by a different tool, or not done at all?
2. Who are the named individuals responsible for this solution (solution owner, business sponsor, and tech lead)?
3. What are the data retention requirements for customer PII stored in S3, and has a Data Protection Impact Assessment (DPIA) been completed?
4. What downstream systems or teams consume the processed data from the S3 output bucket, and are there any timing dependencies (e.g. a report that runs at 6am)?
5. What is the expected volume of files and records — both typical daily volumes and the maximum the pipeline must handle?
6. Is there a requirement for a production environment separate from `dev`, and if so, what is the approval process for promoting changes?
7. What is the target go-live date, and are there any regulatory, contractual, or business deadlines driving it?
8. How should rejected records be handled — should they be stored for manual review, trigger an alert, or be reported to a specific team?
9. Has the security team been made aware of the hardcoded credentials and unencrypted storage, and is there an agreed remediation timeline before go-live?

---
_Draft auto-generated by AI Delivery Bot · 2026-04-02 12:44 UTC · Source: kylodeng/ai-delivery-source v0.1.0_
