# Gap Questionnaire — Data Ingestion Pipeline v0.1.0

Please answer these questions to complete the Solution Overview Document.
Estimated time: 10-15 minutes.

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
_Generated 2026-04-02 12:44 UTC · [View full draft document](https://github.com/kylodeng/ai-delivery-outputs)_
