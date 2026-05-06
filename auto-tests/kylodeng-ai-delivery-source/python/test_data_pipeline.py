"""
Test suite for src/data_pipeline.py

What is tested:
    - validate_customer_record(): happy path, missing fields, invalid email, age boundary values
    - process_csv(): successful processing, mixed valid/invalid rows, S3 interaction, output key transformation
    - get_all_pending_files(): listing CSVs, filtering non-CSV files, empty bucket, missing Contents key
    - get_s3_client(): returns a boto3 client (smoke test)
    - lambda_handler(): success path, exception path, bucket from event vs env var

Mocks used:
    - unittest.mock.patch / MagicMock for boto3.client (all S3 calls)
    - unittest.mock.patch for pandas DataFrame.to_parquet (avoids real S3 write)
    - io.BytesIO / io.StringIO to simulate S3 object bodies

TODOs:
    - TODO: Integration test against localstack once available
    - TODO: Test pagination in get_all_pending_files (>1000 objects) — requires refactor first
    - TODO: Test actual parquet output contents once write path is refactored to return/accept a buffer
    - TODO: Test logger output (caplog) for lambda_handler info/error messages
"""

import io
import os
import pytest
from datetime import datetime
from unittest.mock import MagicMock, patch, PropertyMock

import pandas as pd

from src.data_pipeline import (
    validate_customer_record,
    process_csv,
    get_all_pending_files,
    get_s3_client,
    lambda_handler,
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


def _make_csv_body(rows: list[dict]) -> io.BytesIO:
    """Return a BytesIO object that mimics an S3 object Body for pd.read_csv."""
    df = pd.DataFrame(rows)
    buf = io.BytesIO()
    df.to_csv(buf, index=False)
    buf.seek(0)
    return buf


def _make_s3_mock(csv_body: io.BytesIO) -> MagicMock:
    """Return a mock S3 client whose get_object returns *csv_body*."""
    mock_client = MagicMock()
    mock_client.get_object.return_value = {"Body": csv_body}
    return mock_client


# ---------------------------------------------------------------------------
# validate_customer_record
# ---------------------------------------------------------------------------

class TestValidateCustomerRecord:

    def test_happy_path_returns_true(self):
        assert validate_customer_record(VALID_RECORD) is True

    def test_extra_fields_are_ignored(self):
        record = {**VALID_RECORD, "segment": "enterprise", "annual_revenue": 250_000}
        assert validate_customer_record(record) is True

    # --- missing required fields ---

    @pytest.mark.parametrize("missing_field", ["customer_id", "email", "age", "country_code"])
    def test_missing_required_field_raises(self, missing_field):
        record = {k: v for k, v in VALID_RECORD.items() if k != missing_field}
        with pytest.raises(ValueError, match=f"Missing required field: {missing_field}"):
            validate_customer_record(record)

    def test_all_fields_missing_raises_on_first(self):
        with pytest.raises(ValueError, match="Missing required field:"):
            validate_customer_record({})

    # --- email validation ---

    def test_invalid_email_no_at_sign_raises(self):
        record = {**VALID_RECORD, "email": "invalid-email"}
        with pytest.raises(ValueError, match="Invalid email"):
            validate_customer_record(record)

    def test_email_with_at_sign_is_accepted(self):
        record = {**VALID_RECORD, "email": "x@y"}
        assert validate_customer_record(record) is True

    def test_empty_email_raises(self):
        record = {**VALID_RECORD, "email": ""}
        with pytest.raises(ValueError, match="Invalid email"):
            validate_customer_record(record)

    # --- age boundary values ---

    @pytest.mark.parametrize("valid_age", [1, 2, 75, 149, 150])
    def test_age_within_valid_range(self, valid_age):
        record = {**VALID_RECORD, "age": valid_age}
        assert validate_customer_record(record) is True

    @pytest.mark.parametrize("invalid_age", [0, -1, 151, 200, -999])
    def test_age_out_of_range_raises(self, invalid_age):
        record = {**VALID_RECORD, "age": invalid_age}
        with pytest.raises(ValueError, match="Age out of range"):
            validate_customer_record(record)

    def test_age_exactly_one_is_valid(self):
        assert validate_customer_record({**VALID_RECORD, "age": 1}) is True

    def test_age_exactly_150_is_valid(self):
        assert validate_customer_record({**VALID_RECORD, "age": 150}) is True

    def test_age_zero_is_invalid(self):
        with pytest.raises(ValueError):
            validate_customer_record({**VALID_RECORD, "age": 0})

    # --- synthetic data rows ---

    @pytest.mark.parametrize("record", [
        {"customer_id": "CUST-001", "email": "alice.chen@example.com", "age": 34, "country_code": "GB"},
        {"customer_id": "CUST-002", "email": "bob.smith@example.com",  "age": 28, "country_code": "US"},
        {"customer_id": "CUST-003", "email": "carol.jones@example.com","age": 52, "country_code": "SG"},
        {"customer_id": "CUST-004", "email": "david.lee@example.com",  "age": 19, "country_code": "AU"},
        {"customer_id": "CUST-005", "email": "emma.wilson@example.com","age": 41, "country_code": "DE"},
        {"customer_id": "CUST-006", "email": "frank.brown@example.com","age": 67, "country_code": "US"},
    ])
    def test_synthetic_valid_records(self, record):
        assert validate_customer_record(record) is True

    def test_synthetic_invalid_email_cust007(self):
        record = {"customer_id": "CUST-007", "email": "invalid-email", "age": 25, "country_code": "GB"}
        with pytest.raises(ValueError, match="Invalid email"):
            validate_customer_record(record)

    def test_synthetic_negative_age_cust008(self):
        record = {"customer_id": "CUST-008", "email": "grace.kim@example.com", "age": -1, "country_code": "KR"}
        with pytest.raises(ValueError, match="Age out of range"):
            validate_customer_record(record)


# ---------------------------------------------------------------------------
# get_s3_client
# ---------------------------------------------------------------------------

class TestGetS3Client:

    @patch("src.data_pipeline.boto3.client")
    def test_returns_boto3_client(self, mock_boto3_client):
        mock_instance = MagicMock()
        mock_boto3_client.return_value = mock_instance

        result = get_s3_client()

        mock_boto3_client.assert_called_once_with(
            "s3",
            aws_access_key_id="AKIAIOSFODNN7EXAMPLE",
            aws_secret_access_key="wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
            region_name="us-east-1",
        )
        assert result is mock_instance

    @patch("src.data_pipeline.boto3.client")
    def test_called_each_invocation(self, mock_boto3_client):
        get_s3_client()
        get_s3_client()
        assert mock_boto3_client.call_count == 2


# ---------------------------------------------------------------------------
# process_csv
# ---------------------------------------------------------------------------

class TestProcessCsv:

    VALID_ROWS = [
        {"customer_id": "CUST-001", "email": "alice.chen@example.com",  "age": 34, "country_code": "GB"},
        {"customer_id": "CUST-002", "email": "bob.smith@example.com",   "age": 28, "country_code": "US"},
        {"customer_id": "CUST-003", "email": "carol.jones@example.com", "age": 52, "country_code": "SG"},
    ]

    MIXED_ROWS = [
        {"customer_id": "CUST-001", "email": "alice.chen@example.com", "age": 34,  "country_code": "GB"},
        {"customer_id": "CUST-007", "email": "invalid-email",          "age": 25,  "country_code": "GB"},
        {"customer_id": "CUST-008", "email": "grace.kim@example.com",  "age": -1,  "country_code": "KR"},
    ]

    def _patch_pipeline(self, mock_client, monkeypatch=None):
        """Helper: patch get_s3_client and to_parquet together."""
        patcher_client = patch("src.data_pipeline.get_s3_client", return_value=mock_client)
        patcher_parquet = patch("pandas.DataFrame.to_parquet")
        return patcher_client, patcher_parquet

    # --- happy path: all rows valid ---

    @patch("pandas.DataFrame.to_parquet")
    @patch("src.data_pipeline.get_s3_client")
    def test_all_valid_rows_processed(self, mock_get_client, mock_to_parquet):
        body = _make_csv_body(self.VALID_ROWS)
        mock_get_client.return_value = _make_s3_mock(body)

        result = process_csv("my-bucket", "raw/customers.csv")

        assert result["processed"] == 3
        assert result["failed"] == 0

    @patch("pandas.DataFrame.to_parquet")
    @patch("src.data_pipeline.get_s3_client")
    def test_output_key_transformation(self, mock_get_client, mock_to_parquet):
        body = _make_csv_body(self.VALID_ROWS)
        mock_get_client.return_value = _make_s3_mock(body)

        result = process_csv("my-bucket", "raw/customers.csv")

        assert result["output_key"] == "processed/customers.parquet"

    @patch("pandas.DataFrame.to_parquet")
    @patch("src.data_pipeline.get_s3_client")
    def test_output_key_nested_path(self, mock_get_client, mock_to_parquet):
        body = _make_csv_body(self.VALID_ROWS)
        mock_get_client.return_value = _make_s3_mock(body)

        result = process_csv("my-bucket", "raw/2024/01/customers.csv")

        assert result["output_key"] == "processed/2024/01/customers.parquet"

    @patch("pandas.DataFrame.to_parquet")
    @patch("src.data_pipeline.get_s3_client")
    def test_to_parquet_called_with_correct_s3_path(self, mock_get_client, mock_to_parquet):
        body = _make_csv_body(self.VALID_ROWS)
        mock_get_client.return_value = _make_s3_mock(body)

        process_csv("my-bucket", "raw/customers.csv")

        mock_to_parquet.assert_called_once_with("s3://my-bucket/processed/customers.parquet")

    @patch("pandas.DataFrame.to_parquet")
    @patch("src.data_pipeline.get_s3_client")
    def test_result_contains_timestamp(self, mock_get_client, mock_to_parquet):
        body = _make_csv_body(self.VALID_ROWS)
        mock_get_client.return_value = _make_s3_mock(body)

        result = process_csv("my-bucket", "raw/customers.csv")

        assert "timestamp" in result
        # Should be parseable as an ISO datetime
        datetime.fromisoformat(result["timestamp"])

    # --- mixed valid / invalid rows ---

    @patch("pandas.DataFrame.to_parquet")
    @patch("src.data_pipeline.get_s3_client")
    def test_mixed_rows_counts(self, mock_get_client, mock_to_parquet):
        body = _make_csv_body(self.MIXED_ROWS)
        mock_get_client.return_value = _make_s3_mock(body)

        result = process_csv("my-bucket", "raw/customers.csv")

        assert result["processed"] == 1
        assert result["failed"] == 2

    # --- all rows invalid ---

    @patch("pandas.DataFrame.to_parquet")
    @patch("src.data_pipeline.get_s3_client")
    def test_all_invalid_rows(self, mock_get_client, mock_to_parquet):
        rows = [
            {"customer_id": "CUST-007", "email": "invalid-email", "age": 25, "country_code": "GB"},
            {"customer_id": "CUST-008", "email": "grace.kim@example.com", "age": -1, "country_code": "KR"},
        ]
        body = _make_csv_body(rows)
        mock_get_client.return_value = _make_s3_mock(body)

        result = process_csv("my-bucket", "raw/customers.csv")

        assert result["processed"] == 0
        assert result["failed"] == 2

    # --- empty CSV ---

    @patch("pandas.DataFrame.to_parquet")
    @patch("src.data_pipeline.get_s3_client")
    def test_empty_csv(self, mock_get_client, mock_to_parquet):
        """CSV with headers but no data rows should return zeros."""
        empty_buf = io.BytesIO(
            b"customer_id,email,age,country_code\n"
        )
        mock_client = MagicMock()
        mock_client.get_object.return_value = {"Body": empty_buf}
        mock_get_client.return_value = mock_client

        result = process_csv("my-bucket", "raw/empty.csv")

        assert result["processed"] == 0
        assert result["failed"] == 0

    # --- S3 get_object failure ---

    @patch("src.data_pipeline.get_s3_client")
    def test_s3_get_object_raises(self, mock_get_client):
        mock_client = MagicMock()
        mock_client.get_object.side_effect = Exception("NoSuchKey")
        mock_get_client.return_value = mock_client

        with pytest.raises(Exception, match="NoSuchKey"):
            process_csv("my-bucket", "raw/missing.csv")

    # --- get_object is called with correct args ---

    @patch("pandas.DataFrame.to_parquet")
    @patch("src.data_pipeline.get_s3_client")
    def test_get_object_called_correctly(self, mock_get_client, mock_to_parquet):
        body = _make_csv_body(self.VALID_ROWS)
        mock_client = _make_s3_mock(body)
        mock_get_client.return_value = mock