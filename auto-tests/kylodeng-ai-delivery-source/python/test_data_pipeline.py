"""
Test suite for src/data_pipeline.py

What is tested:
    - validate_customer_record: happy path, missing fields, invalid email, age boundary values
    - process_csv: successful processing, mixed valid/invalid rows, empty CSV, S3 interaction
    - get_all_pending_files: normal listing, empty bucket, filtering of non-CSV keys
    - lambda_handler: successful invocation, error handling, missing key, env-var bucket fallback
    - get_s3_client: returns a boto3 client (smoke test)

Mocks used:
    - unittest.mock.patch / MagicMock for boto3.client (S3 get_object, list_objects_v2)
    - io.BytesIO to simulate S3 object bodies
    - unittest.mock.patch for pandas DataFrame.to_parquet (avoids real S3 writes)
    - unittest.mock.patch for os.environ

TODOs:
    - TODO: Integration test against a localstack/moto environment for full end-to-end S3 round-trip
    - TODO: Test parquet output schema/content once real S3 writes are mocked at a deeper level
    - TODO: Test logging side-effects (logger.info / logger.error) if log monitoring is required
    - TODO: Add tests for pagination once get_all_pending_files implements it
"""

import io
import os
import pytest
import pandas as pd
from unittest.mock import MagicMock, patch, call

# ---------------------------------------------------------------------------
# Module under test
# ---------------------------------------------------------------------------
import src.data_pipeline as dp
from src.data_pipeline import (
    validate_customer_record,
    process_csv,
    get_all_pending_files,
    lambda_handler,
    get_s3_client,
)


# ===========================================================================
# Fixtures & helpers
# ===========================================================================

def _make_csv_bytes(rows: list[dict]) -> bytes:
    """Serialise a list of dicts to CSV bytes (simulates an S3 object body)."""
    df = pd.DataFrame(rows)
    buf = io.BytesIO()
    df.to_csv(buf, index=False)
    buf.seek(0)
    return buf.read()


VALID_RECORD = {
    "customer_id": "CUST-001",
    "email": "alice.chen@example.com",
    "age": 34,
    "country_code": "GB",
    "segment": "enterprise",
    "annual_revenue": 250000,
}

SYNTHETIC_VALID_RECORDS = [
    {"customer_id": "CUST-001", "email": "alice.chen@example.com",  "age": 34, "country_code": "GB", "segment": "enterprise", "annual_revenue": 250000},
    {"customer_id": "CUST-002", "email": "bob.smith@example.com",   "age": 28, "country_code": "US", "segment": "smb",        "annual_revenue": 45000},
    {"customer_id": "CUST-003", "email": "carol.jones@example.com", "age": 52, "country_code": "SG", "segment": "enterprise", "annual_revenue": 500000},
    {"customer_id": "CUST-004", "email": "david.lee@example.com",   "age": 19, "country_code": "AU", "segment": "consumer",   "annual_revenue": 0},
    {"customer_id": "CUST-005", "email": "emma.wilson@example.com", "age": 41, "country_code": "DE", "segment": "smb",        "annual_revenue": 78000},
    {"customer_id": "CUST-006", "email": "frank.brown@example.com", "age": 67, "country_code": "US", "segment": "enterprise", "annual_revenue": 320000},
]

SYNTHETIC_INVALID_RECORDS = [
    {"customer_id": "CUST-007", "email": "invalid-email", "age": 25,  "country_code": "GB", "segment": "consumer", "annual_revenue": 0},
    {"customer_id": "CUST-008", "email": "grace.kim@example.com", "age": -1, "country_code": "KR", "segment": "smb", "annual_revenue": 55000},
]


@pytest.fixture
def mock_s3_client():
    """Return a MagicMock that impersonates a boto3 S3 client."""
    return MagicMock()


@pytest.fixture
def patch_get_s3_client(mock_s3_client):
    """Patch get_s3_client so no real AWS calls are made."""
    with patch.object(dp, "get_s3_client", return_value=mock_s3_client):
        yield mock_s3_client


@pytest.fixture
def patch_to_parquet():
    """Suppress actual parquet writes."""
    with patch.object(pd.DataFrame, "to_parquet") as mock_pq:
        yield mock_pq


# ===========================================================================
# get_s3_client
# ===========================================================================

class TestGetS3Client:
    @patch("src.data_pipeline.boto3.client")
    def test_returns_boto3_client(self, mock_boto_client):
        """Smoke-test: get_s3_client calls boto3.client with expected args."""
        fake_client = MagicMock()
        mock_boto_client.return_value = fake_client

        result = get_s3_client()

        mock_boto_client.assert_called_once_with(
            "s3",
            aws_access_key_id=dp.AWS_ACCESS_KEY,
            aws_secret_access_key=dp.AWS_SECRET_KEY,
            region_name="us-east-1",
        )
        assert result is fake_client

    @patch("src.data_pipeline.boto3.client")
    def test_hardcoded_credentials_present(self, mock_boto_client):
        """Confirm the (insecure) hardcoded keys are still wired in — so we notice if they change."""
        get_s3_client()
        _, kwargs = mock_boto_client.call_args
        assert kwargs["aws_access_key_id"] == "AKIAIOSFODNN7EXAMPLE"
        assert "aws_secret_access_key" in kwargs


# ===========================================================================
# validate_customer_record
# ===========================================================================

class TestValidateCustomerRecord:

    # --- happy path ---

    def test_valid_record_returns_true(self):
        assert validate_customer_record(VALID_RECORD.copy()) is True

    @pytest.mark.parametrize("record", SYNTHETIC_VALID_RECORDS)
    def test_all_synthetic_valid_records_pass(self, record):
        assert validate_customer_record(record.copy()) is True

    # --- age boundary values ---

    @pytest.mark.parametrize("age", [1, 2, 75, 149, 150])
    def test_valid_age_boundaries(self, age):
        rec = VALID_RECORD.copy()
        rec["age"] = age
        assert validate_customer_record(rec) is True

    @pytest.mark.parametrize("age", [0, -1, 151, 200, -100])
    def test_invalid_age_raises(self, age):
        rec = VALID_RECORD.copy()
        rec["age"] = age
        with pytest.raises(ValueError, match="Age out of range"):
            validate_customer_record(rec)

    # CUST-008 from synthetic data: age -1
    def test_synthetic_negative_age_raises(self):
        rec = SYNTHETIC_INVALID_RECORDS[1].copy()
        with pytest.raises(ValueError, match="Age out of range"):
            validate_customer_record(rec)

    # --- email validation ---

    def test_missing_at_sign_raises(self):
        rec = VALID_RECORD.copy()
        rec["email"] = "invalid-email"
        with pytest.raises(ValueError, match="Invalid email"):
            validate_customer_record(rec)

    # CUST-007 from synthetic data
    def test_synthetic_invalid_email_raises(self):
        rec = SYNTHETIC_INVALID_RECORDS[0].copy()
        with pytest.raises(ValueError, match="Invalid email"):
            validate_customer_record(rec)

    def test_email_with_at_sign_passes(self):
        rec = VALID_RECORD.copy()
        rec["email"] = "x@y"  # minimal valid-looking email
        assert validate_customer_record(rec) is True

    # --- missing required fields ---

    @pytest.mark.parametrize("missing_field", ["customer_id", "email", "age", "country_code"])
    def test_missing_required_field_raises(self, missing_field):
        rec = VALID_RECORD.copy()
        del rec[missing_field]
        with pytest.raises(ValueError, match=f"Missing required field: {missing_field}"):
            validate_customer_record(rec)

    def test_empty_record_raises_on_first_required_field(self):
        with pytest.raises(ValueError, match="Missing required field: customer_id"):
            validate_customer_record({})

    def test_extra_fields_are_ignored(self):
        rec = VALID_RECORD.copy()
        rec["extra_column"] = "some_value"
        assert validate_customer_record(rec) is True

    # --- edge cases ---

    def test_age_exactly_1(self):
        rec = VALID_RECORD.copy()
        rec["age"] = 1
        assert validate_customer_record(rec) is True

    def test_age_exactly_150(self):
        rec = VALID_RECORD.copy()
        rec["age"] = 150
        assert validate_customer_record(rec) is True

    def test_age_0_is_invalid(self):
        rec = VALID_RECORD.copy()
        rec["age"] = 0
        with pytest.raises(ValueError):
            validate_customer_record(rec)

    def test_age_151_is_invalid(self):
        rec = VALID_RECORD.copy()
        rec["age"] = 151
        with pytest.raises(ValueError):
            validate_customer_record(rec)


# ===========================================================================
# process_csv
# ===========================================================================

class TestProcessCsv:

    def _build_s3_response(self, rows: list[dict]) -> dict:
        csv_bytes = _make_csv_bytes(rows)
        return {"Body": io.BytesIO(csv_bytes)}

    # --- happy path: all rows valid ---

    def test_all_valid_rows_processed(self, patch_get_s3_client, patch_to_parquet):
        patch_get_s3_client.get_object.return_value = self._build_s3_response(SYNTHETIC_VALID_RECORDS)

        result = process_csv("my-bucket", "raw/customers.csv")

        assert result["processed"] == len(SYNTHETIC_VALID_RECORDS)
        assert result["failed"] == 0

    def test_output_key_transformation(self, patch_get_s3_client, patch_to_parquet):
        patch_get_s3_client.get_object.return_value = self._build_s3_response([VALID_RECORD])

        result = process_csv("my-bucket", "raw/customers.csv")

        assert result["output_key"] == "processed/customers.parquet"

    def test_result_contains_timestamp(self, patch_get_s3_client, patch_to_parquet):
        patch_get_s3_client.get_object.return_value = self._build_s3_response([VALID_RECORD])

        result = process_csv("my-bucket", "raw/customers.csv")

        assert "timestamp" in result
        # Should be a valid ISO format string
        from datetime import datetime
        datetime.fromisoformat(result["timestamp"])  # raises if malformed

    # --- mixed valid / invalid rows ---

    def test_mixed_rows_counted_correctly(self, patch_get_s3_client, patch_to_parquet):
        rows = SYNTHETIC_VALID_RECORDS + SYNTHETIC_INVALID_RECORDS
        patch_get_s3_client.get_object.return_value = self._build_s3_response(rows)

        result = process_csv("my-bucket", "raw/customers.csv")

        assert result["processed"] == len(SYNTHETIC_VALID_RECORDS)
        assert result["failed"] == len(SYNTHETIC_INVALID_RECORDS)

    def test_all_invalid_rows_gives_zero_processed(self, patch_get_s3_client, patch_to_parquet):
        patch_get_s3_client.get_object.return_value = self._build_s3_response(SYNTHETIC_INVALID_RECORDS)

        result = process_csv("my-bucket", "raw/customers.csv")

        assert result["processed"] == 0
        assert result["failed"] == len(SYNTHETIC_INVALID_RECORDS)

    # --- empty CSV ---

    def test_empty_csv_no_rows(self, patch_get_s3_client, patch_to_parquet):
        empty_df = pd.DataFrame(columns=["customer_id", "email", "age", "country_code"])
        buf = io.BytesIO()
        empty_df.to_csv(buf, index=False)
        buf.seek(0)
        patch_get_s3_client.get_object.return_value = {"Body": buf}

        result = process_csv("my-bucket", "raw/empty.csv")

        assert result["processed"] == 0
        assert result["failed"] == 0

    # --- S3 interactions ---

    def test_get_object_called_with_correct_args(self, patch_get_s3_client, patch_to_parquet):
        patch_get_s3_client.get_object.return_value = self._build_s3_response([VALID_RECORD])

        process_csv("target-bucket", "raw/data.csv")

        patch_get_s3_client.get_object.assert_called_once_with(
            Bucket="target-bucket", Key="raw/data.csv"
        )

    def test_to_parquet_called_with_s3_path(self, patch_get_s3_client, patch_to_parquet):
        patch_get_s3_client.get_object.return_value = self._build_s3_response([VALID_RECORD])

        process_csv("my-bucket", "raw/data.csv")

        patch_to_parquet.assert_called_once()
        call_args = patch_to_parquet.call_args
        path_arg = call_args.args[0] if call_args.args else call_args.kwargs.get("path")
        assert path_arg == "s3://my-bucket/processed/data.parquet"

    def test_s3_error_propagates(self, patch_get_s3_client, patch_to_parquet):
        patch_get_s3_client.get_object.side_effect = Exception("S3 unavailable")

        with pytest.raises(Exception, match="S3 unavailable"):
            process_csv("my-bucket", "raw/data.csv")

    # --- key path edge cases ---

    def test_key_without_raw_prefix_still_transforms(self, patch_get_s3_client, patch_to_parquet):
        """Keys not under raw/ won't have the prefix replaced but should still process."""
        patch_get_s3_client.get_object.return_value = self._build_s3_response([VALID_RECORD])

        result = process_csv("my-bucket", "other/customers.csv")

        # raw/ not in key, so output_key replaces .csv → .parquet only
        assert result["output_key"] == "other/customers.parquet"

    def test_deeply_nested_key(self, patch_get_s3_client, patch_to_parquet):
        patch_get_s3_client.get_object.return_value = self._build_s3_response([VALID_RECORD])

        result = process_csv("my-bucket", "raw/2024/01/customers.csv")

        assert result["output_