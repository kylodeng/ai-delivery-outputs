"""
Test suite for src/data_pipeline.py

What is tested:
    - validate_customer_record(): happy path, missing fields, invalid email, age boundary values
    - process_csv(): S3 download, CSV parsing, validation routing, parquet upload, return dict
    - get_all_pending_files(): S3 list objects, prefix filtering, pagination absence, empty bucket
    - lambda_handler(): event routing, success path, error path, missing bucket fallback

Mocks used:
    - boto3.client (patched via unittest.mock.patch) — no real AWS calls made
    - pandas DataFrame.to_parquet — patched to avoid real S3 / filesystem writes
    - io.BytesIO / CSV body returned from mocked get_object

TODOs:
    - TODO: test behaviour when result_df.to_parquet raises an S3 permission error
    - TODO: test pagination in get_all_pending_files (>1000 objects) once implemented
    - TODO: test secrets manager integration once AWS_ACCESS_KEY/SECRET moved
    - TODO: test concurrent / large-file performance characteristics
    - TODO: test behaviour for partially-corrupt parquet output on disk
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

# ---------------------------------------------------------------------------
# Shared helpers / fixtures
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
    "CUST-007,invalid-email,25,GB,consumer,0\n"
    "CUST-008,grace.kim@example.com,-1,KR,smb,55000\n"
)


def _make_s3_body(csv_text: str):
    """Return a StreamingBody-like object for mocked get_object responses."""
    return io.BytesIO(csv_text.encode("utf-8"))


@pytest.fixture()
def mock_s3_client():
    """Patch boto3.client and return the mock S3 client instance."""
    with patch("data_pipeline.boto3.client") as mock_boto:
        client = MagicMock()
        mock_boto.return_value = client
        yield client


# ===========================================================================
# get_s3_client
# ===========================================================================

class TestGetS3Client:
    def test_returns_boto3_client(self):
        with patch("data_pipeline.boto3.client") as mock_boto:
            mock_boto.return_value = MagicMock()
            client = get_s3_client()
            mock_boto.assert_called_once_with(
                "s3",
                aws_access_key_id=data_pipeline.AWS_ACCESS_KEY,
                aws_secret_access_key=data_pipeline.AWS_SECRET_KEY,
                region_name="us-east-1",
            )
            assert client is mock_boto.return_value

    def test_credentials_are_hardcoded_placeholder(self):
        # Documents the known issue — credentials are hardcoded.
        assert data_pipeline.AWS_ACCESS_KEY == "AKIAIOSFODNN7EXAMPLE"
        assert "EXAMPLE" in data_pipeline.AWS_SECRET_KEY


# ===========================================================================
# validate_customer_record
# ===========================================================================

class TestValidateCustomerRecord:

    # --- Happy path ---------------------------------------------------------

    def test_valid_record_returns_true(self):
        assert validate_customer_record(VALID_RECORD) is True

    @pytest.mark.parametrize("record", [
        {**VALID_RECORD, "age": 34, "email": "alice.chen@example.com"},
        {**VALID_RECORD, "age": 28, "email": "bob.smith@example.com"},
        {**VALID_RECORD, "age": 52, "email": "carol.jones@example.com"},
        {**VALID_RECORD, "age": 19, "email": "david.lee@example.com"},
        {**VALID_RECORD, "age": 41, "email": "emma.wilson@example.com"},
        {**VALID_RECORD, "age": 67, "email": "frank.brown@example.com"},
    ])
    def test_valid_synthetic_records(self, record):
        assert validate_customer_record(record) is True

    # --- Missing required fields --------------------------------------------

    @pytest.mark.parametrize("missing_field", [
        "customer_id", "email", "age", "country_code"
    ])
    def test_missing_required_field_raises_value_error(self, missing_field):
        record = {k: v for k, v in VALID_RECORD.items() if k != missing_field}
        with pytest.raises(ValueError, match=f"Missing required field: {missing_field}"):
            validate_customer_record(record)

    def test_empty_record_raises_value_error(self):
        with pytest.raises(ValueError, match="Missing required field"):
            validate_customer_record({})

    # --- Email validation ---------------------------------------------------

    def test_invalid_email_no_at_sign_raises(self):
        record = {**VALID_RECORD, "email": "invalid-email"}
        with pytest.raises(ValueError, match="Invalid email"):
            validate_customer_record(record)

    def test_invalid_email_from_synthetic_data(self):
        """CUST-007 has 'invalid-email' as email."""
        record = {**VALID_RECORD, "customer_id": "CUST-007", "email": "invalid-email"}
        with pytest.raises(ValueError, match="Invalid email"):
            validate_customer_record(record)

    def test_empty_email_raises(self):
        record = {**VALID_RECORD, "email": ""}
        with pytest.raises(ValueError, match="Invalid email"):
            validate_customer_record(record)

    def test_email_with_at_sign_is_accepted(self):
        record = {**VALID_RECORD, "email": "@"}
        # Minimal @ present — pipeline accepts it (validation is intentionally basic)
        assert validate_customer_record(record) is True

    # --- Age boundary values ------------------------------------------------

    @pytest.mark.parametrize("age", [1, 2, 75, 149, 150])
    def test_age_within_valid_range(self, age):
        record = {**VALID_RECORD, "age": age}
        assert validate_customer_record(record) is True

    @pytest.mark.parametrize("age", [0, -1, 151, 200, -100])
    def test_age_out_of_range_raises(self, age):
        record = {**VALID_RECORD, "age": age}
        with pytest.raises(ValueError, match="Age out of range"):
            validate_customer_record(record)

    def test_age_minus_one_from_synthetic_data(self):
        """CUST-008 has age=-1."""
        record = {**VALID_RECORD, "customer_id": "CUST-008", "age": -1}
        with pytest.raises(ValueError, match="Age out of range"):
            validate_customer_record(record)

    def test_age_boundary_zero(self):
        record = {**VALID_RECORD, "age": 0}
        with pytest.raises(ValueError, match="Age out of range"):
            validate_customer_record(record)

    def test_age_boundary_151(self):
        record = {**VALID_RECORD, "age": 151}
        with pytest.raises(ValueError, match="Age out of range"):
            validate_customer_record(record)

    # --- Extra fields are tolerated -----------------------------------------

    def test_extra_fields_do_not_cause_failure(self):
        record = {**VALID_RECORD, "segment": "enterprise", "annual_revenue": 500000}
        assert validate_customer_record(record) is True


# ===========================================================================
# process_csv
# ===========================================================================

class TestProcessCsv:

    def _setup_mock_client(self, mock_s3_client, csv_text=SAMPLE_CSV_CONTENT):
        mock_s3_client.get_object.return_value = {"Body": _make_s3_body(csv_text)}

    @patch("data_pipeline.pd.DataFrame.to_parquet")
    def test_returns_dict_with_expected_keys(self, mock_parquet, mock_s3_client):
        self._setup_mock_client(mock_s3_client)
        result = process_csv("my-bucket", "raw/customers.csv")
        assert set(result.keys()) == {"processed", "failed", "output_key", "timestamp"}

    @patch("data_pipeline.pd.DataFrame.to_parquet")
    def test_valid_rows_counted_correctly(self, mock_parquet, mock_s3_client):
        self._setup_mock_client(mock_s3_client)
        result = process_csv("my-bucket", "raw/customers.csv")
        # 6 valid rows (CUST-001..006), 2 invalid (CUST-007 bad email, CUST-008 age=-1)
        assert result["processed"] == 6
        assert result["failed"] == 2

    @patch("data_pipeline.pd.DataFrame.to_parquet")
    def test_output_key_replaces_raw_with_processed_and_csv_with_parquet(
        self, mock_parquet, mock_s3_client
    ):
        self._setup_mock_client(mock_s3_client)
        result = process_csv("my-bucket", "raw/customers.csv")
        assert result["output_key"] == "processed/customers.parquet"

    @patch("data_pipeline.pd.DataFrame.to_parquet")
    def test_output_key_nested_path(self, mock_parquet, mock_s3_client):
        self._setup_mock_client(mock_s3_client)
        result = process_csv("my-bucket", "raw/2024/01/customers.csv")
        assert result["output_key"] == "processed/2024/01/customers.parquet"

    @patch("data_pipeline.pd.DataFrame.to_parquet")
    def test_timestamp_is_iso_format(self, mock_parquet, mock_s3_client):
        self._setup_mock_client(mock_s3_client)
        result = process_csv("my-bucket", "raw/customers.csv")
        # Should parse without error
        datetime.fromisoformat(result["timestamp"])

    @patch("data_pipeline.pd.DataFrame.to_parquet")
    def test_s3_get_object_called_with_correct_args(self, mock_parquet, mock_s3_client):
        self._setup_mock_client(mock_s3_client)
        process_csv("my-bucket", "raw/customers.csv")
        mock_s3_client.get_object.assert_called_once_with(
            Bucket="my-bucket", Key="raw/customers.csv"
        )

    @patch("data_pipeline.pd.DataFrame.to_parquet")
    def test_to_parquet_called_with_correct_s3_path(self, mock_parquet, mock_s3_client):
        self._setup_mock_client(mock_s3_client)
        process_csv("my-bucket", "raw/customers.csv")
        mock_parquet.assert_called_once_with(
            "s3://my-bucket/processed/customers.parquet"
        )

    @patch("data_pipeline.pd.DataFrame.to_parquet")
    def test_all_valid_csv_produces_zero_failures(self, mock_parquet, mock_s3_client):
        csv = (
            "customer_id,email,age,country_code\n"
            "CUST-001,alice@example.com,34,GB\n"
            "CUST-002,bob@example.com,28,US\n"
        )
        self._setup_mock_client(mock_s3_client, csv)
        result = process_csv("bucket", "raw/good.csv")
        assert result["processed"] == 2
        assert result["failed"] == 0

    @patch("data_pipeline.pd.DataFrame.to_parquet")
    def test_all_invalid_csv_produces_zero_processed(self, mock_parquet, mock_s3_client):
        csv = (
            "customer_id,email,age,country_code\n"
            "CUST-007,invalid-email,25,GB\n"
            "CUST-008,grace@example.com,-1,KR\n"
        )
        self._setup_mock_client(mock_s3_client, csv)
        result = process_csv("bucket", "raw/all_bad.csv")
        assert result["processed"] == 0
        assert result["failed"] == 2

    @patch("data_pipeline.pd.DataFrame.to_parquet")
    def test_empty_csv_produces_zero_counts(self, mock_parquet, mock_s3_client):
        csv = "customer_id,email,age,country_code\n"
        self._setup_mock_client(mock_s3_client, csv)
        result = process_csv("bucket", "raw/empty.csv")
        assert result["processed"] == 0
        assert result["failed"] == 0

    def test_s3_get_object_raises_propagates(self, mock_s3_client):
        mock_s3_client.get_object.side_effect = Exception("S3 unavailable")
        with pytest.raises(Exception, match="S3 unavailable"):
            process_csv("bucket", "raw/customers.csv")

    @patch("data_pipeline.pd.DataFrame.to_parquet")
    def test_missing_required_column_in_csv(self, mock_parquet, mock_s3_client):
        """CSV missing 'age' column — every row should fail validation."""
        csv = (
            "customer_id,email,country_code\n"
            "CUST-001,alice@example.com,GB\n"
        )
        self._setup_mock_client(mock_s3_client, csv)
        result = process_csv("bucket", "raw/no_age.csv")
        assert result["processed"] == 0
        assert result["failed"] == 1

    @pytest.mark.skip(reason="TODO: test to_parquet S3 permission error once error handling added")
    def test_to_parquet_s3_permission_error(self, mock_s3_client):
        pass  # TODO: test behaviour when result_df.to_parquet raises PermissionError

    @patch("data_pipeline.pd.DataFrame.to_parquet")
    def test_single_valid_record(self, mock_parquet, mock_s3_client):
        csv = (
            "customer_id,email,age,country_code\n"
            "CUST-001,alice@example.