"""
Test suite for src/data_pipeline.py

What is tested:
    - validate_customer_record: required fields, email validation, age boundary values
    - process_csv: happy path with valid/invalid rows, S3 download, parquet upload
    - get_all_pending_files: listing CSVs from S3 with/without results, pagination gap
    - get_s3_client: client construction (credentials wiring)
    - lambda_handler: success path, error path, missing bucket fallback to env var

Mocks used:
    - boto3.client (via unittest.mock.patch) — no real AWS calls
    - pandas DataFrame.to_parquet — patched to avoid real S3/filesystem writes
    - os.environ — patched for LANDING_BUCKET tests

TODOs:
    - TODO: test pagination behaviour in get_all_pending_files once implemented
    - TODO: test actual parquet output schema once a schema is defined
    - TODO: test secrets-manager integration once AWS_ACCESS_KEY/SECRET are moved
    - TODO: test malformed / non-UTF-8 CSV once error handling is added to process_csv
"""

import io
import os
import logging
import pytest
from datetime import datetime
from unittest.mock import MagicMock, patch, PropertyMock

import pandas as pd

# ---------------------------------------------------------------------------
# Module under test
# ---------------------------------------------------------------------------
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from data_pipeline import (
    validate_customer_record,
    process_csv,
    get_all_pending_files,
    get_s3_client,
    lambda_handler,
)


# ===========================================================================
# Helpers / fixtures
# ===========================================================================

VALID_RECORD = {
    "customer_id": "CUST-001",
    "email": "alice.chen@example.com",
    "age": 34,
    "country_code": "GB",
    "segment": "enterprise",
    "annual_revenue": 250000,
}

# Synthetic data rows derived from customers_sample.csv
VALID_RECORDS = [
    {"customer_id": "CUST-001", "email": "alice.chen@example.com",  "age": 34, "country_code": "GB", "segment": "enterprise", "annual_revenue": 250000},
    {"customer_id": "CUST-002", "email": "bob.smith@example.com",   "age": 28, "country_code": "US", "segment": "smb",        "annual_revenue": 45000},
    {"customer_id": "CUST-003", "email": "carol.jones@example.com", "age": 52, "country_code": "SG", "segment": "enterprise", "annual_revenue": 500000},
    {"customer_id": "CUST-004", "email": "david.lee@example.com",   "age": 19, "country_code": "AU", "segment": "consumer",   "annual_revenue": 0},
    {"customer_id": "CUST-005", "email": "emma.wilson@example.com", "age": 41, "country_code": "DE", "segment": "smb",        "annual_revenue": 78000},
    {"customer_id": "CUST-006", "email": "frank.brown@example.com", "age": 67, "country_code": "US", "segment": "enterprise", "annual_revenue": 320000},
]

INVALID_RECORDS = [
    # invalid email (no @)
    {"customer_id": "CUST-007", "email": "invalid-email", "age": 25, "country_code": "GB", "segment": "consumer", "annual_revenue": 0},
    # age out of range: negative
    {"customer_id": "CUST-008", "email": "grace.kim@example.com", "age": -1, "country_code": "KR", "segment": "smb", "annual_revenue": 55000},
]


def _make_csv_body(records: list) -> io.BytesIO:
    """Return a BytesIO CSV stream from a list of dicts."""
    df = pd.DataFrame(records)
    buf = io.BytesIO()
    df.to_csv(buf, index=False)
    buf.seek(0)
    return buf


def _mock_s3_client_for_process_csv(csv_body: io.BytesIO) -> MagicMock:
    """Return a mock boto3 S3 client whose get_object returns csv_body."""
    mock_client = MagicMock()
    mock_client.get_object.return_value = {"Body": csv_body}
    return mock_client


# ===========================================================================
# validate_customer_record
# ===========================================================================

class TestValidateCustomerRecord:

    # --- happy path ---

    def test_valid_record_returns_true(self):
        assert validate_customer_record(VALID_RECORD) is True

    @pytest.mark.parametrize("record", VALID_RECORDS)
    def test_all_synthetic_valid_records_pass(self, record):
        assert validate_customer_record(record) is True

    # --- boundary values for age ---

    def test_age_minimum_boundary_passes(self):
        rec = {**VALID_RECORD, "age": 1}
        assert validate_customer_record(rec) is True

    def test_age_maximum_boundary_passes(self):
        rec = {**VALID_RECORD, "age": 150}
        assert validate_customer_record(rec) is True

    def test_age_just_below_minimum_raises(self):
        rec = {**VALID_RECORD, "age": 0}
        with pytest.raises(ValueError, match="Age out of range"):
            validate_customer_record(rec)

    def test_age_just_above_maximum_raises(self):
        rec = {**VALID_RECORD, "age": 151}
        with pytest.raises(ValueError, match="Age out of range"):
            validate_customer_record(rec)

    def test_age_negative_raises(self):
        rec = {**VALID_RECORD, "age": -1}
        with pytest.raises(ValueError, match="Age out of range"):
            validate_customer_record(rec)

    def test_age_zero_raises(self):
        rec = {**VALID_RECORD, "age": 0}
        with pytest.raises(ValueError, match="Age out of range"):
            validate_customer_record(rec)

    def test_age_very_large_raises(self):
        rec = {**VALID_RECORD, "age": 9999}
        with pytest.raises(ValueError, match="Age out of range"):
            validate_customer_record(rec)

    # --- email validation ---

    def test_invalid_email_no_at_sign_raises(self):
        rec = {**VALID_RECORD, "email": "invalid-email"}
        with pytest.raises(ValueError, match="Invalid email"):
            validate_customer_record(rec)

    def test_email_with_at_sign_passes(self):
        rec = {**VALID_RECORD, "email": "a@b"}
        assert validate_customer_record(rec) is True

    def test_email_empty_string_raises(self):
        rec = {**VALID_RECORD, "email": ""}
        with pytest.raises(ValueError, match="Invalid email"):
            validate_customer_record(rec)

    # --- missing required fields ---

    @pytest.mark.parametrize("missing_field", ["customer_id", "email", "age", "country_code"])
    def test_missing_required_field_raises(self, missing_field):
        rec = {k: v for k, v in VALID_RECORD.items() if k != missing_field}
        with pytest.raises(ValueError, match=f"Missing required field: {missing_field}"):
            validate_customer_record(rec)

    def test_empty_record_raises_on_first_required_field(self):
        with pytest.raises(ValueError, match="Missing required field: customer_id"):
            validate_customer_record({})

    def test_extra_fields_are_ignored(self):
        rec = {**VALID_RECORD, "extra_field": "unexpected_value"}
        assert validate_customer_record(rec) is True

    # --- parameterised invalid synthetic rows ---

    @pytest.mark.parametrize("record,expected_match", [
        (INVALID_RECORDS[0], "Invalid email"),
        (INVALID_RECORDS[1], "Age out of range"),
    ])
    def test_invalid_synthetic_records_raise(self, record, expected_match):
        with pytest.raises(ValueError, match=expected_match):
            validate_customer_record(record)


# ===========================================================================
# get_s3_client
# ===========================================================================

class TestGetS3Client:

    @patch("data_pipeline.boto3.client")
    def test_returns_boto3_client(self, mock_boto3_client):
        mock_instance = MagicMock()
        mock_boto3_client.return_value = mock_instance

        result = get_s3_client()

        assert result is mock_instance

    @patch("data_pipeline.boto3.client")
    def test_called_with_correct_region(self, mock_boto3_client):
        get_s3_client()
        _, kwargs = mock_boto3_client.call_args
        assert kwargs.get("region_name") == "us-east-1"

    @patch("data_pipeline.boto3.client")
    def test_called_with_s3_service(self, mock_boto3_client):
        get_s3_client()
        args, _ = mock_boto3_client.call_args
        assert args[0] == "s3"

    @patch("data_pipeline.boto3.client")
    def test_credentials_passed_to_client(self, mock_boto3_client):
        get_s3_client()
        _, kwargs = mock_boto3_client.call_args
        assert "aws_access_key_id" in kwargs
        assert "aws_secret_access_key" in kwargs


# ===========================================================================
# process_csv
# ===========================================================================

class TestProcessCsv:

    BUCKET = "my-landing-bucket"
    KEY = "raw/customers_2024.csv"
    EXPECTED_OUTPUT_KEY = "processed/customers_2024.parquet"

    def _patch_s3_and_parquet(self, csv_body: io.BytesIO):
        """
        Returns context managers: patch get_s3_client and DataFrame.to_parquet.
        Usage: use with patch(...) as ..., patch(...) as ...
        """
        mock_client = _mock_s3_client_for_process_csv(csv_body)
        return mock_client

    # --- happy path: all rows valid ---

    @patch("data_pipeline.pd.DataFrame.to_parquet")
    @patch("data_pipeline.get_s3_client")
    def test_all_valid_rows_processed(self, mock_get_client, mock_to_parquet):
        csv_body = _make_csv_body(VALID_RECORDS)
        mock_get_client.return_value = _mock_s3_client_for_process_csv(csv_body)

        result = process_csv(self.BUCKET, self.KEY)

        assert result["processed"] == len(VALID_RECORDS)
        assert result["failed"] == 0

    @patch("data_pipeline.pd.DataFrame.to_parquet")
    @patch("data_pipeline.get_s3_client")
    def test_output_key_replaces_raw_prefix_and_csv_extension(self, mock_get_client, mock_to_parquet):
        csv_body = _make_csv_body(VALID_RECORDS)
        mock_get_client.return_value = _mock_s3_client_for_process_csv(csv_body)

        result = process_csv(self.BUCKET, self.KEY)

        assert result["output_key"] == self.EXPECTED_OUTPUT_KEY

    @patch("data_pipeline.pd.DataFrame.to_parquet")
    @patch("data_pipeline.get_s3_client")
    def test_result_contains_timestamp(self, mock_get_client, mock_to_parquet):
        csv_body = _make_csv_body(VALID_RECORDS)
        mock_get_client.return_value = _mock_s3_client_for_process_csv(csv_body)

        result = process_csv(self.BUCKET, self.KEY)

        assert "timestamp" in result
        # Should be parseable as ISO datetime
        datetime.fromisoformat(result["timestamp"])

    @patch("data_pipeline.pd.DataFrame.to_parquet")
    @patch("data_pipeline.get_s3_client")
    def test_parquet_written_to_correct_s3_path(self, mock_get_client, mock_to_parquet):
        csv_body = _make_csv_body(VALID_RECORDS)
        mock_get_client.return_value = _mock_s3_client_for_process_csv(csv_body)

        process_csv(self.BUCKET, self.KEY)

        expected_path = f"s3://{self.BUCKET}/{self.EXPECTED_OUTPUT_KEY}"
        mock_to_parquet.assert_called_once_with(expected_path)

    # --- mixed valid and invalid rows ---

    @patch("data_pipeline.pd.DataFrame.to_parquet")
    @patch("data_pipeline.get_s3_client")
    def test_invalid_rows_counted_in_failed(self, mock_get_client, mock_to_parquet):
        mixed = VALID_RECORDS + INVALID_RECORDS
        csv_body = _make_csv_body(mixed)
        mock_get_client.return_value = _mock_s3_client_for_process_csv(csv_body)

        result = process_csv(self.BUCKET, self.KEY)

        assert result["processed"] == len(VALID_RECORDS)
        assert result["failed"] == len(INVALID_RECORDS)

    @patch("data_pipeline.pd.DataFrame.to_parquet")
    @patch("data_pipeline.get_s3_client")
    def test_all_invalid_rows_yields_zero_processed(self, mock_get_client, mock_to_parquet):
        csv_body = _make_csv_body(INVALID_RECORDS)
        mock_get_client.return_value = _mock_s3_client_for_process_csv(csv_body)

        result = process_csv(self.BUCKET, self.KEY)

        assert result["processed"] == 0
        assert result["failed"] == len(INVALID_RECORDS)

    # --- empty CSV ---

    @patch("data_pipeline.pd.DataFrame.to_parquet")
    @patch("data_pipeline.get_s3_client")
    def test_empty_csv_returns_zero_counts(self, mock_get_client, mock_to_parquet):
        # CSV with header only, no data rows
        csv_body = _make_csv_body([])
        mock_get_client.return_value = _mock_s3_client_for_process_csv(csv_body)

        result = process_csv(self.BUCKET, self.KEY)

        assert result["processed"] == 0
        assert result["failed"] == 0

    # --- S3 get_object failure ---

    @patch("data_pipeline.get_s3_client")
    def test_s3_get_object_error_propagates(self, mock_get_client):
        mock_client = MagicMock()
        mock_client.get_object.side_effect = Exception("S3 access denied")
        mock_get_client.return_value = mock_client

        with pytest.raises(Exception, match="S3 access denied"):
            process_csv(self.BUCKET, self.KEY)

    # --- key path variations ---

    @patch("data_pipeline.pd.DataFrame.to_parquet")
    @patch("data_pipeline.get_s3_client")
    def test_nested_raw_key_transforms_correctly(self, mock_get_client, mock_to_parquet):
        key = "raw/2024/01/customers.csv"
        csv_body = _make_csv_body(