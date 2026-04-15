"""
Test suite for src/data_pipeline.py

WHAT IS TESTED:
- validate_customer_record: happy path, missing fields, invalid email, age boundary values
- process_csv: successful processing, partial failures, all-fail scenarios, empty CSV
- get_all_pending_files: normal listing, empty bucket, missing Contents key
- lambda_handler: success path, missing key error, bucket fallback from env, S3 failure
- get_s3_client: client creation (mocked boto3)

MOCKS USED:
- unittest.mock.patch / MagicMock for boto3.client (no real AWS calls)
- io.BytesIO / StringIO to simulate S3 object bodies
- pandas DataFrame.to_parquet patched to avoid real S3 writes

TODOs:
- TODO: Test pagination behaviour in get_all_pending_files (>1000 objects) — needs paginator refactor first
- TODO: Test actual parquet output schema/content once write path is injectable
- TODO: Test secrets manager integration once AWS_ACCESS_KEY is migrated
- TODO: Test concurrent lambda invocations / race conditions
"""

import io
import os
import pytest
import pandas as pd
from datetime import datetime
from unittest.mock import MagicMock, patch, call

# ---------------------------------------------------------------------------
# Module under test
# ---------------------------------------------------------------------------
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import data_pipeline
from data_pipeline import (
    validate_customer_record,
    process_csv,
    get_all_pending_files,
    lambda_handler,
    get_s3_client,
)


# ===========================================================================
# Fixtures & helpers
# ===========================================================================

def _make_csv_bytes(*rows: dict) -> bytes:
    """Build a CSV byte-string from a list of dicts (uniform keys required)."""
    if not rows:
        return b"customer_id,email,age,country_code\n"
    df = pd.DataFrame(rows)
    buf = io.BytesIO()
    df.to_csv(buf, index=False)
    return buf.getvalue()


def _s3_body(content: bytes) -> dict:
    """Wrap bytes in an S3-like get_object response dict."""
    return {"Body": io.BytesIO(content)}


VALID_RECORD = {
    "customer_id": "CUST-001",
    "email": "alice.chen@example.com",
    "age": 34,
    "country_code": "GB",
    "segment": "enterprise",
    "annual_revenue": 250000,
}

SYNTHETIC_VALID_RECORDS = [
    {"customer_id": "CUST-001", "email": "alice.chen@example.com",  "age": 34, "country_code": "GB",  "segment": "enterprise", "annual_revenue": 250000},
    {"customer_id": "CUST-002", "email": "bob.smith@example.com",   "age": 28, "country_code": "US",  "segment": "smb",        "annual_revenue": 45000},
    {"customer_id": "CUST-003", "email": "carol.jones@example.com", "age": 52, "country_code": "SG",  "segment": "enterprise", "annual_revenue": 500000},
    {"customer_id": "CUST-004", "email": "david.lee@example.com",   "age": 19, "country_code": "AU",  "segment": "consumer",   "annual_revenue": 0},
    {"customer_id": "CUST-005", "email": "emma.wilson@example.com", "age": 41, "country_code": "DE",  "segment": "smb",        "annual_revenue": 78000},
    {"customer_id": "CUST-006", "email": "frank.brown@example.com", "age": 67, "country_code": "US",  "segment": "enterprise", "annual_revenue": 320000},
]

SYNTHETIC_INVALID_RECORDS = [
    # CUST-007: invalid email
    {"customer_id": "CUST-007", "email": "invalid-email", "age": 25, "country_code": "GB", "segment": "consumer", "annual_revenue": 0},
    # CUST-008: age -1
    {"customer_id": "CUST-008", "email": "grace.kim@example.com", "age": -1, "country_code": "KR", "segment": "smb", "annual_revenue": 55000},
]


# ===========================================================================
# validate_customer_record
# ===========================================================================

class TestValidateCustomerRecord:

    def test_valid_record_returns_true(self):
        assert validate_customer_record(VALID_RECORD) is True

    @pytest.mark.parametrize("record", SYNTHETIC_VALID_RECORDS)
    def test_synthetic_valid_records_pass(self, record):
        assert validate_customer_record(record) is True

    # --- Missing required fields ---

    @pytest.mark.parametrize("missing_field", ["customer_id", "email", "age", "country_code"])
    def test_missing_required_field_raises(self, missing_field):
        record = VALID_RECORD.copy()
        del record[missing_field]
        with pytest.raises(ValueError, match=f"Missing required field: {missing_field}"):
            validate_customer_record(record)

    def test_empty_dict_raises_on_first_missing_field(self):
        with pytest.raises(ValueError, match="Missing required field: customer_id"):
            validate_customer_record({})

    # --- Email validation ---

    @pytest.mark.parametrize("bad_email", [
        "invalid-email",          # CUST-007 style
        "nodomain",
        "",
        "missingatsign.com",
    ])
    def test_invalid_email_raises(self, bad_email):
        record = {**VALID_RECORD, "email": bad_email}
        with pytest.raises(ValueError, match="Invalid email"):
            validate_customer_record(record)

    def test_valid_email_with_at_sign_passes(self):
        record = {**VALID_RECORD, "email": "user@domain.org"}
        assert validate_customer_record(record) is True

    def test_email_with_multiple_at_signs_passes(self):
        """The current check only requires '@' to be present."""
        record = {**VALID_RECORD, "email": "a@@b"}
        assert validate_customer_record(record) is True

    # --- Age boundary values ---

    @pytest.mark.parametrize("valid_age", [1, 2, 75, 149, 150])
    def test_age_within_bounds_passes(self, valid_age):
        record = {**VALID_RECORD, "age": valid_age}
        assert validate_customer_record(record) is True

    @pytest.mark.parametrize("invalid_age", [-1, 0, 151, 200, -100])
    def test_age_out_of_range_raises(self, invalid_age):
        record = {**VALID_RECORD, "age": invalid_age}
        with pytest.raises(ValueError, match="Age out of range"):
            validate_customer_record(record)

    def test_age_exactly_1_is_valid(self):
        assert validate_customer_record({**VALID_RECORD, "age": 1}) is True

    def test_age_exactly_150_is_valid(self):
        assert validate_customer_record({**VALID_RECORD, "age": 150}) is True

    def test_age_0_is_invalid(self):
        with pytest.raises(ValueError, match="Age out of range: 0"):
            validate_customer_record({**VALID_RECORD, "age": 0})

    def test_age_151_is_invalid(self):
        with pytest.raises(ValueError, match="Age out of range: 151"):
            validate_customer_record({**VALID_RECORD, "age": 151})

    @pytest.mark.parametrize("record", SYNTHETIC_INVALID_RECORDS)
    def test_synthetic_invalid_records_raise(self, record):
        with pytest.raises(ValueError):
            validate_customer_record(record)


# ===========================================================================
# get_s3_client
# ===========================================================================

class TestGetS3Client:

    @patch("data_pipeline.boto3.client")
    def test_returns_boto3_client(self, mock_boto3_client):
        fake_client = MagicMock()
        mock_boto3_client.return_value = fake_client

        result = get_s3_client()

        assert result is fake_client
        mock_boto3_client.assert_called_once_with(
            "s3",
            aws_access_key_id=data_pipeline.AWS_ACCESS_KEY,
            aws_secret_access_key=data_pipeline.AWS_SECRET_KEY,
            region_name="us-east-1",
        )

    @patch("data_pipeline.boto3.client")
    def test_uses_hardcoded_credentials(self, mock_boto3_client):
        """Guard against accidental credential change."""
        get_s3_client()
        _, kwargs = mock_boto3_client.call_args
        assert kwargs["aws_access_key_id"] == "AKIAIOSFODNN7EXAMPLE"
        assert kwargs["region_name"] == "us-east-1"


# ===========================================================================
# process_csv
# ===========================================================================

class TestProcessCsv:

    def _mock_client(self, csv_content: bytes) -> MagicMock:
        client = MagicMock()
        client.get_object.return_value = _s3_body(csv_content)
        return client

    @patch("data_pipeline.pd.DataFrame.to_parquet")
    @patch("data_pipeline.get_s3_client")
    def test_all_valid_rows_processed(self, mock_get_client, mock_to_parquet):
        rows = SYNTHETIC_VALID_RECORDS
        csv_bytes = _make_csv_bytes(*rows)
        mock_get_client.return_value = self._mock_client(csv_bytes)

        result = process_csv("my-bucket", "raw/customers.csv")

        assert result["processed"] == len(rows)
        assert result["failed"] == 0
        assert result["output_key"] == "processed/customers.parquet"
        assert "timestamp" in result

    @patch("data_pipeline.pd.DataFrame.to_parquet")
    @patch("data_pipeline.get_s3_client")
    def test_invalid_rows_counted_as_failed(self, mock_get_client, mock_to_parquet):
        rows = SYNTHETIC_VALID_RECORDS + SYNTHETIC_INVALID_RECORDS
        csv_bytes = _make_csv_bytes(*rows)
        mock_get_client.return_value = self._mock_client(csv_bytes)

        result = process_csv("my-bucket", "raw/customers.csv")

        assert result["processed"] == len(SYNTHETIC_VALID_RECORDS)
        assert result["failed"] == len(SYNTHETIC_INVALID_RECORDS)

    @patch("data_pipeline.pd.DataFrame.to_parquet")
    @patch("data_pipeline.get_s3_client")
    def test_all_invalid_rows_results_in_zero_processed(self, mock_get_client, mock_to_parquet):
        rows = SYNTHETIC_INVALID_RECORDS
        csv_bytes = _make_csv_bytes(*rows)
        mock_get_client.return_value = self._mock_client(csv_bytes)

        result = process_csv("my-bucket", "raw/data.csv")

        assert result["processed"] == 0
        assert result["failed"] == len(rows)

    @patch("data_pipeline.pd.DataFrame.to_parquet")
    @patch("data_pipeline.get_s3_client")
    def test_empty_csv_returns_zero_counts(self, mock_get_client, mock_to_parquet):
        csv_bytes = b"customer_id,email,age,country_code\n"
        mock_get_client.return_value = self._mock_client(csv_bytes)

        result = process_csv("my-bucket", "raw/empty.csv")

        assert result["processed"] == 0
        assert result["failed"] == 0

    @patch("data_pipeline.pd.DataFrame.to_parquet")
    @patch("data_pipeline.get_s3_client")
    def test_output_key_replaces_raw_prefix_and_extension(self, mock_get_client, mock_to_parquet):
        csv_bytes = _make_csv_bytes(*SYNTHETIC_VALID_RECORDS[:1])
        mock_get_client.return_value = self._mock_client(csv_bytes)

        result = process_csv("bucket", "raw/2024/01/customers.csv")

        assert result["output_key"] == "processed/2024/01/customers.parquet"

    @patch("data_pipeline.pd.DataFrame.to_parquet")
    @patch("data_pipeline.get_s3_client")
    def test_to_parquet_called_with_s3_path(self, mock_get_client, mock_to_parquet):
        csv_bytes = _make_csv_bytes(*SYNTHETIC_VALID_RECORDS[:1])
        mock_get_client.return_value = self._mock_client(csv_bytes)

        process_csv("my-bucket", "raw/file.csv")

        mock_to_parquet.assert_called_once_with("s3://my-bucket/processed/file.parquet")

    @patch("data_pipeline.pd.DataFrame.to_parquet")
    @patch("data_pipeline.get_s3_client")
    def test_s3_get_object_called_correctly(self, mock_get_client, mock_to_parquet):
        csv_bytes = _make_csv_bytes(*SYNTHETIC_VALID_RECORDS[:1])
        mock_client = self._mock_client(csv_bytes)
        mock_get_client.return_value = mock_client

        process_csv("my-bucket", "raw/file.csv")

        mock_client.get_object.assert_called_once_with(Bucket="my-bucket", Key="raw/file.csv")

    @patch("data_pipeline.get_s3_client")
    def test_s3_get_object_failure_propagates(self, mock_get_client):
        mock_client = MagicMock()
        mock_client.get_object.side_effect = Exception("S3 unavailable")
        mock_get_client.return_value = mock_client

        with pytest.raises(Exception, match="S3 unavailable"):
            process_csv("my-bucket", "raw/file.csv")

    @patch("data_pipeline.pd.DataFrame.to_parquet")
    @patch("data_pipeline.get_s3_client")
    def test_result_contains_timestamp(self, mock_get_client, mock_to_parquet):
        csv_bytes = _make_csv_bytes(*SYNTHETIC_VALID_RECORDS[:1])
        mock_get_client.return_value = self._mock_client(csv_bytes)

        before = datetime.utcnow().isoformat()
        result = process_csv("my-bucket", "raw/file.csv")
        after = datetime.utcnow().isoformat()

        assert before <= result["timestamp"] <= after

    @patch("data_pipeline.pd.DataFrame.to_parquet")
    @patch("data_pipeline.get_s3_client")
    def test_single_valid_record(self, mock_get_client, mock_to_parquet):
        csv_bytes = _make_csv_bytes(SYNTHETIC_VALID_RECORDS[0])
        mock_get_client.return_value = self._mock_client(csv_bytes)

        result = process_csv("bucket", "raw/one.csv")

        assert result["processed"] == 1
        assert result["failed"] == 0

    @patch("data_pipeline.pd.DataFrame.to_parquet", side_effect=OSError("write failed"))
    @patch("data_pipeline.get_s3