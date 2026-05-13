"""
Test suite for src/data_pipeline.py

What is tested:
- validate_customer_record: happy path, missing fields, invalid email, age boundary values
- process_csv: successful processing, mixed valid/invalid rows, S3 interaction
- get_all_pending_files: listing CSV files, empty bucket, filtering non-CSV keys
- lambda_handler: success path, error path, missing key, bucket fallback to env var
- get_s3_client: client construction (smoke test)

Mocks used:
- unittest.mock.patch / MagicMock for boto3.client (all S3 calls)
- unittest.mock.patch for pandas DataFrame.to_parquet (avoids real S3 writes)
- io.BytesIO / io.StringIO to simulate S3 object bodies
- os.environ patched where LANDING_BUCKET is required

TODOs:
- TODO: Integration test against localstack once available
- TODO: Test pagination behaviour in get_all_pending_files (>1000 objects)
- TODO: Test concurrent / large-file performance characteristics
- TODO: Verify parquet schema / column types after transformation
- TODO: Test behaviour when s3://bucket/output_key write partially fails mid-stream
"""

import io
import os
import pytest
import logging
from datetime import datetime
from unittest.mock import MagicMock, patch, call

import pandas as pd

# ---------------------------------------------------------------------------
# Module under test
# ---------------------------------------------------------------------------
from src.data_pipeline import (
    validate_customer_record,
    process_csv,
    get_all_pending_files,
    lambda_handler,
    get_s3_client,
)


# ===========================================================================
# Fixtures
# ===========================================================================

@pytest.fixture()
def valid_record():
    """A fully valid customer record."""
    return {
        "customer_id": "CUST-001",
        "email": "alice.chen@example.com",
        "age": 34,
        "country_code": "GB",
        "segment": "enterprise",
        "annual_revenue": 250000,
    }


@pytest.fixture()
def minimal_valid_record():
    """Only the required fields present."""
    return {
        "customer_id": "CUST-MIN",
        "email": "min@example.com",
        "age": 1,
        "country_code": "US",
    }


SAMPLE_CSV_CONTENT = (
    "customer_id,email,age,country_code,segment,annual_revenue\n"
    "CUST-001,alice.chen@example.com,34,GB,enterprise,250000\n"
    "CUST-002,bob.smith@example.com,28,US,smb,45000\n"
    "CUST-003,carol.jones@example.com,52,SG,enterprise,500000\n"
    "CUST-004,david.lee@example.com,19,AU,consumer,0\n"
    "CUST-005,emma.wilson@example.com,41,DE,smb,78000\n"
    "CUST-006,frank.brown@example.com,67,US,enterprise,320000\n"
    "CUST-007,invalid-email,25,GB,consumer,0\n"
    "CUST-008,grace.kim@example.com,-1,KR,smb,55000\n"
)


def _make_s3_body(content: str):
    """Return a file-like object that mimics an S3 response Body."""
    return io.BytesIO(content.encode("utf-8"))


def _make_mock_s3_client(csv_content: str = SAMPLE_CSV_CONTENT):
    """Build a MagicMock that behaves like a minimal boto3 S3 client."""
    mock_client = MagicMock()
    mock_client.get_object.return_value = {"Body": _make_s3_body(csv_content)}
    mock_client.list_objects_v2.return_value = {
        "Contents": [
            {"Key": "raw/customers_2024.csv"},
            {"Key": "raw/accounts_2024.csv"},
            {"Key": "raw/archive/old_data.csv"},
            {"Key": "raw/notes.txt"},
        ]
    }
    return mock_client


# ===========================================================================
# validate_customer_record
# ===========================================================================

class TestValidateCustomerRecord:

    def test_valid_record_returns_true(self, valid_record):
        assert validate_customer_record(valid_record) is True

    def test_minimal_valid_record(self, minimal_valid_record):
        assert validate_customer_record(minimal_valid_record) is True

    # --- Missing required fields ---

    @pytest.mark.parametrize("missing_field", [
        "customer_id", "email", "age", "country_code"
    ])
    def test_missing_required_field_raises(self, valid_record, missing_field):
        del valid_record[missing_field]
        with pytest.raises(ValueError, match=f"Missing required field: {missing_field}"):
            validate_customer_record(valid_record)

    def test_empty_dict_raises_for_customer_id(self):
        with pytest.raises(ValueError, match="Missing required field: customer_id"):
            validate_customer_record({})

    # --- Email validation ---

    def test_invalid_email_no_at_sign(self, valid_record):
        valid_record["email"] = "invalid-email"
        with pytest.raises(ValueError, match="Invalid email"):
            validate_customer_record(valid_record)

    def test_email_with_at_sign_passes(self, valid_record):
        valid_record["email"] = "a@b"
        assert validate_customer_record(valid_record) is True

    def test_empty_string_email_raises(self, valid_record):
        valid_record["email"] = ""
        with pytest.raises(ValueError, match="Invalid email"):
            validate_customer_record(valid_record)

    # --- Age boundary values ---

    @pytest.mark.parametrize("age", [1, 75, 150])
    def test_age_within_valid_range(self, valid_record, age):
        valid_record["age"] = age
        assert validate_customer_record(valid_record) is True

    @pytest.mark.parametrize("age", [0, -1, -100, 151, 200, 9999])
    def test_age_out_of_range_raises(self, valid_record, age):
        valid_record["age"] = age
        with pytest.raises(ValueError, match="Age out of range"):
            validate_customer_record(valid_record)

    def test_age_boundary_exactly_150(self, valid_record):
        valid_record["age"] = 150
        assert validate_customer_record(valid_record) is True

    def test_age_boundary_exactly_1(self, valid_record):
        valid_record["age"] = 1
        assert validate_customer_record(valid_record) is True

    def test_age_boundary_151_fails(self, valid_record):
        valid_record["age"] = 151
        with pytest.raises(ValueError):
            validate_customer_record(valid_record)

    # --- Synthetic data samples ---

    @pytest.mark.parametrize("record,should_pass", [
        # CUST-001 through CUST-006: valid
        ({"customer_id": "CUST-001", "email": "alice.chen@example.com",  "age": 34, "country_code": "GB"}, True),
        ({"customer_id": "CUST-002", "email": "bob.smith@example.com",   "age": 28, "country_code": "US"}, True),
        ({"customer_id": "CUST-003", "email": "carol.jones@example.com", "age": 52, "country_code": "SG"}, True),
        ({"customer_id": "CUST-004", "email": "david.lee@example.com",   "age": 19, "country_code": "AU"}, True),
        ({"customer_id": "CUST-005", "email": "emma.wilson@example.com", "age": 41, "country_code": "DE"}, True),
        ({"customer_id": "CUST-006", "email": "frank.brown@example.com", "age": 67, "country_code": "US"}, True),
        # CUST-007: invalid email
        ({"customer_id": "CUST-007", "email": "invalid-email",           "age": 25, "country_code": "GB"}, False),
        # CUST-008: age -1
        ({"customer_id": "CUST-008", "email": "grace.kim@example.com",   "age": -1, "country_code": "KR"}, False),
    ])
    def test_synthetic_samples(self, record, should_pass):
        if should_pass:
            assert validate_customer_record(record) is True
        else:
            with pytest.raises(ValueError):
                validate_customer_record(record)


# ===========================================================================
# get_s3_client
# ===========================================================================

class TestGetS3Client:

    @patch("src.data_pipeline.boto3.client")
    def test_returns_boto3_client(self, mock_boto3_client):
        mock_boto3_client.return_value = MagicMock()
        client = get_s3_client()
        mock_boto3_client.assert_called_once_with(
            "s3",
            aws_access_key_id="AKIAIOSFODNN7EXAMPLE",
            aws_secret_access_key="wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
            region_name="us-east-1",
        )
        assert client is mock_boto3_client.return_value

    @patch("src.data_pipeline.boto3.client")
    def test_client_is_s3_service(self, mock_boto3_client):
        get_s3_client()
        args, _ = mock_boto3_client.call_args
        assert args[0] == "s3"


# ===========================================================================
# process_csv
# ===========================================================================

class TestProcessCsv:

    @patch("src.data_pipeline.get_s3_client")
    @patch("pandas.DataFrame.to_parquet")
    def test_happy_path_returns_summary(self, mock_to_parquet, mock_get_client):
        mock_get_client.return_value = _make_mock_s3_client()

        result = process_csv("my-bucket", "raw/customers_2024.csv")

        assert result["processed"] == 6   # CUST-001 to CUST-006
        assert result["failed"] == 2      # CUST-007 (bad email), CUST-008 (bad age)
        assert "output_key" in result
        assert "timestamp" in result

    @patch("src.data_pipeline.get_s3_client")
    @patch("pandas.DataFrame.to_parquet")
    def test_output_key_transforms_raw_to_processed_parquet(
        self, mock_to_parquet, mock_get_client
    ):
        mock_get_client.return_value = _make_mock_s3_client()
        result = process_csv("my-bucket", "raw/customers_2024.csv")
        assert result["output_key"] == "processed/customers_2024.parquet"

    @patch("src.data_pipeline.get_s3_client")
    @patch("pandas.DataFrame.to_parquet")
    def test_to_parquet_called_with_correct_s3_path(
        self, mock_to_parquet, mock_get_client
    ):
        mock_get_client.return_value = _make_mock_s3_client()
        process_csv("my-bucket", "raw/customers_2024.csv")
        mock_to_parquet.assert_called_once_with(
            "s3://my-bucket/processed/customers_2024.parquet"
        )

    @patch("src.data_pipeline.get_s3_client")
    @patch("pandas.DataFrame.to_parquet")
    def test_timestamp_is_iso_format(self, mock_to_parquet, mock_get_client):
        mock_get_client.return_value = _make_mock_s3_client()
        result = process_csv("my-bucket", "raw/customers_2024.csv")
        # Should not raise
        datetime.fromisoformat(result["timestamp"])

    @patch("src.data_pipeline.get_s3_client")
    @patch("pandas.DataFrame.to_parquet")
    def test_all_valid_rows_no_failures(self, mock_to_parquet, mock_get_client):
        all_valid_csv = (
            "customer_id,email,age,country_code\n"
            "CUST-001,alice.chen@example.com,34,GB\n"
            "CUST-002,bob.smith@example.com,28,US\n"
        )
        mock_get_client.return_value = _make_mock_s3_client(all_valid_csv)
        result = process_csv("bucket", "raw/file.csv")
        assert result["processed"] == 2
        assert result["failed"] == 0

    @patch("src.data_pipeline.get_s3_client")
    @patch("pandas.DataFrame.to_parquet")
    def test_all_invalid_rows(self, mock_to_parquet, mock_get_client):
        all_invalid_csv = (
            "customer_id,email,age,country_code\n"
            "CUST-007,invalid-email,25,GB\n"
            "CUST-008,grace.kim@example.com,-1,KR\n"
        )
        mock_get_client.return_value = _make_mock_s3_client(all_invalid_csv)
        result = process_csv("bucket", "raw/file.csv")
        assert result["processed"] == 0
        assert result["failed"] == 2

    @patch("src.data_pipeline.get_s3_client")
    @patch("pandas.DataFrame.to_parquet")
    def test_empty_csv_returns_zero_counts(self, mock_to_parquet, mock_get_client):
        empty_csv = "customer_id,email,age,country_code\n"
        mock_get_client.return_value = _make_mock_s3_client(empty_csv)
        result = process_csv("bucket", "raw/empty.csv")
        assert result["processed"] == 0
        assert result["failed"] == 0
        assert result["output_key"] == "processed/empty.parquet"

    @patch("src.data_pipeline.get_s3_client")
    def test_s3_get_object_error_propagates(self, mock_get_client):
        mock_client = MagicMock()
        mock_client.get_object.side_effect = Exception("S3 unavailable")
        mock_get_client.return_value = mock_client
        with pytest.raises(Exception, match="S3 unavailable"):
            process_csv("bucket", "raw/file.csv")

    @patch("src.data_pipeline.get_s3_client")
    @patch("pandas.DataFrame.to_parquet")
    def test_s3_get_object_called_with_correct_args(
        self, mock_to_parquet, mock_get_client
    ):
        mock_client = _make_mock_s3_client()
        mock_get_client.return_value = mock_client

        process_csv("test-bucket", "raw/data.csv")

        mock_client.get_object.assert_called_once_with(
            Bucket="test-bucket", Key="raw/data.csv"
        )

    @patch("src.data_pipeline.get_s3_client")
    @patch("pandas.DataFrame.to_parquet")
    def test_single_valid_row(self, mock_to_parquet, mock_get_client):
        single_