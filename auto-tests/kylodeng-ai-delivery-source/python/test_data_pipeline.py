"""
Test suite for src/data_pipeline.py

WHAT IS TESTED:
- get_s3_client(): verifies boto3 client is constructed with correct credentials/region
- validate_customer_record(): happy path, missing fields, invalid email, age boundary values
- process_csv(): full pipeline — download CSV from S3, validate rows, write parquet, return summary
- get_all_pending_files(): lists CSV files under raw/ prefix, handles empty bucket, pagination absence
- lambda_handler(): event routing, success response shape, error handling / 500 path

MOCKS USED:
- boto3.client (via unittest.mock.patch) — no real AWS calls are made
- pandas.DataFrame.to_parquet — patched to avoid fsspec/s3fs dependency at test time
- src.data_pipeline.get_s3_client — patched at module level for process_csv and get_all_pending_files

TODOs:
- TODO: test actual parquet bytes written to S3 once an s3fs/moto fixture is available
- TODO: test pagination in get_all_pending_files (NextContinuationToken path not implemented in source)
- TODO: test behaviour when LANDING_BUCKET env-var is absent AND event has no "bucket" key
- TODO: validate logging output (logger.info / logger.error) with caplog assertions
"""

import io
import os
import pytest
from datetime import datetime
from unittest.mock import MagicMock, patch, call

import pandas as pd

import src.data_pipeline as pipeline
from src.data_pipeline import (
    get_s3_client,
    validate_customer_record,
    process_csv,
    get_all_pending_files,
    lambda_handler,
    AWS_ACCESS_KEY,
    AWS_SECRET_KEY,
)


# ---------------------------------------------------------------------------
# Helpers / shared fixtures
# ---------------------------------------------------------------------------

VALID_RECORD = {
    "customer_id": "CUST-001",
    "email": "alice.chen@example.com",
    "age": 34,
    "country_code": "GB",
}

MINIMAL_CSV = (
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


def _make_s3_body(csv_text: str) -> MagicMock:
    """Return a mock whose 'Body' attribute behaves like a readable stream."""
    mock_body = MagicMock()
    mock_body.read.return_value = csv_text.encode()
    # pandas.read_csv accepts file-like objects; wrap in BytesIO
    mock_body.__iter__ = lambda self: iter(self.read().splitlines())
    body_stream = io.BytesIO(csv_text.encode())
    mock_obj = {"Body": body_stream}
    return mock_obj


# ---------------------------------------------------------------------------
# get_s3_client
# ---------------------------------------------------------------------------

class TestGetS3Client:
    @patch("src.data_pipeline.boto3.client")
    def test_returns_boto3_client(self, mock_boto_client):
        fake_client = MagicMock()
        mock_boto_client.return_value = fake_client

        result = get_s3_client()

        assert result is fake_client

    @patch("src.data_pipeline.boto3.client")
    def test_uses_hardcoded_credentials(self, mock_boto_client):
        get_s3_client()

        mock_boto_client.assert_called_once_with(
            "s3",
            aws_access_key_id=AWS_ACCESS_KEY,
            aws_secret_access_key=AWS_SECRET_KEY,
            region_name="us-east-1",
        )

    @patch("src.data_pipeline.boto3.client")
    def test_region_is_us_east_1(self, mock_boto_client):
        get_s3_client()
        _, kwargs = mock_boto_client.call_args
        assert kwargs["region_name"] == "us-east-1"


# ---------------------------------------------------------------------------
# validate_customer_record
# ---------------------------------------------------------------------------

class TestValidateCustomerRecord:

    # --- happy path ---

    def test_valid_record_returns_true(self):
        assert validate_customer_record(VALID_RECORD) is True

    @pytest.mark.parametrize("record", [
        {"customer_id": "CUST-001", "email": "alice.chen@example.com", "age": 34, "country_code": "GB"},
        {"customer_id": "CUST-002", "email": "bob.smith@example.com",  "age": 28, "country_code": "US"},
        {"customer_id": "CUST-003", "email": "carol.jones@example.com","age": 52, "country_code": "SG"},
        {"customer_id": "CUST-005", "email": "emma.wilson@example.com","age": 41, "country_code": "DE"},
        {"customer_id": "CUST-006", "email": "frank.brown@example.com","age": 67, "country_code": "US"},
    ])
    def test_valid_synthetic_records(self, record):
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

    def test_empty_dict_raises_on_first_required_field(self):
        with pytest.raises(ValueError, match="Missing required field: customer_id"):
            validate_customer_record({})

    # --- email validation ---

    def test_invalid_email_no_at_sign_raises(self):
        record = {**VALID_RECORD, "email": "invalid-email"}
        with pytest.raises(ValueError, match="Invalid email"):
            validate_customer_record(record)

    def test_invalid_email_from_synthetic_data_cust007(self):
        record = {
            "customer_id": "CUST-007",
            "email": "invalid-email",
            "age": 25,
            "country_code": "GB",
        }
        with pytest.raises(ValueError, match="Invalid email"):
            validate_customer_record(record)

    def test_email_with_at_sign_passes(self):
        record = {**VALID_RECORD, "email": "x@y"}
        assert validate_customer_record(record) is True

    def test_email_multiple_at_signs_passes(self):
        # The check is only "@ in email", so two @ signs still passes
        record = {**VALID_RECORD, "email": "a@@b.com"}
        assert validate_customer_record(record) is True

    # --- age boundary values ---

    @pytest.mark.parametrize("valid_age", [1, 2, 75, 149, 150])
    def test_age_within_range_passes(self, valid_age):
        record = {**VALID_RECORD, "age": valid_age}
        assert validate_customer_record(record) is True

    @pytest.mark.parametrize("invalid_age", [0, -1, 151, 200, -100])
    def test_age_out_of_range_raises(self, invalid_age):
        record = {**VALID_RECORD, "age": invalid_age}
        with pytest.raises(ValueError, match="Age out of range"):
            validate_customer_record(record)

    def test_negative_age_from_synthetic_data_cust008(self):
        record = {
            "customer_id": "CUST-008",
            "email": "grace.kim@example.com",
            "age": -1,
            "country_code": "KR",
        }
        with pytest.raises(ValueError, match="Age out of range"):
            validate_customer_record(record)

    def test_age_exactly_1_is_valid(self):
        assert validate_customer_record({**VALID_RECORD, "age": 1}) is True

    def test_age_exactly_150_is_valid(self):
        assert validate_customer_record({**VALID_RECORD, "age": 150}) is True

    def test_age_zero_is_invalid(self):
        with pytest.raises(ValueError, match="Age out of range"):
            validate_customer_record({**VALID_RECORD, "age": 0})

    def test_age_151_is_invalid(self):
        with pytest.raises(ValueError, match="Age out of range"):
            validate_customer_record({**VALID_RECORD, "age": 151})

    # --- extra fields do not break validation ---

    def test_extra_fields_in_record_are_ignored(self):
        record = {**VALID_RECORD, "segment": "enterprise", "annual_revenue": 250000}
        assert validate_customer_record(record) is True


# ---------------------------------------------------------------------------
# process_csv
# ---------------------------------------------------------------------------

class TestProcessCsv:

    def _make_mock_client(self, csv_text: str) -> MagicMock:
        mock_client = MagicMock()
        mock_client.get_object.return_value = _make_s3_body(csv_text)
        return mock_client

    @patch("src.data_pipeline.pd.DataFrame.to_parquet")
    @patch("src.data_pipeline.get_s3_client")
    def test_returns_correct_keys(self, mock_get_client, mock_to_parquet):
        mock_get_client.return_value = self._make_mock_client(MINIMAL_CSV)

        result = process_csv("my-bucket", "raw/customers.csv")

        assert "processed" in result
        assert "failed" in result
        assert "output_key" in result
        assert "timestamp" in result

    @patch("src.data_pipeline.pd.DataFrame.to_parquet")
    @patch("src.data_pipeline.get_s3_client")
    def test_valid_rows_count(self, mock_get_client, mock_to_parquet):
        """CUST-001..006 are valid; CUST-007 has bad email; CUST-008 has bad age."""
        mock_get_client.return_value = self._make_mock_client(MINIMAL_CSV)

        result = process_csv("my-bucket", "raw/customers.csv")

        assert result["processed"] == 6
        assert result["failed"] == 2

    @patch("src.data_pipeline.pd.DataFrame.to_parquet")
    @patch("src.data_pipeline.get_s3_client")
    def test_output_key_replaces_raw_with_processed(self, mock_get_client, mock_to_parquet):
        mock_get_client.return_value = self._make_mock_client(MINIMAL_CSV)

        result = process_csv("my-bucket", "raw/customers.csv")

        assert result["output_key"] == "processed/customers.parquet"

    @patch("src.data_pipeline.pd.DataFrame.to_parquet")
    @patch("src.data_pipeline.get_s3_client")
    def test_output_key_csv_replaced_with_parquet(self, mock_get_client, mock_to_parquet):
        mock_get_client.return_value = self._make_mock_client(MINIMAL_CSV)

        result = process_csv("my-bucket", "raw/sub/data.csv")

        assert result["output_key"].endswith(".parquet")
        assert ".csv" not in result["output_key"]

    @patch("src.data_pipeline.pd.DataFrame.to_parquet")
    @patch("src.data_pipeline.get_s3_client")
    def test_timestamp_is_iso_format(self, mock_get_client, mock_to_parquet):
        mock_get_client.return_value = self._make_mock_client(MINIMAL_CSV)

        result = process_csv("my-bucket", "raw/customers.csv")

        # Should parse without raising
        parsed = datetime.fromisoformat(result["timestamp"])
        assert isinstance(parsed, datetime)

    @patch("src.data_pipeline.pd.DataFrame.to_parquet")
    @patch("src.data_pipeline.get_s3_client")
    def test_to_parquet_called_with_correct_s3_path(self, mock_get_client, mock_to_parquet):
        mock_get_client.return_value = self._make_mock_client(MINIMAL_CSV)

        process_csv("my-bucket", "raw/customers.csv")

        mock_to_parquet.assert_called_once_with("s3://my-bucket/processed/customers.parquet")

    @patch("src.data_pipeline.pd.DataFrame.to_parquet")
    @patch("src.data_pipeline.get_s3_client")
    def test_all_valid_csv(self, mock_get_client, mock_to_parquet):
        all_valid_csv = (
            "customer_id,email,age,country_code\n"
            "CUST-001,alice@example.com,34,GB\n"
            "CUST-002,bob@example.com,28,US\n"
        )
        mock_get_client.return_value = self._make_mock_client(all_valid_csv)

        result = process_csv("bucket", "raw/all_valid.csv")

        assert result["processed"] == 2
        assert result["failed"] == 0

    @patch("src.data_pipeline.pd.DataFrame.to_parquet")
    @patch("src.data_pipeline.get_s3_client")
    def test_all_invalid_csv(self, mock_get_client, mock_to_parquet):
        all_invalid_csv = (
            "customer_id,email,age,country_code\n"
            "CUST-007,invalid-email,25,GB\n"
            "CUST-008,grace@example.com,-1,KR\n"
        )
        mock_get_client.return_value = self._make_mock_client(all_invalid_csv)

        result = process_csv("bucket", "raw/all_invalid.csv")

        assert result["processed"] == 0
        assert result["failed"] == 2

    @patch("src.data_pipeline.pd.DataFrame.to_parquet")
    @patch("src.data_pipeline.get_s3_client")
    def test_empty_csv_returns_zero_counts(self, mock_get_client, mock_to_parquet):
        empty_csv = "customer_id,email,age,country_code\n"
        mock_get_client.return_value = self._make_mock_client(empty_csv)

        result = process_csv("bucket", "raw/empty.csv")

        assert result["processed"] == 0
        assert result["failed"] == 0

    @patch("src.data_pipeline.pd.DataFrame.to_parquet")
    @patch("src.data_pipeline.get_s3_client")
    def test_s3_get_object_called_with_correct_args(self, mock_get_client, mock_to_parquet):
        mock_client = self._make