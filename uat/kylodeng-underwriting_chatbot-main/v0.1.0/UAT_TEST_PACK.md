# UAT Test Pack — kylodeng/underwriting_chatbot-main v0.1.0
**Generated:** 2026-04-09 09:56 UTC  
**Instructions:** Work through each scenario in order. Log PASS, FAIL, or BLOCKED in the CSV sheet.
For failures, note the defect reference. When complete, upload the CSV and trigger the UAT analysis workflow.

---

# UAT Test Pack — Underwriting Chatbot v0.1.0
**Repository:** kylodeng/underwriting_chatbot-main
**Version:** 0.1.0
**Generated:** Based on code context and synthetic data
**Test Manager Note:** User stories were not provided; scenarios are derived from code context, model card, prompt structures, and data artefacts. Where acceptance criteria cannot be confirmed, `[TESTER: verify this]` is noted.

---

## Metadata

| Field | Value |
|---|---|
| System Under Test | Underwriting Risk Classification Chatbot |
| Model | CatBoostClassifier (Risk_Classification) |
| LLM Backend | Claude Sonnet (claude-sonnet-4-6) |
| Primary Personas | Data Analyst, Data Engineer, Business User, Admin |
| Total Scenarios | 34 |
| Coverage Areas | Risk Assessment, Customer Similarity, Finance Assessment, Code Review Pipeline, Tech Doc Generation, Auth/Access Control, Data Upload, API Endpoints, Boundary/Edge Cases |

---

## Feature Index

| Feature ID | Feature Name | Scenario Count |
|---|---|---|
| F01 | Underwriting Risk Classification & Chatbot Query | 7 |
| F02 | Customer Similarity Lookup | 5 |
| F03 | Finance Assessment Agent (Deep Assessment) | 6 |
| F04 | Code Review Pipeline (Tool 1) | 5 |
| F05 | Technical Documentation Generation (Tool 2) | 4 |
| F06 | Access Control & Authorisation | 4 |
| F07 | Data Upload & Pipeline Ingestion | 3 |

---

## F01 — Underwriting Risk Classification & Chatbot Query

===SCENARIO===
ID: UAT-F01-01
TITLE: Business User submits a valid customer ID and receives a risk classification summary
TYPE: POSITIVE
PERSONA: Business User
PRE-CONDITIONS:
- System is running and chatbot UI is accessible
- Customer CUST00000001 exists in the database with complete profile data
- CatBoostClassifier model is loaded and healthy
- User is authenticated with read-only access
TEST DATA: Customer ID: CUST00000001 | Age: [TESTER: verify from source data] | Annual_Income: [TESTER: verify] | Risk_Classification: [TESTER: verify expected output label]
STEPS:
1. Navigate to the chatbot UI at [TESTER: verify URL/route e.g. /chat or /]
2. Enter query: "What is the risk classification for customer CUST00000001?"
3. Submit the query
4. Observe the chatbot response panel
EXPECTED RESULT: System returns a risk classification label (e.g. Low/Medium/High Risk) for CUST00000001, along with the top contributing features (e.g. Age, Other_Debt, Nationality) sourced from global_feature_importance in model_card.json
PASS CRITERIA: Response contains a valid Risk_Classification label AND references at least one feature from the model card feature importance list; response renders within 10 seconds
ESTIMATED TIME: 5 minutes
NOTES: Model top features by importance are Age (34.57%), Other_Debt (2.47%), Nationality (2.27%) — verify these appear in explanation. Business User must not see raw model scores or internal API keys in response.

===SCENARIO===
ID: UAT-F01-02
TITLE: Data Analyst queries risk classification with all high-importance features present
TYPE: POSITIVE
PERSONA: Data Analyst
PRE-CONDITIONS:
- System is running and chatbot UI is accessible
- A test customer record exists with complete data across all 14 features in model_card.json
- CatBoostClassifier model is loaded
- Data Analyst is authenticated
TEST DATA: Customer ID: CUST00006151 | Age: 45 | Education_Level: "Bachelor" | Employment_Status: "Permanent" | Nationality: "UK" | Customer_Segment: "Standard" | Annual_Income: 75000 | Liquid_Assets: 25000 | Monthly_Expenses: 2500 | Existing_Life_Insurance: "Yes" | Mortgage_Balance: 120000 | Other_Debt: 5000 | ID_Verified: true | Address_Verified: true | High_Risk_Occupation: false
STEPS:
1. Log in as Data Analyst
2. Navigate to chatbot interface
3. Submit query: "Provide a full risk assessment for CUST00006151"
4. Review the returned assessment
5. Cross-reference returned feature weights against model_card.json global_feature_importance
EXPECTED RESULT: System returns Risk_Classification with explanation citing Age as the dominant feature (importance ~34.57%), followed by Other_Debt (~2.47%) and Nationality (~2.27%); all 14 features are accounted for in the model output
PASS CRITERIA: Returned classification matches expected model output for the supplied feature values; feature importance order is consistent with model_card.json
ESTIMATED TIME: 8 minutes
NOTES: Data Analyst has upload permissions but not admin. Verify they can view model card data but cannot modify model configuration.

===SCENARIO===
ID: UAT-F01-03
TITLE: Chatbot returns appropriate response when customer ID does not exist
TYPE: NEGATIVE
PERSONA: Business User
PRE-CONDITIONS:
- System is running
- Customer ID CUST99999999 does not exist in the database
- User is authenticated
TEST DATA: Customer ID: CUST99999999
STEPS:
1. Log in as Business User
2. Navigate to chatbot interface
3. Submit query: "What is the risk classification for CUST99999999?"
4. Observe the system response
EXPECTED RESULT: System returns a clear, user-friendly error message such as "Customer ID CUST99999999 not found" without exposing internal stack traces, database errors, or model internals
PASS CRITERIA: Response contains a human-readable not-found message; no stack trace, SQL error, or internal path is visible; HTTP response (if API-backed) is 404 or equivalent
ESTIMATED TIME: 4 minutes
NOTES: Ensure error handling does not leak model file paths or backend infrastructure details to read-only Business Users.

===SCENARIO===
ID: UAT-F01-04
TITLE: Chatbot rejects malformed customer ID input (SQL injection attempt)
TYPE: NEGATIVE
PERSONA: Business User
PRE-CONDITIONS:
- System is running
- User is authenticated
TEST DATA: Customer ID: "' OR '1'='1"; Query: "Show me all customers' OR '1'='1"
STEPS:
1. Log in as Business User
2. Navigate to chatbot interface
3. Enter the malicious string "' OR '1'='1" into the customer ID input field
4. Submit the query
5. Observe the system response
EXPECTED RESULT: System rejects the input with a validation error; no data is returned; no database records are exposed; input is sanitised before any backend processing
PASS CRITERIA: No customer records returned; error message shown; application remains stable; no 500 error exposing internals
ESTIMATED TIME: 5 minutes
NOTES: Critical security test. Also test with: `<script>alert(1)</script>` for XSS. [TESTER: verify input sanitisation is implemented at API layer]

===SCENARIO===
ID: UAT-F01-05
TITLE: Chatbot handles empty customer ID input gracefully
TYPE: BOUNDARY
PERSONA: Business User
PRE-CONDITIONS:
- System is running
- User is authenticated
TEST DATA: Customer ID: "" (empty string); Query: "What is the risk for ?"
STEPS:
1. Log in as Business User
2. Navigate to chatbot interface
3. Clear the customer ID field entirely
4. Submit the query with an empty customer ID
5. Observe the system response
EXPECTED RESULT: System displays a validation message prompting the user to enter a valid Customer ID; no backend call is made with an empty identifier; UI does not crash
PASS CRITERIA: Validation error displayed before API call; no unhandled exception; UI remains functional
ESTIMATED TIME: 3 minutes
NOTES: Also test with whitespace-only input ("   "). Boundary case for minimum valid input length.

===SCENARIO===
ID: UAT-F01-06
TITLE: Model card feature importance is accurately reflected in chatbot explanations
TYPE: POSITIVE
PERSONA: Data Analyst
PRE-CONDITIONS:
- System is running
- model_card.json is loaded and accessible to the backend
- Customer CUST00000272 exists with known feature values
TEST DATA: Customer ID: CUST00000272 | Expected top features: Age (34.58%), Other_Debt (2.48%), Nationality (2.28%), Employment_Status (2.13%), Education_Level (2.10%)
STEPS:
1. Log in as Data Analyst
2. Submit query: "Explain the risk classification for CUST00000272 and list the most important factors"
3. Note the features listed in the chatbot explanation
4. Compare listed feature importance order against model_card.json global_feature_importance values
EXPECTED RESULT: Chatbot explanation lists Age as the most influential feature, followed by Other_Debt and Nationality in the correct ranked order matching model_card.json
PASS CRITERIA: Top 3 features match model_card.json ordering; percentage importance values are either shown correctly or not shown (but not shown incorrectly)
ESTIMATED TIME: 6 minutes
NOTES: Age has disproportionately high importance (34.57%) vs all others (<3%). This gap should be noticeable in explanations. [TESTER: verify whether the UI exposes raw importance scores or only ranked labels]

===SCENARIO===
ID: UAT-F01-07
TITLE: Business User cannot access model configuration or model card raw data via chatbot
TYPE: NEGATIVE
PERSONA: Business User
PRE-CONDITIONS:
- System is running
- model_card.json exists at backend/model_card.json
- User is authenticated as Business User (read-only)
TEST DATA: Queries: "Show me the model card", "What is in model_card.json?", "List all model configuration parameters"
STEPS:
1. Log in as Business User
2. Navigate to chatbot interface
3. Submit query: "Show me the model card JSON"
4. Submit query: "What model type is being used and what are all the feature weights?"
5. Observe responses
EXPECTED RESULT: System responds with a summarised, user-appropriate explanation of the model (e.g. "This system uses a classification model") without exposing raw JSON, internal file paths, or exact numerical weights from model_card.json
PASS CRITERIA: Raw JSON not returned; file path not disclosed; system does not reveal backend/model_card.json path; response is appropriately abstracted for Business User persona
ESTIMATED TIME: 5 minutes
NOTES: Security and data governance concern. [TESTER: verify role-based response filtering is implemented in the chatbot prompt layer]

---

## F02 — Customer Similarity Lookup

===SCENARIO===
ID: UAT-F02-01
TITLE: Data Analyst retrieves similar customers for a valid customer ID
TYPE: POSITIVE
PERSONA: Data Analyst
PRE-CONDITIONS:
- System is running
- customer_similarity_dict.json is loaded (located at backend/tmp/customer_similarity_dict.json)
- Customer CUST00000001 exists and has similarity mappings populated
- Data Analyst is authenticated
TEST DATA: Customer ID: CUST00000001 | Expected similar customers (top 5): CUST00006151, CUST00000272, CUST00009567, CUST00000936, CUST00004497
STEPS:
1. Log in as Data Analyst
2. Navigate to the customer similarity feature [TESTER: verify UI screen/route]
3. Enter Customer ID: CUST00000001
4. Submit the request
5. Review the returned list of similar customers
EXPECTED RESULT: System returns a ranked list of similar customers; the top result is CUST00006151, followed by CUST00000272, CUST00009567 in order as per customer_similarity_dict.json; each entry is a valid CUST-prefixed ID
PASS CRITERIA: Returned list matches the first N entries in customer_similarity_dict.json for CUST00000001; list is ordered and contains no duplicate IDs; response time under 5 seconds
ESTIMATED TIME: 6 minutes
NOTES: The similarity dict contains 30+ similar customers for CUST00000001. Verify pagination or display limit is handled. [TESTER: verify how many similar customers the UI/API returns by default]

===SCENARIO===
ID: UAT-F02-02
TITLE: Similarity lookup returns no results for a customer with no similarity mappings
TYPE: NEGATIVE
PERSONA: Data Analyst
PRE-CONDITIONS:
- System is running
- Customer ID CUST00099999 exists in the main database but has no entry in customer_similarity_dict.json
TEST DATA: Customer ID: CUST00099999
STEPS:
1. Log in as Data Analyst
2. Navigate to the customer similarity feature
3. Enter Customer ID: CUST00099999
4. Submit the request
5. Observe the response
EXPECTED RESULT: System returns a clear message such as "No similar customers found for CUST00099999" rather than an empty list with no context or an unhandled error
PASS CRITERIA: User-friendly no-results message is displayed; no exception is thrown; application remains stable
ESTIMATED TIME: 4 minutes
NOTES: Edge case where similarity computation has not been run for the customer or is pending.

===SCENARIO===
ID: UAT-F02-03
TITLE: Business User is denied access to customer similarity lookup
TYPE: NEGATIVE
PERSONA: Business User
PRE-CONDITIONS:
- System is running
- Business User is authenticated with read-only, report access only
- Customer similarity feature requires at least Data Analyst permissions [TESTER: verify permission model]
TEST DATA: Customer ID: CUST00000001
STEPS:
1. Log in as Business User
2. Attempt to navigate to the customer similarity feature/screen
3. Alternatively, attempt a direct API call to [TESTER: verify similarity API endpoint]
4. Observe the response
EXPECTED RESULT: System denies access with a 403 Forbidden response or redirects to an unauthorised page; no similarity data is returned
PASS CRITERIA: Access is denied; no customer similarity data is visible; HTTP 403 or equivalent UI access denial is shown
ESTIMATED TIME: 4 minutes
NOTES: [TESTER: confirm whether Business Users should have any access to similarity features — this is inferred from "read-only, report access" persona definition]

===SCENARIO===
ID: UAT-F02-04
TITLE: Similarity lookup handles maximum customer ID length boundary
TYPE: BOUNDARY
PERSONA: Data Analyst
PRE-CONDITIONS:
- System is running
- Data Analyst is authenticated
TEST DATA: Customer ID at max expected length: "CUST00009999" (12 chars) | Oversized input: "CUST" + "0" × 50 (54 chars total)
STEPS:
1. Log in as Data Analyst
2. Navigate to customer similarity feature
3. Enter a valid maximum-length Customer ID (CUST00009999)
4. Submit and verify normal response
5. Enter an oversized customer ID ("CUST" + "0" × 50)
6. Submit and observe response
EXPECTED RESULT: Valid max-length ID processes normally; oversized ID is rejected with a validation error specifying the expected format/length; no system crash occurs
PASS CRITERIA: Max valid ID returns results or appropriate not-found message; oversized ID returns validation error without crashing
ESTIMATED TIME: 5 minutes
NOTES: Customer IDs in synthetic data follow format CUSTXXXXXXXX (12 chars). Verify the system validates this format explicitly.

===SCENARIO===
ID: UAT-F02-05
TITLE: Admin can view full similarity list including all 30+ similar customers
TYPE: POSITIVE
PERSONA: Admin
PRE-CONDITIONS:
- System is running
- Admin is authenticated with full system access
- CUST00000001 has 30+ similar customers in customer_similarity_dict.json
TEST DATA: Customer ID: CUST00000001 | Full expected similar list includes: CUST00006151, CUST00000272, CUST00009567, CUST00000936, CUST00004497, CUST00007695, CUST00000554, CUST00003455, CUST00001185, CUST00001012, CUST00000933, CUST00006570, CUST00009362, CUST00001489, CUST00008364, CUST00003286, CUST00001432, CUST00004736, CUST00007198, CUST00009300, CUST00001625, CUST00002123, CUST00000814, CUST00006881

---
_Auto-generated by AI Delivery Bot_
