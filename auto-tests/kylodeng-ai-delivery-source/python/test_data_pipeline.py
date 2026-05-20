"""
Test suite for src/data_pipeline.py

What is tested:
- validate_customer_record: happy path, missing fields, invalid email, age boundaries
- process_csv: happy path with valid/invalid mixed rows, empty CSV, S3 interaction
- get_all_pending_files: happy path, empty bucket, mixed file types, pagination gap
- lambda_handler: success path, error path, missing key, bucket fallback to env var
- get_s3_client: client creation (boto3 patched)

Mocks used:
- unittest.mock.patch / MagicMock for boto3.client (S3 get_object, list_objects_v2)
- unittest.mock.patch for pandas DataFrame.to_parquet (avoids real S3 writes)
- io.BytesIO / io.StringIO for simulating S3 object bodies

TODOs:
- TODO: test actual parquet output schema once output bucket fixture is available
- TODO: test pagination behaviour in get_all_pending_files (>1000 objects) — needs
         paginator refactor in source first
- TODO: integration test for full lambda_handler with localstack
"""

import io
import os
import logging
import pytest
from unittest.mock import MagicMock, patch, call
from datetime import datetime

import pandas as pd

# ---------------------------------------------------------------------------
# Module under test
# ---------------------------------------------------------------------------
import src.data_pipeline as pipeline
from src.data_pipeline import (
    validate_customer_record,
    process_csv,
    get_all_pending_files,
    lambda_handler,
    get_s3_client,
)

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

VALID_RECORD = {
    "customer_id": "CUST-001",
    "email": "alice.chen@example.com",
    "age": 34,
    "country_code": "GB",
    "segment": "enterprise",
    "annual_revenue": 250000,
}

SAMPLE_CSV_CONTENT = (
    "customer_id,email,age,country_code,segment,annual_revenue\n"
    "CUST-001,alice.chen@example.com,34,GB,enterprise,250000\n"
    "CUST-002,bob.smith@example.com,28,US,smb,45000\n"
    "CUST-003,carol.jones@example.com,52,SG,enterprise,500000\n"
    "CUST-004,david.lee@example.com,19,AU,consumer,0\n"
    "CUST-005,emma.wilson@example.com,41,DE,smb,78000\n"
    "CUST-006,frank.brown@example.com,67,US,enterprise,320000\n"
    "CUST-007,invalid-email,25,GB,consumer,0\n"          # bad email
    "CUST-008,grace.kim@example.com,-1,KR,smb,55000\n"  # bad age
)

MIXED_VALID_INVALID_CSV = (
    "customer_id,email,age,country_code\n"
    "CUST-001,alice.chen@example.com,34,GB\n"   # valid
    "CUST-007,invalid-email,25,GB\n"             # invalid email
    "CUST-008,grace.kim@example.com,-1,KR\n"    # invalid age
)

ALL_VALID_CSV = (
    "customer_id,email,age,country_code\n"
    "CUST-001,alice.chen@example.com,34,GB\n"
    "CUST-002,bob.smith@example.com,28,US\n"
)

EMPTY_ROWS_CSV = "customer_id,email,age,country_code\n"


def _make_s3_body(csv_text: str):
    """Return a StreamingBody-compatible BytesIO from a CSV string."""
    return io.BytesIO(csv_text.encode("utf-8"))


@pytest.fixture()
def mock_s3_client():
    """Patch boto3.client and return the mock S3 client instance."""
    with patch("src.data_pipeline.boto3.client") as mock_boto:
        client = MagicMock()
        mock_boto.return_value = client
        yield client


# ---------------------------------------------------------------------------
# validate_customer_record
# ---------------------------------------------------------------------------

class TestValidateCustomerRecord:

    def test_valid_record_returns_true(self):
        assert validate_customer_record(VALID_RECORD) is True

    def test_valid_record_minimal_fields(self):
        record = {"customer_id": "X1", "email": "a@b.com", "age": 1, "country_code": "US"}
        assert validate_customer_record(record) is True

    # --- missing required fields ---

    @pytest.mark.parametrize("missing_field", [
        "customer_id", "email", "age", "country_code"
    ])
    def test_missing_required_field_raises(self, missing_field):
        record = VALID_RECORD.copy()
        del record[missing_field]
        with pytest.raises(ValueError, match=f"Missing required field: {missing_field}"):
            validate_customer_record(record)

    def test_empty_dict_raises_for_first_required_field(self):
        with pytest.raises(ValueError, match="Missing required field: customer_id"):
            validate_customer_record({})

    # --- email validation ---

    def test_invalid_email_no_at_sign_raises(self):
        record = VALID_RECORD.copy()
        record["email"] = "invalid-email"
        with pytest.raises(ValueError, match="Invalid email"):
            validate_customer_record(record)

    def test_email_with_at_sign_passes(self):
        record = VALID_RECORD.copy()
        record["email"] = "user@domain.org"
        assert validate_customer_record(record) is True

    def test_email_that_is_just_at_sign_passes(self):
        """Boundary: '@' alone satisfies the current simple check."""
        record = VALID_RECORD.copy()
        record["email"] = "@"
        assert validate_customer_record(record) is True

    # --- age boundary values ---

    @pytest.mark.parametrize("valid_age", [1, 75, 150])
    def test_age_within_range_passes(self, valid_age):
        record = VALID_RECORD.copy()
        record["age"] = valid_age
        assert validate_customer_record(record) is True

    @pytest.mark.parametrize("invalid_age", [0, -1, 151, 200, -999])
    def test_age_out_of_range_raises(self, invalid_age):
        record = VALID_RECORD.copy()
        record["age"] = invalid_age
        with pytest.raises(ValueError, match="Age out of range"):
            validate_customer_record(record)

    def test_age_boundary_zero_raises(self):
        record = VALID_RECORD.copy()
        record["age"] = 0
        with pytest.raises(ValueError):
            validate_customer_record(record)

    def test_age_boundary_151_raises(self):
        record = VALID_RECORD.copy()
        record["age"] = 151
        with pytest.raises(ValueError):
            validate_customer_record(record)

    # --- synthetic sample data ---

    @pytest.mark.parametrize("record,should_pass", [
        ({"customer_id": "CUST-001", "email": "alice.chen@example.com", "age": 34, "country_code": "GB"}, True),
        ({"customer_id": "CUST-002", "email": "bob.smith@example.com",  "age": 28, "country_code": "US"}, True),
        ({"customer_id": "CUST-007", "email": "invalid-email",           "age": 25, "country_code": "GB"}, False),
        ({"customer_id": "CUST-008", "email": "grace.kim@example.com",   "age": -1, "country_code": "KR"}, False),
    ])
    def test_synthetic_sample_records(self, record, should_pass):
        if should_pass:
            assert validate_customer_record(record) is True
        else:
            with pytest.raises(ValueError):
                validate_customer_record(record)


# ---------------------------------------------------------------------------
# get_s3_client
# ---------------------------------------------------------------------------

class TestGetS3Client:

    def test_returns_boto3_client(self):
        with patch("src.data_pipeline.boto3.client") as mock_boto:
            mock_instance = MagicMock()
            mock_boto.return_value = mock_instance
            result = get_s3_client()
            assert result is mock_instance

    def test_called_with_correct_service_and_region(self):
        with patch("src.data_pipeline.boto3.client") as mock_boto:
            get_s3_client()
            mock_boto.assert_called_once_with(
                's3',
                aws_access_key_id=pipeline.AWS_ACCESS_KEY,
                aws_secret_access_key=pipeline.AWS_SECRET_KEY,
                region_name="us-east-1",
            )


# ---------------------------------------------------------------------------
# process_csv
# ---------------------------------------------------------------------------

class TestProcessCsv:

    def _setup_s3_mock(self, mock_s3_client, csv_content: str):
        mock_s3_client.get_object.return_value = {
            "Body": _make_s3_body(csv_content)
        }

    def test_happy_path_all_valid_rows(self, mock_s3_client):
        self._setup_s3_mock(mock_s3_client, ALL_VALID_CSV)
        with patch.object(pd.DataFrame, "to_parquet"):
            result = process_csv("my-bucket", "raw/customers.csv")

        assert result["processed"] == 2
        assert result["failed"] == 0
        assert result["output_key"] == "processed/customers.parquet"
        assert "timestamp" in result

    def test_mixed_valid_and_invalid_rows(self, mock_s3_client):
        self._setup_s3_mock(mock_s3_client, MIXED_VALID_INVALID_CSV)
        with patch.object(pd.DataFrame, "to_parquet"):
            result = process_csv("my-bucket", "raw/mixed.csv")

        assert result["processed"] == 1
        assert result["failed"] == 2

    def test_all_invalid_rows(self, mock_s3_client):
        csv = (
            "customer_id,email,age,country_code\n"
            "CUST-007,invalid-email,25,GB\n"
            "CUST-008,grace.kim@example.com,-1,KR\n"
        )
        self._setup_s3_mock(mock_s3_client, csv)
        with patch.object(pd.DataFrame, "to_parquet"):
            result = process_csv("my-bucket", "raw/bad.csv")

        assert result["processed"] == 0
        assert result["failed"] == 2

    def test_empty_csv_no_data_rows(self, mock_s3_client):
        self._setup_s3_mock(mock_s3_client, EMPTY_ROWS_CSV)
        with patch.object(pd.DataFrame, "to_parquet"):
            result = process_csv("my-bucket", "raw/empty.csv")

        assert result["processed"] == 0
        assert result["failed"] == 0

    def test_output_key_replaces_raw_prefix_and_extension(self, mock_s3_client):
        self._setup_s3_mock(mock_s3_client, ALL_VALID_CSV)
        with patch.object(pd.DataFrame, "to_parquet"):
            result = process_csv("my-bucket", "raw/2024/01/customers.csv")

        assert result["output_key"] == "processed/2024/01/customers.parquet"
        assert result["output_key"].endswith(".parquet")

    def test_parquet_written_to_correct_s3_path(self, mock_s3_client):
        self._setup_s3_mock(mock_s3_client, ALL_VALID_CSV)
        with patch.object(pd.DataFrame, "to_parquet") as mock_to_parquet:
            process_csv("my-bucket", "raw/customers.csv")
            mock_to_parquet.assert_called_once_with(
                "s3://my-bucket/processed/customers.parquet"
            )

    def test_s3_get_object_called_with_correct_args(self, mock_s3_client):
        self._setup_s3_mock(mock_s3_client, ALL_VALID_CSV)
        with patch.object(pd.DataFrame, "to_parquet"):
            process_csv("test-bucket", "raw/file.csv")

        mock_s3_client.get_object.assert_called_once_with(
            Bucket="test-bucket", Key="raw/file.csv"
        )

    def test_result_contains_iso_timestamp(self, mock_s3_client):
        self._setup_s3_mock(mock_s3_client, ALL_VALID_CSV)
        before = datetime.utcnow().isoformat()
        with patch.object(pd.DataFrame, "to_parquet"):
            result = process_csv("my-bucket", "raw/customers.csv")
        after = datetime.utcnow().isoformat()

        ts = result["timestamp"]
        assert before <= ts <= after

    def test_s3_get_object_raises_propagates(self, mock_s3_client):
        mock_s3_client.get_object.side_effect = Exception("S3 unavailable")
        with pytest.raises(Exception, match="S3 unavailable"):
            process_csv("my-bucket", "raw/customers.csv")

    def test_full_sample_csv_counts(self, mock_s3_client):
        """Using the provided synthetic sample: 6 valid, 2 invalid (CUST-007 bad email,
        CUST-008 bad age). CUST-009 partial row — pandas may parse or skip."""
        self._setup_s3_mock(mock_s3_client, SAMPLE_CSV_CONTENT)
        with patch.object(pd.DataFrame, "to_parquet"):
            result = process_csv("my-bucket", "raw/customers.csv")

        # CUST-001 through CUST-006 are valid (6), CUST-007 and CUST-008 are invalid (2)
        assert result["processed"] == 6
        assert result["failed"] == 2


# ---------------------------------------------------------------------------
# get_all_pending_files
# ---------------------------------------------------------------------------

class TestGetAllPendingFiles:

    def test_returns_only_csv_keys(self, mock_s3_client):
        mock_s3_client.list_objects_v2.return_value = {
            "Contents": [
                {"Key": "raw/customers.csv"},
                {"Key": "raw/data.parquet"},
                {"Key": "raw/readme.txt"},
                {"Key": "raw/orders.csv"},
            ]
        }
        result = get_all_pending_files("my-bucket")
        assert result == ["raw/customers.csv", "raw/orders.csv"]

    def test_empty_bucket_returns_empty_list(self, mock_s3_client):
        mock_s3_client.list_objects_v2.return_value = {}
        result = get_all_pending_files("my-bucket")
        assert result == []

    def test_contents_key_missing_returns_empty_list(self, mock_s3_client):
        mock_s3_client.list_objects_v2.return_value = {"KeyCount": 0}
        result = get_all_pending_files("my-bucket")
        assert result == []

    def test_all_csv_files_returned(self, mock_s3_client):
        mock_s3_client.list_objects_v2.return