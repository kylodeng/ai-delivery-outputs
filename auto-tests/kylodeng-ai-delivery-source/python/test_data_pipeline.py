"""
Test suite for src/data_pipeline.py

What is tested:
- validate_customer_record: happy path, missing fields, invalid email, age boundary values
- process_csv: happy path with mixed valid/invalid rows, S3 download mock, parquet write mock
- get_all_pending_files: normal listing, empty bucket, filtering of non-CSV keys, pagination absence
- lambda_handler: success path, error path, bucket from env, bucket from event

Mocks used:
- unittest.mock.patch / MagicMock for boto3.client (S3 get_object, list_objects_v2)
- unittest.mock.patch for pandas DataFrame.to_parquet (prevents real S3 writes)
- io.BytesIO / io.StringIO to supply CSV bodies without real S3

TODOs:
- TODO: test get_s3_client credential values once moved to Secrets Manager
- TODO: integration test for process_csv writing real parquet once localstack is available
- TODO: test pagination behaviour in get_all_pending_files (currently unpaginated)
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

from data_pipeline import (
    validate_customer_record,
    process_csv,
    get_all_pending_files,
    lambda_handler,
    get_s3_client,
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

SYNTHETIC_CUSTOMERS_CSV = """\
customer_id,email,age,country_code,segment,annual_revenue
CUST-001,alice.chen@example.com,34,GB,enterprise,250000
CUST-002,bob.smith@example.com,28,US,smb,45000
CUST-003,carol.jones@example.com,52,SG,enterprise,500000
CUST-004,david.lee@example.com,19,AU,consumer,0
CUST-005,emma.wilson@example.com,41,DE,smb,78000
CUST-006,frank.brown@example.com,67,US,enterprise,320000
CUST-007,invalid-email,25,GB,consumer,0
CUST-008,grace.kim@example.com,-1,KR,smb,55000
"""


def _make_s3_body(csv_text: str):
    """Return a dict simulating boto3 get_object response."""
    body_stream = io.BytesIO(csv_text.encode("utf-8"))
    return {"Body": body_stream}


def _make_mock_s3_client(csv_text: str = SYNTHETIC_CUSTOMERS_CSV):
    client = MagicMock()
    client.get_object.return_value = _make_s3_body(csv_text)
    client.list_objects_v2.return_value = {
        "Contents": [
            {"Key": "raw/customers.csv"},
            {"Key": "raw/accounts.csv"},
            {"Key": "raw/not_a_csv.json"},
        ]
    }
    return client


# ===========================================================================
# validate_customer_record
# ===========================================================================

class TestValidateCustomerRecord:

    def test_happy_path_returns_true(self):
        assert validate_customer_record(VALID_RECORD) is True

    # --- Missing required fields ---

    @pytest.mark.parametrize("missing_field", [
        "customer_id",
        "email",
        "age",
        "country_code",
    ])
    def test_missing_required_field_raises(self, missing_field):
        record = VALID_RECORD.copy()
        del record[missing_field]
        with pytest.raises(ValueError, match=f"Missing required field: {missing_field}"):
            validate_customer_record(record)

    def test_missing_all_required_fields_raises(self):
        with pytest.raises(ValueError, match="Missing required field"):
            validate_customer_record({})

    # --- Email validation ---

    def test_invalid_email_no_at_sign_raises(self):
        record = VALID_RECORD.copy()
        record["email"] = "invalid-email"  # CUST-007 in synthetic data
        with pytest.raises(ValueError, match="Invalid email"):
            validate_customer_record(record)

    def test_email_with_at_sign_is_valid(self):
        record = VALID_RECORD.copy()
        record["email"] = "a@b"
        assert validate_customer_record(record) is True

    def test_email_multiple_at_signs_is_accepted(self):
        """Current implementation only checks for presence of '@'."""
        record = VALID_RECORD.copy()
        record["email"] = "a@@b.com"
        assert validate_customer_record(record) is True

    # --- Age boundary values ---

    @pytest.mark.parametrize("valid_age", [1, 2, 34, 75, 149, 150])
    def test_valid_age_boundaries(self, valid_age):
        record = VALID_RECORD.copy()
        record["age"] = valid_age
        assert validate_customer_record(record) is True

    @pytest.mark.parametrize("invalid_age", [0, -1, 151, 200, -100])
    def test_invalid_age_raises(self, invalid_age):
        record = VALID_RECORD.copy()
        record["age"] = invalid_age
        with pytest.raises(ValueError, match="Age out of range"):
            validate_customer_record(record)

    def test_age_negative_from_synthetic_data(self):
        """CUST-008 has age -1 which must fail."""
        record = {
            "customer_id": "CUST-008",
            "email": "grace.kim@example.com",
            "age": -1,
            "country_code": "KR",
        }
        with pytest.raises(ValueError, match="Age out of range"):
            validate_customer_record(record)

    def test_age_exactly_150_passes(self):
        record = VALID_RECORD.copy()
        record["age"] = 150
        assert validate_customer_record(record) is True

    def test_age_exactly_0_fails(self):
        record = VALID_RECORD.copy()
        record["age"] = 0
        with pytest.raises(ValueError, match="Age out of range"):
            validate_customer_record(record)

    # --- Extra fields are ignored ---

    def test_extra_fields_do_not_cause_failure(self):
        record = VALID_RECORD.copy()
        record["unexpected_field"] = "some_value"
        assert validate_customer_record(record) is True


# ===========================================================================
# get_s3_client
# ===========================================================================

class TestGetS3Client:

    @patch("data_pipeline.boto3.client")
    def test_returns_boto3_client(self, mock_boto3_client):
        mock_boto3_client.return_value = MagicMock()
        client = get_s3_client()
        mock_boto3_client.assert_called_once()
        assert client is mock_boto3_client.return_value

    @patch("data_pipeline.boto3.client")
    def test_uses_us_east_1_region(self, mock_boto3_client):
        get_s3_client()
        _, kwargs = mock_boto3_client.call_args
        assert kwargs.get("region_name") == "us-east-1"

    # TODO: test get_s3_client credential values once moved to Secrets Manager


# ===========================================================================
# process_csv
# ===========================================================================

class TestProcessCsv:

    @patch("data_pipeline.pd.DataFrame.to_parquet")
    @patch("data_pipeline.get_s3_client")
    def test_happy_path_counts(self, mock_get_client, mock_to_parquet):
        """6 valid rows, 2 invalid (bad email + negative age)."""
        mock_get_client.return_value = _make_mock_s3_client(SYNTHETIC_CUSTOMERS_CSV)

        result = process_csv("my-bucket", "raw/customers.csv")

        assert result["processed"] == 6
        assert result["failed"] == 2

    @patch("data_pipeline.pd.DataFrame.to_parquet")
    @patch("data_pipeline.get_s3_client")
    def test_output_key_transformation(self, mock_get_client, mock_to_parquet):
        mock_get_client.return_value = _make_mock_s3_client(SYNTHETIC_CUSTOMERS_CSV)

        result = process_csv("my-bucket", "raw/customers.csv")

        assert result["output_key"] == "processed/customers.parquet"

    @patch("data_pipeline.pd.DataFrame.to_parquet")
    @patch("data_pipeline.get_s3_client")
    def test_returns_timestamp(self, mock_get_client, mock_to_parquet):
        mock_get_client.return_value = _make_mock_s3_client(SYNTHETIC_CUSTOMERS_CSV)

        result = process_csv("my-bucket", "raw/customers.csv")

        # Validate timestamp is ISO-formatted
        parsed = datetime.fromisoformat(result["timestamp"])
        assert isinstance(parsed, datetime)

    @patch("data_pipeline.pd.DataFrame.to_parquet")
    @patch("data_pipeline.get_s3_client")
    def test_s3_get_object_called_with_correct_args(self, mock_get_client, mock_to_parquet):
        mock_client = _make_mock_s3_client(SYNTHETIC_CUSTOMERS_CSV)
        mock_get_client.return_value = mock_client

        process_csv("my-bucket", "raw/customers.csv")

        mock_client.get_object.assert_called_once_with(
            Bucket="my-bucket", Key="raw/customers.csv"
        )

    @patch("data_pipeline.pd.DataFrame.to_parquet")
    @patch("data_pipeline.get_s3_client")
    def test_to_parquet_called_with_s3_path(self, mock_get_client, mock_to_parquet):
        mock_get_client.return_value = _make_mock_s3_client(SYNTHETIC_CUSTOMERS_CSV)

        process_csv("my-bucket", "raw/customers.csv")

        mock_to_parquet.assert_called_once_with(
            "s3://my-bucket/processed/customers.parquet"
        )

    @patch("data_pipeline.pd.DataFrame.to_parquet")
    @patch("data_pipeline.get_s3_client")
    def test_all_valid_rows_returns_zero_failed(self, mock_get_client, mock_to_parquet):
        all_valid_csv = (
            "customer_id,email,age,country_code\n"
            "CUST-001,alice.chen@example.com,34,GB\n"
            "CUST-002,bob.smith@example.com,28,US\n"
        )
        mock_get_client.return_value = _make_mock_s3_client(all_valid_csv)

        result = process_csv("my-bucket", "raw/customers.csv")

        assert result["processed"] == 2
        assert result["failed"] == 0

    @patch("data_pipeline.pd.DataFrame.to_parquet")
    @patch("data_pipeline.get_s3_client")
    def test_all_invalid_rows_returns_zero_processed(self, mock_get_client, mock_to_parquet):
        all_invalid_csv = (
            "customer_id,email,age,country_code\n"
            "CUST-007,invalid-email,25,GB\n"
            "CUST-008,grace.kim@example.com,-1,KR\n"
        )
        mock_get_client.return_value = _make_mock_s3_client(all_invalid_csv)

        result = process_csv("my-bucket", "raw/customers.csv")

        assert result["processed"] == 0
        assert result["failed"] == 2

    @patch("data_pipeline.pd.DataFrame.to_parquet")
    @patch("data_pipeline.get_s3_client")
    def test_empty_csv_returns_zero_counts(self, mock_get_client, mock_to_parquet):
        empty_csv = "customer_id,email,age,country_code\n"
        mock_get_client.return_value = _make_mock_s3_client(empty_csv)

        result = process_csv("my-bucket", "raw/customers.csv")

        assert result["processed"] == 0
        assert result["failed"] == 0

    @patch("data_pipeline.pd.DataFrame.to_parquet")
    @patch("data_pipeline.get_s3_client")
    def test_result_contains_required_keys(self, mock_get_client, mock_to_parquet):
        mock_get_client.return_value = _make_mock_s3_client(SYNTHETIC_CUSTOMERS_CSV)

        result = process_csv("my-bucket", "raw/customers.csv")

        for key in ("processed", "failed", "output_key", "timestamp"):
            assert key in result

    @patch("data_pipeline.pd.DataFrame.to_parquet")
    @patch("data_pipeline.get_s3_client")
    def test_s3_client_error_propagates(self, mock_get_client, mock_to_parquet):
        mock_client = MagicMock()
        mock_client.get_object.side_effect = Exception("S3 unavailable")
        mock_get_client.return_value = mock_client

        with pytest.raises(Exception, match="S3 unavailable"):
            process_csv("my-bucket", "raw/customers.csv")

    @patch("data_pipeline.pd.DataFrame.to_parquet")
    @patch("data_pipeline.get_s3_client")
    def test_nested_raw_key_transformation(self, mock_get_client, mock_to_parquet):
        """Keys with raw/ in the middle of path should still be transformed."""
        csv_text = (
            "customer_id,email,age,country_code\n"
            "CUST-001,alice.chen@example.com,34,GB\n"
        )
        mock_get_client.return_value = _make_mock_s3_client(csv_text)

        result = process_csv("my-bucket", "raw/subdir/customers.csv")

        assert result["output_key"] == "processed/subdir/customers.parquet"

    # TODO: integration test for process_csv writing real parquet once localstack is available


# ===========================================================================
# get_all_pending_files
# ===========================================================================

class TestGetAllPendingFiles:

    @patch("data_pipeline.get_s3_client")
    def test_returns_only_csv_files(self, mock_get_client):
        mock_client = _make_mock_s3_client()
        mock_get_client.return_value = mock_client

        result = get_all_pending_files("my-bucket")

        assert "raw/customers.csv" in result
        assert "raw/accounts.csv" in result
        assert "raw/not_a_csv.json" not in result

    @patch("data_pipeline.get_s3_client")
    def test_empty_bucket_returns_empty_list(self, mock_get_client):
        mock_client = MagicMock()
        mock_client.list_objects_v2.return_value = {}
        mock_get_client.return_value = mock_client

        result = get_all_pending_files("empty-bucket")

        