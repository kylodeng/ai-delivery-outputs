"""
Test module for src/data_pipeline.py

What is tested:
    - validate_customer_record: happy path, missing fields, invalid email, age boundaries
    - process_csv: successful processing, mixed valid/invalid rows, S3 interaction
    - get_all_pending_files: normal listing, empty bucket, filtering non-CSV keys
    - lambda_handler: success response, error handling, missing bucket/key scenarios
    - get_s3_client: basic construction (boto3 patched)

Mocks used:
    - unittest.mock.patch / MagicMock for boto3.client (all S3 calls)
    - unittest.mock.patch for pandas DataFrame.to_parquet (S3 write)
    - io.BytesIO / io.StringIO for S3 object bodies

TODOs:
    - TODO: Test pagination behaviour in get_all_pending_files once implemented
    - TODO: Test secrets-manager credential retrieval once hardcoded keys are removed
    - TODO: Test malformed/corrupt CSV handling once error handling is added to process_csv
    - TODO: Integration test verifying parquet schema of output once schema is formalised
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
from src.data_pipeline import (
    get_s3_client,
    validate_customer_record,
    process_csv,
    get_all_pending_files,
    lambda_handler,
)

# ---------------------------------------------------------------------------
# Synthetic / fixture data
# ---------------------------------------------------------------------------

VALID_CUSTOMERS_CSV = (
    "customer_id,email,age,country_code,segment,annual_revenue\n"
    "CUST-001,alice.chen@example.com,34,GB,enterprise,250000\n"
    "CUST-002,bob.smith@example.com,28,US,smb,45000\n"
    "CUST-003,carol.jones@example.com,52,SG,enterprise,500000\n"
    "CUST-004,david.lee@example.com,19,AU,consumer,0\n"
    "CUST-005,emma.wilson@example.com,41,DE,smb,78000\n"
    "CUST-006,frank.brown@example.com,67,US,enterprise,320000\n"
)

MIXED_CUSTOMERS_CSV = (
    "customer_id,email,age,country_code,segment,annual_revenue\n"
    "CUST-001,alice.chen@example.com,34,GB,enterprise,250000\n"
    "CUST-007,invalid-email,25,GB,consumer,0\n"           # bad email
    "CUST-008,grace.kim@example.com,-1,KR,smb,55000\n"    # age out of range
)

ONLY_INVALID_CSV = (
    "customer_id,email,age,country_code,segment,annual_revenue\n"
    "CUST-007,invalid-email,25,GB,consumer,0\n"
    "CUST-008,grace.kim@example.com,-1,KR,smb,55000\n"
    "CUST-008,grace.kim@example.com,200,KR,smb,55000\n"   # age > 150
)

MISSING_FIELD_CSV = (
    "customer_id,age,country_code\n"
    "CUST-010,30,US\n"
)


def _make_s3_body(csv_text: str):
    """Return a dict mimicking a boto3 get_object response."""
    return {"Body": io.StringIO(csv_text)}


# ---------------------------------------------------------------------------
# get_s3_client
# ---------------------------------------------------------------------------

class TestGetS3Client:
    @patch("src.data_pipeline.boto3.client")
    def test_returns_boto3_client(self, mock_boto_client):
        mock_client = MagicMock()
        mock_boto_client.return_value = mock_client

        result = get_s3_client()

        mock_boto_client.assert_called_once_with(
            "s3",
            aws_access_key_id="AKIAIOSFODNN7EXAMPLE",
            aws_secret_access_key="wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
            region_name="us-east-1",
        )
        assert result is mock_client

    @patch("src.data_pipeline.boto3.client")
    def test_called_each_invocation(self, mock_boto_client):
        """get_s3_client should create a new client each time (no caching)."""
        get_s3_client()
        get_s3_client()
        assert mock_boto_client.call_count == 2


# ---------------------------------------------------------------------------
# validate_customer_record
# ---------------------------------------------------------------------------

class TestValidateCustomerRecord:

    # --- happy path ---

    @pytest.mark.parametrize("record", [
        {"customer_id": "CUST-001", "email": "alice.chen@example.com", "age": 34,  "country_code": "GB"},
        {"customer_id": "CUST-002", "email": "bob.smith@example.com",  "age": 28,  "country_code": "US"},
        {"customer_id": "CUST-003", "email": "carol.jones@example.com","age": 52,  "country_code": "SG"},
        {"customer_id": "CUST-005", "email": "emma.wilson@example.com","age": 41,  "country_code": "DE"},
        {"customer_id": "CUST-006", "email": "frank.brown@example.com","age": 67,  "country_code": "US"},
    ])
    def test_valid_records_return_true(self, record):
        assert validate_customer_record(record) is True

    # --- age boundary values ---

    def test_age_minimum_boundary(self):
        record = {"customer_id": "X", "email": "a@b.com", "age": 1, "country_code": "US"}
        assert validate_customer_record(record) is True

    def test_age_maximum_boundary(self):
        record = {"customer_id": "X", "email": "a@b.com", "age": 150, "country_code": "US"}
        assert validate_customer_record(record) is True

    def test_age_just_below_minimum_raises(self):
        record = {"customer_id": "X", "email": "a@b.com", "age": 0, "country_code": "US"}
        with pytest.raises(ValueError, match="Age out of range"):
            validate_customer_record(record)

    def test_age_just_above_maximum_raises(self):
        record = {"customer_id": "X", "email": "a@b.com", "age": 151, "country_code": "US"}
        with pytest.raises(ValueError, match="Age out of range"):
            validate_customer_record(record)

    def test_negative_age_raises(self):
        """CUST-008 has age -1."""
        record = {"customer_id": "CUST-008", "email": "grace.kim@example.com", "age": -1, "country_code": "KR"}
        with pytest.raises(ValueError, match="Age out of range"):
            validate_customer_record(record)

    def test_very_large_age_raises(self):
        record = {"customer_id": "X", "email": "a@b.com", "age": 999, "country_code": "US"}
        with pytest.raises(ValueError, match="Age out of range"):
            validate_customer_record(record)

    # --- email validation ---

    def test_missing_at_sign_raises(self):
        """CUST-007 has 'invalid-email' with no @."""
        record = {"customer_id": "CUST-007", "email": "invalid-email", "age": 25, "country_code": "GB"}
        with pytest.raises(ValueError, match="Invalid email"):
            validate_customer_record(record)

    def test_email_with_at_sign_passes(self):
        record = {"customer_id": "X", "email": "minimal@x", "age": 30, "country_code": "US"}
        assert validate_customer_record(record) is True

    # --- missing required fields ---

    @pytest.mark.parametrize("missing_field", ["customer_id", "email", "age", "country_code"])
    def test_missing_required_field_raises(self, missing_field):
        record = {
            "customer_id": "X",
            "email": "a@b.com",
            "age": 30,
            "country_code": "US",
        }
        del record[missing_field]
        with pytest.raises(ValueError, match=f"Missing required field: {missing_field}"):
            validate_customer_record(record)

    def test_empty_dict_raises_on_first_required_field(self):
        with pytest.raises(ValueError, match="Missing required field: customer_id"):
            validate_customer_record({})

    def test_extra_fields_are_allowed(self):
        """Extra columns (segment, annual_revenue) must not break validation."""
        record = {
            "customer_id": "CUST-001",
            "email": "alice.chen@example.com",
            "age": 34,
            "country_code": "GB",
            "segment": "enterprise",
            "annual_revenue": 250000,
        }
        assert validate_customer_record(record) is True


# ---------------------------------------------------------------------------
# process_csv
# ---------------------------------------------------------------------------

class TestProcessCsv:

    def _make_mock_client(self, csv_text: str):
        mock_client = MagicMock()
        mock_client.get_object.return_value = _make_s3_body(csv_text)
        return mock_client

    @patch("src.data_pipeline.pd.DataFrame.to_parquet")
    @patch("src.data_pipeline.get_s3_client")
    def test_all_valid_rows_processed(self, mock_get_client, mock_to_parquet):
        mock_get_client.return_value = self._make_mock_client(VALID_CUSTOMERS_CSV)

        result = process_csv("my-bucket", "raw/customers.csv")

        assert result["processed"] == 6
        assert result["failed"] == 0

    @patch("src.data_pipeline.pd.DataFrame.to_parquet")
    @patch("src.data_pipeline.get_s3_client")
    def test_mixed_rows_counted_correctly(self, mock_get_client, mock_to_parquet):
        mock_get_client.return_value = self._make_mock_client(MIXED_CUSTOMERS_CSV)

        result = process_csv("my-bucket", "raw/customers.csv")

        assert result["processed"] == 1
        assert result["failed"] == 2

    @patch("src.data_pipeline.pd.DataFrame.to_parquet")
    @patch("src.data_pipeline.get_s3_client")
    def test_all_invalid_rows_produces_zero_processed(self, mock_get_client, mock_to_parquet):
        mock_get_client.return_value = self._make_mock_client(ONLY_INVALID_CSV)

        result = process_csv("my-bucket", "raw/customers.csv")

        assert result["processed"] == 0
        assert result["failed"] == 3

    @patch("src.data_pipeline.pd.DataFrame.to_parquet")
    @patch("src.data_pipeline.get_s3_client")
    def test_output_key_replaces_raw_with_processed_and_csv_with_parquet(
        self, mock_get_client, mock_to_parquet
    ):
        mock_get_client.return_value = self._make_mock_client(VALID_CUSTOMERS_CSV)

        result = process_csv("my-bucket", "raw/2024/customers.csv")

        assert result["output_key"] == "processed/2024/customers.parquet"

    @patch("src.data_pipeline.pd.DataFrame.to_parquet")
    @patch("src.data_pipeline.get_s3_client")
    def test_output_key_for_flat_raw_prefix(self, mock_get_client, mock_to_parquet):
        mock_get_client.return_value = self._make_mock_client(VALID_CUSTOMERS_CSV)

        result = process_csv("my-bucket", "raw/customers.csv")

        assert result["output_key"] == "processed/customers.parquet"

    @patch("src.data_pipeline.pd.DataFrame.to_parquet")
    @patch("src.data_pipeline.get_s3_client")
    def test_parquet_written_to_correct_s3_path(self, mock_get_client, mock_to_parquet):
        mock_get_client.return_value = self._make_mock_client(VALID_CUSTOMERS_CSV)

        process_csv("my-bucket", "raw/customers.csv")

        mock_to_parquet.assert_called_once_with("s3://my-bucket/processed/customers.parquet")

    @patch("src.data_pipeline.pd.DataFrame.to_parquet")
    @patch("src.data_pipeline.get_s3_client")
    def test_result_contains_timestamp(self, mock_get_client, mock_to_parquet):
        mock_get_client.return_value = self._make_mock_client(VALID_CUSTOMERS_CSV)

        result = process_csv("my-bucket", "raw/customers.csv")

        assert "timestamp" in result
        # Should be parseable ISO format
        datetime.fromisoformat(result["timestamp"])

    @patch("src.data_pipeline.pd.DataFrame.to_parquet")
    @patch("src.data_pipeline.get_s3_client")
    def test_result_contains_output_key(self, mock_get_client, mock_to_parquet):
        mock_get_client.return_value = self._make_mock_client(VALID_CUSTOMERS_CSV)

        result = process_csv("my-bucket", "raw/customers.csv")

        assert "output_key" in result

    @patch("src.data_pipeline.pd.DataFrame.to_parquet")
    @patch("src.data_pipeline.get_s3_client")
    def test_get_object_called_with_correct_args(self, mock_get_client, mock_to_parquet):
        mock_client = self._make_mock_client(VALID_CUSTOMERS_CSV)
        mock_get_client.return_value = mock_client

        process_csv("my-bucket", "raw/customers.csv")

        mock_client.get_object.assert_called_once_with(
            Bucket="my-bucket", Key="raw/customers.csv"
        )

    @patch("src.data_pipeline.pd.DataFrame.to_parquet")
    @patch("src.data_pipeline.get_s3_client")
    def test_s3_get_object_exception_propagates(self, mock_get_client, mock_to_parquet):
        mock_client = MagicMock()
        mock_client.get_object.side_effect = Exception("S3 access denied")
        mock_get_client.return_value = mock_client

        with pytest.raises(Exception, match="S3 access denied"):
            process_csv("my-bucket", "raw/customers.csv")

    @patch("src.data_pipeline.pd.DataFrame.to_parquet")
    @patch("src.data_pipeline.get_s3_client")
    def test_empty_csv_produces_zero_counts(self, mock_get_client, mock_to_parquet):
        empty_csv = "customer_id,email,age,country_code\n"
        mock_get_client.return_value = self._make_mock_client(empty_csv)

        result = process_csv("my-bucket", "raw/