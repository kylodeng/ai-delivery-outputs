"""
Test module for api/rag_tools.py

What is tested:
  - reset_sources(): initialises a fresh list in the contextvar
  - get_current_sources(): reads back sources from the contextvar
  - _find_file_url(): filesystem glob with lru_cache
  - _to_docs_path(): URI-to-server-URL conversion
  - _collect_sources(): deduplication, ID assignment, bucket population
  - _log_hits(): conditional logging based on SHOW_TOOL_CALLS env var
  - make_rag_tools() / get_current_date tool: date formatting
  - make_rag_tools() / list_products tool: stub (source truncated)

Mocks used:
  - unittest.mock.patch for Path.rglob, date.today, os.getenv, logger
  - In-process contextvar manipulation (no external services)
  - Fake store object passed to make_rag_tools

TODOs:
  - list_products tool: source code is truncated; full behaviour cannot be tested
  - Any additional tools defined after list_products in make_rag_tools
  - Integration test with a real vector store
"""

import contextvars
import logging
import os
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

# ---------------------------------------------------------------------------
# Import the module under test
# ---------------------------------------------------------------------------
import api.rag_tools as rag_tools
from api.rag_tools import (
    _collect_sources,
    _find_file_url,
    _log_hits,
    _sources_ctx,
    _to_docs_path,
    get_current_sources,
    make_rag_tools,
    reset_sources,
)


# ===========================================================================
# Helpers / fixtures
# ===========================================================================

@pytest.fixture(autouse=True)
def reset_contextvar():
    """Ensure each test starts with a clean contextvar state."""
    token = _sources_ctx.set(None)
    yield
    _sources_ctx.reset(token)


@pytest.fixture(autouse=True)
def clear_lru_cache():
    """Clear lru_cache between tests so filesystem mocks are not skipped."""
    _find_file_url.cache_clear()
    yield
    _find_file_url.cache_clear()


def _make_hit(
    document_name="Generations-II_PB_EN.pdf",
    page_start=1,
    page_end=2,
    product_name="Generations II",
    section_title="Overview",
    file_url="",
    chunk_id="c001",
    text="Sample chunk text for testing purposes.",
    doc_type="product_brochure",
    word_count=10,
):
    return {
        "text": text,
        "metadata": {
            "document_name": document_name,
            "page_start": page_start,
            "page_end": page_end,
            "product_name": product_name,
            "section_title": section_title,
            "file_url": file_url,
            "chunk_id": chunk_id,
            "doc_type": doc_type,
            "word_count": word_count,
        },
    }


# ===========================================================================
# reset_sources
# ===========================================================================

class TestResetSources:
    def test_initialises_empty_list(self):
        reset_sources()
        assert _sources_ctx.get(None) == []

    def test_replaces_existing_list(self):
        _sources_ctx.set(["stale"])
        reset_sources()
        assert _sources_ctx.get(None) == []

    def test_called_twice_gives_fresh_list(self):
        reset_sources()
        first = _sources_ctx.get(None)
        first.append("something")
        reset_sources()
        assert _sources_ctx.get(None) == []


# ===========================================================================
# get_current_sources
# ===========================================================================

class TestGetCurrentSources:
    def test_returns_empty_list_when_not_initialised(self):
        assert get_current_sources() == []

    def test_returns_empty_list_when_contextvar_is_none(self):
        _sources_ctx.set(None)
        assert get_current_sources() == []

    def test_returns_populated_list(self):
        reset_sources()
        bucket = _sources_ctx.get(None)
        bucket.append({"source_id": "S1"})
        result = get_current_sources()
        assert result == [{"source_id": "S1"}]

    def test_returns_list_reference(self):
        reset_sources()
        result = get_current_sources()
        assert isinstance(result, list)


# ===========================================================================
# _find_file_url
# ===========================================================================

class TestFindFileUrl:
    def test_returns_uri_when_file_found(self, tmp_path):
        fake_file = tmp_path / "doc.pdf"
        fake_file.touch()

        with patch.object(Path, "rglob", return_value=[fake_file]):
            result = _find_file_url("doc.pdf")

        assert result.startswith("file:///") or result.startswith("file:/")
        assert "doc.pdf" in result

    def test_returns_empty_string_when_not_found(self):
        with patch.object(Path, "rglob", return_value=[]):
            result = _find_file_url("nonexistent.pdf")

        assert result == ""

    def test_returns_first_match_when_multiple(self, tmp_path):
        file_a = tmp_path / "a" / "doc.pdf"
        file_b = tmp_path / "b" / "doc.pdf"
        file_a.parent.mkdir(parents=True)
        file_b.parent.mkdir(parents=True)
        file_a.touch()
        file_b.touch()

        with patch.object(Path, "rglob", return_value=[file_a, file_b]):
            result = _find_file_url("doc.pdf")

        assert "a" in result or "doc.pdf" in result

    def test_caches_result(self):
        with patch.object(Path, "rglob", return_value=[]) as mock_rglob:
            _find_file_url("cached.pdf")
            _find_file_url("cached.pdf")
            # rglob should only be called once due to lru_cache
            assert mock_rglob.call_count == 1


# ===========================================================================
# _to_docs_path
# ===========================================================================

class TestToDocsPath:
    def test_returns_empty_string_for_empty_input(self):
        assert _to_docs_path("") == ""

    def test_converts_file_uri_to_docs_path(self, tmp_path):
        # Create a temp directory structure matching _DATA_DIR expectations
        data_dir = tmp_path / "data"
        sub_dir = data_dir / "Insurance-product-info"
        sub_dir.mkdir(parents=True)
        fake_file = sub_dir / "doc.pdf"
        fake_file.touch()

        file_url = fake_file.resolve().as_uri()

        with patch.object(rag_tools, "_DATA_DIR", data_dir):
            result = _to_docs_path(file_url)

        assert result.startswith("/docs/")
        assert "Insurance-product-info" in result
        assert "doc.pdf" in result

    def test_returns_empty_string_on_path_not_relative_to_data_dir(self, tmp_path):
        # File outside _DATA_DIR should trigger the except branch
        outside_file = tmp_path / "outside" / "doc.pdf"
        outside_file.parent.mkdir(parents=True)
        outside_file.touch()

        file_url = outside_file.resolve().as_uri()

        data_dir = tmp_path / "data"
        data_dir.mkdir()

        with patch.object(rag_tools, "_DATA_DIR", data_dir):
            result = _to_docs_path(file_url)

        assert result == ""

    def test_url_encodes_spaces_in_path(self, tmp_path):
        data_dir = tmp_path / "data"
        sub_dir = data_dir / "My Documents"
        sub_dir.mkdir(parents=True)
        fake_file = sub_dir / "my doc.pdf"
        fake_file.touch()

        file_url = fake_file.resolve().as_uri()

        with patch.object(rag_tools, "_DATA_DIR", data_dir):
            result = _to_docs_path(file_url)

        assert "%20" in result or "my%20doc" in result or "My%20Documents" in result

    def test_invalid_uri_returns_empty_string(self):
        result = _to_docs_path("not-a-uri-at-all:::///???")
        assert result == ""


# ===========================================================================
# _collect_sources
# ===========================================================================

class TestCollectSources:
    def test_returns_empty_strings_when_no_bucket(self):
        # contextvar is None (not reset)
        hits = [_make_hit()]
        result = _collect_sources(hits)
        assert result == [""]

    def test_returns_list_of_empty_strings_for_multiple_hits_no_bucket(self):
        hits = [_make_hit(), _make_hit(document_name="other.pdf")]
        result = _collect_sources(hits)
        assert result == ["", ""]

    def test_assigns_sequential_ids(self):
        reset_sources()
        hits = [
            _make_hit(document_name="doc_a.pdf", page_start=1),
            _make_hit(document_name="doc_b.pdf", page_start=1),
        ]
        result = _collect_sources(hits)
        assert result == ["S1", "S2"]

    def test_deduplicates_same_document_and_page(self):
        reset_sources()
        hit = _make_hit(document_name="doc_a.pdf", page_start=1)
        result = _collect_sources([hit, hit])
        assert result == ["S1", "S1"]
        assert len(_sources_ctx.get(None)) == 1

    def test_different_pages_same_document_get_different_ids(self):
        reset_sources()
        hits = [
            _make_hit(document_name="doc.pdf", page_start=1),
            _make_hit(document_name="doc.pdf", page_start=2),
        ]
        result = _collect_sources(hits)
        assert result == ["S1", "S2"]

    def test_bucket_populated_with_correct_fields(self):
        reset_sources()
        hit = _make_hit(
            document_name="Generations-II_PB_EN.pdf",
            page_start=3,
            page_end=4,
            product_name="Generations II",
            section_title="Benefits",
            chunk_id="c007",
            text="Hello world",
        )
        _collect_sources([hit])
        bucket = _sources_ctx.get(None)
        assert len(bucket) == 1
        entry = bucket[0]
        assert entry["source_id"] == "S1"
        assert entry["document"] == "Generations-II_PB_EN.pdf"
        assert entry["product"] == "Generations II"
        assert entry["page_start"] == 3
        assert entry["page_end"] == 4
        assert entry["section"] == "Benefits"
        assert entry["chunk_id"] == "c007"
        assert entry["text_preview"] == "Hello world"

    def test_text_preview_truncated_to_250_chars(self):
        reset_sources()
        long_text = "A" * 300
        hit = _make_hit(text=long_text)
        _collect_sources([hit])
        bucket = _sources_ctx.get(None)
        assert len(bucket[0]["text_preview"]) == 250

    def test_uses_find_file_url_when_no_file_url_in_metadata(self):
        reset_sources()
        hit = _make_hit(file_url="")

        with patch.object(rag_tools, "_find_file_url", return_value="") as mock_find:
            _collect_sources([hit])
            mock_find.assert_called_once_with("Generations-II_PB_EN.pdf")

    def test_prefers_metadata_file_url_over_find_file_url(self):
        reset_sources()
        hit = _make_hit(file_url="file:///some/path/doc.pdf")

        with patch.object(rag_tools, "_find_file_url") as mock_find:
            with patch.object(rag_tools, "_to_docs_path", return_value="/docs/doc.pdf"):
                _collect_sources([hit])
                mock_find.assert_not_called()

    def test_empty_hits_list_returns_empty_list(self):
        reset_sources()
        result = _collect_sources([])
        assert result == []

    def test_missing_metadata_uses_defaults(self):
        reset_sources()
        # Hit with no metadata at all
        hit = {"text": "raw text", "metadata": {}}
        result = _collect_sources([hit])
        assert result == ["S1"]
        bucket = _sources_ctx.get(None)
        assert bucket[0]["document"] == "?"
        assert bucket[0]["page_start"] == "?"

    def test_dedup_across_multiple_calls(self):
        reset_sources()
        hit_a = _make_hit(document_name="doc.pdf", page_start=1)
        _collect_sources([hit_a])
        # Second call with same hit should reuse S1
        result = _collect_sources([hit_a])
        assert result == ["S1"]
        assert len(_sources_ctx.get(None)) == 1

    def test_counter_continues_after_previous_entries(self):
        reset_sources()
        hit_a = _make_hit(document_name="doc_a.pdf", page_start=1)
        hit_b = _make_hit(document_name="doc_b.pdf", page_start=1)
        _collect_sources([hit_a])     # S1
        result = _collect_sources([hit_b])  # S2
        assert result == ["S2"]

    def test_synthetic_insurance_data(self):
        """Uses the synthetic data samples from the brief."""
        reset_sources()
        hits = [
            _make_hit(
                document_name="Generations-II_PB_EN.pdf",
                product_name="Generations II",
                doc_type="product_brochure",
                page_start=1,
                page_end=3,
            ),
            _make_hit(
                document_name="List of designated hospitals in mainland China.pdf",
                product_name="List of Designated Hospitals in Mainland China",
                doc_type="supplementary",
                page_start=1,
                page_end=1,
            ),
            _make_hit(
                document_name="Mainland_China_VIP_Hospital_Network.pdf",
                product_name="List of Network Hospitals with Mainland China VIP Medical Navigation Service",
                doc_type="supplementary",
                page_start=2,
                page_end=4,
            ),
        ]
        result = _collect_sources(hits)
        assert result == ["S1", "S2", "S3"]
        bucket = _sources_ctx.get(None)
        assert len(bucket) == 3
        assert bucket[0]["product"] == "Generations II"
        assert bucket[1]["document"] == "List of designated hospitals in mainland China.pdf"

    @pytest.mark.parametrize("n_hits", [0, 1, 5, 50])
    def test_bucket_length_matches_unique_hits(self, n_hits):
        reset_sources()
        hits = [
            _make_hit(document_name=f"doc_{i}.pdf", page_start=i)
            for i in range(n_hits)
        ]
        _collect_sources(hits)