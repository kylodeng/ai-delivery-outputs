"""
Test module for api/rag_tools.py

What is tested:
- reset_sources(): initialises fresh empty list in contextvar
- get_current_sources(): returns current sources or empty list
- _find_file_url(): filesystem glob-based file lookup (mocked)
- _to_docs_path(): URI-to-server-URL conversion logic
- _collect_sources(): deduplication, source ID generation, bucket management
- _log_hits(): conditional logging based on SHOW_TOOL_CALLS flag
- make_rag_tools(): tool factory — get_current_date, list_products stubs

Mocks used:
- unittest.mock.patch for filesystem operations (_DATA_DIR, Path.rglob)
- unittest.mock.patch for os.getenv / _SHOW_TOOL_CALLS
- unittest.mock.MagicMock for the vector store passed to make_rag_tools
- unittest.mock.patch for logger to verify log calls
- contextvars isolation handled via reset_sources() / _sources_ctx.set()

TODOs:
- TODO: Full integration test for list_products tool requires a real/mocked store with known product metadata
- TODO: Tests for any additional tools inside make_rag_tools beyond get_current_date and list_products stubs
- TODO: Async tool invocation tests once async tool signatures are confirmed
"""

import contextvars
import logging
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

# ---------------------------------------------------------------------------
# Module under test
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


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

def _make_hit(document_name="doc.pdf", page_start=1, page_end=2,
              product_name="Generations II", file_url="", chunk_id="c1",
              text="Sample chunk text", section_title="Intro", doc_type="product_brochure",
              word_count=120):
    return {
        "text": text,
        "metadata": {
            "document_name": document_name,
            "page_start": page_start,
            "page_end": page_end,
            "product_name": product_name,
            "file_url": file_url,
            "chunk_id": chunk_id,
            "section_title": section_title,
            "doc_type": doc_type,
            "word_count": word_count,
        },
    }


@pytest.fixture(autouse=True)
def isolate_contextvar():
    """Reset the contextvar before every test to avoid cross-test pollution."""
    token = _sources_ctx.set(None)
    yield
    _sources_ctx.reset(token)


@pytest.fixture()
def fresh_bucket():
    """Provide a test with an initialised (empty) sources bucket."""
    reset_sources()
    yield


# ---------------------------------------------------------------------------
# reset_sources / get_current_sources
# ---------------------------------------------------------------------------

class TestResetSources:
    def test_sets_empty_list(self):
        reset_sources()
        assert _sources_ctx.get(None) == []

    def test_overwrites_existing_list(self):
        _sources_ctx.set([{"source_id": "S1"}])
        reset_sources()
        assert _sources_ctx.get(None) == []

    def test_called_twice_gives_fresh_list(self):
        reset_sources()
        first = _sources_ctx.get(None)
        first.append("sentinel")
        reset_sources()
        second = _sources_ctx.get(None)
        assert second == []
        assert "sentinel" not in second


class TestGetCurrentSources:
    def test_returns_empty_list_when_not_initialised(self):
        # contextvar default is None → should return []
        assert get_current_sources() == []

    def test_returns_empty_list_after_reset(self, fresh_bucket):
        assert get_current_sources() == []

    def test_returns_sources_after_collection(self, fresh_bucket):
        hit = _make_hit()
        _collect_sources([hit])
        sources = get_current_sources()
        assert len(sources) == 1
        assert sources[0]["source_id"] == "S1"

    def test_returns_copy_of_live_list(self, fresh_bucket):
        sources = get_current_sources()
        assert isinstance(sources, list)


# ---------------------------------------------------------------------------
# _find_file_url
# ---------------------------------------------------------------------------

class TestFindFileUrl:
    def test_returns_uri_when_file_found(self, tmp_path):
        # Create a real temp file and patch _DATA_DIR
        subdir = tmp_path / "Insurance-product-info"
        subdir.mkdir()
        pdf = subdir / "doc.pdf"
        pdf.write_bytes(b"%PDF")

        with patch.object(rag_tools, "_DATA_DIR", tmp_path):
            # Clear LRU cache so patch takes effect
            _find_file_url.cache_clear()
            result = _find_file_url("doc.pdf")
        _find_file_url.cache_clear()

        assert result.startswith("file:///") or result.startswith("file:/")
        assert "doc.pdf" in result

    def test_returns_empty_string_when_not_found(self, tmp_path):
        with patch.object(rag_tools, "_DATA_DIR", tmp_path):
            _find_file_url.cache_clear()
            result = _find_file_url("nonexistent.pdf")
        _find_file_url.cache_clear()

        assert result == ""

    def test_lru_cache_returns_same_result(self, tmp_path):
        subdir = tmp_path / "Insurance-product-info"
        subdir.mkdir()
        pdf = subdir / "cached.pdf"
        pdf.write_bytes(b"%PDF")

        with patch.object(rag_tools, "_DATA_DIR", tmp_path):
            _find_file_url.cache_clear()
            first = _find_file_url("cached.pdf")
            second = _find_file_url("cached.pdf")
        _find_file_url.cache_clear()

        assert first == second

    def test_empty_document_name(self, tmp_path):
        with patch.object(rag_tools, "_DATA_DIR", tmp_path):
            _find_file_url.cache_clear()
            result = _find_file_url("")
        _find_file_url.cache_clear()
        # Either empty string or some URI — must not raise
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# _to_docs_path
# ---------------------------------------------------------------------------

class TestToDocsPath:
    def test_empty_string_returns_empty(self):
        assert _to_docs_path("") == ""

    def test_valid_file_uri_returns_docs_path(self, tmp_path):
        # Build a real URI that resolves relative to _DATA_DIR
        subdir = tmp_path / "Insurance-product-info"
        subdir.mkdir()
        pdf = subdir / "Generations-II_PB_EN.pdf"
        pdf.write_bytes(b"%PDF")

        uri = pdf.resolve().as_uri()
        with patch.object(rag_tools, "_DATA_DIR", tmp_path):
            result = _to_docs_path(uri)

        assert result == "/docs/Insurance-product-info/Generations-II_PB_EN.pdf"

    def test_path_outside_data_dir_returns_empty(self, tmp_path):
        # A URI whose path is NOT relative to _DATA_DIR → ValueError → returns ""
        outside = tmp_path / "outside" / "other.pdf"
        outside.parent.mkdir(parents=True)
        outside.write_bytes(b"%PDF")
        uri = outside.resolve().as_uri()

        data_dir = tmp_path / "data"
        data_dir.mkdir()
        with patch.object(rag_tools, "_DATA_DIR", data_dir):
            result = _to_docs_path(uri)

        assert result == ""

    def test_spaces_in_filename_are_percent_encoded(self, tmp_path):
        subdir = tmp_path / "products"
        subdir.mkdir()
        pdf = subdir / "my doc.pdf"
        pdf.write_bytes(b"%PDF")
        uri = pdf.resolve().as_uri()

        with patch.object(rag_tools, "_DATA_DIR", tmp_path):
            result = _to_docs_path(uri)

        assert "my%20doc.pdf" in result or "my+doc.pdf" in result or "my doc.pdf" not in result

    def test_malformed_uri_returns_empty(self):
        result = _to_docs_path("not-a-uri://\x00bad")
        assert result == ""

    def test_returns_string_for_any_input(self):
        for val in ["", "file:///", "http://example.com/doc.pdf", "ftp://bad"]:
            assert isinstance(_to_docs_path(val), str)


# ---------------------------------------------------------------------------
# _collect_sources
# ---------------------------------------------------------------------------

class TestCollectSources:
    def test_no_bucket_returns_empty_strings(self):
        # contextvar is None → should return list of empty strings
        hits = [_make_hit(), _make_hit(document_name="other.pdf")]
        result = _collect_sources(hits)
        assert result == ["", ""]

    def test_empty_hits_returns_empty_list(self, fresh_bucket):
        result = _collect_sources([])
        assert result == []

    def test_single_hit_creates_s1(self, fresh_bucket):
        hit = _make_hit(document_name="doc.pdf", page_start=1)
        result = _collect_sources([hit])
        assert result == ["S1"]
        bucket = _sources_ctx.get(None)
        assert len(bucket) == 1
        assert bucket[0]["source_id"] == "S1"

    def test_two_different_hits_create_s1_s2(self, fresh_bucket):
        hits = [
            _make_hit(document_name="a.pdf", page_start=1),
            _make_hit(document_name="b.pdf", page_start=1),
        ]
        result = _collect_sources(hits)
        assert result == ["S1", "S2"]
        assert len(_sources_ctx.get(None)) == 2

    def test_duplicate_hit_reuses_source_id(self, fresh_bucket):
        hit = _make_hit(document_name="doc.pdf", page_start=3)
        result1 = _collect_sources([hit])
        result2 = _collect_sources([hit])
        assert result1 == ["S1"]
        assert result2 == ["S1"]  # same dedup key → reused
        assert len(_sources_ctx.get(None)) == 1

    def test_same_doc_different_pages_are_distinct(self, fresh_bucket):
        hits = [
            _make_hit(document_name="doc.pdf", page_start=1),
            _make_hit(document_name="doc.pdf", page_start=5),
        ]
        result = _collect_sources(hits)
        assert result == ["S1", "S2"]

    def test_source_entry_fields(self, fresh_bucket):
        hit = _make_hit(
            document_name="Generations-II_PB_EN.pdf",
            page_start=4,
            page_end=5,
            product_name="Generations II",
            chunk_id="c42",
            text="Hello world" * 30,
            section_title="Benefits",
        )
        _collect_sources([hit])
        entry = _sources_ctx.get(None)[0]
        assert entry["source_id"] == "S1"
        assert entry["document"] == "Generations-II_PB_EN.pdf"
        assert entry["product"] == "Generations II"
        assert entry["page_start"] == 4
        assert entry["page_end"] == 5
        assert entry["section"] == "Benefits"
        assert entry["chunk_id"] == "c42"
        assert len(entry["text_preview"]) <= 250

    def test_text_preview_truncated_to_250(self, fresh_bucket):
        long_text = "X" * 500
        hit = _make_hit(text=long_text)
        _collect_sources([hit])
        entry = _sources_ctx.get(None)[0]
        assert entry["text_preview"] == "X" * 250

    def test_missing_metadata_fields_use_defaults(self, fresh_bucket):
        hit = {"text": "bare hit", "metadata": {}}
        _collect_sources([hit])
        entry = _sources_ctx.get(None)[0]
        assert entry["document"] == "?"
        assert entry["page_start"] == "?"
        assert entry["page_end"] == "?"
        assert entry["product"] == ""
        assert entry["section"] == ""

    def test_hit_without_metadata_key_uses_defaults(self, fresh_bucket):
        hit = {"text": "no metadata key"}
        _collect_sources([hit])
        entry = _sources_ctx.get(None)[0]
        assert entry["document"] == "?"

    def test_file_url_populated_from_metadata(self, tmp_path, fresh_bucket):
        subdir = tmp_path / "Insurance-product-info"
        subdir.mkdir()
        pdf = subdir / "doc.pdf"
        pdf.write_bytes(b"%PDF")
        uri = pdf.resolve().as_uri()
        hit = _make_hit(document_name="doc.pdf", file_url=uri)

        with patch.object(rag_tools, "_DATA_DIR", tmp_path):
            _collect_sources([hit])

        entry = _sources_ctx.get(None)[0]
        assert entry["file_url"] == "/docs/Insurance-product-info/doc.pdf"

    def test_file_url_falls_back_to_find_file_url(self, tmp_path, fresh_bucket):
        subdir = tmp_path / "Insurance-product-info"
        subdir.mkdir()
        pdf = subdir / "fallback.pdf"
        pdf.write_bytes(b"%PDF")

        with patch.object(rag_tools, "_DATA_DIR", tmp_path):
            _find_file_url.cache_clear()
            hit = _make_hit(document_name="fallback.pdf", file_url="")
            _collect_sources([hit])
            _find_file_url.cache_clear()

        entry = _sources_ctx.get(None)[0]
        assert "fallback.pdf" in entry["file_url"]

    def test_counter_increments_across_separate_calls(self, fresh_bucket):
        _collect_sources([_make_hit(document_name="a.pdf", page_start=1)])
        _collect_sources([_make_hit(document_name="b.pdf", page_start=1)])
        _collect_sources([_make_hit(document_name="c.pdf", page_start=1)])
        ids = [s["source_id"] for s in _sources_ctx.get(None)]
        assert ids == ["S1", "S2", "S3"]

    def test_mixed_duplicates_and_new_hits(self, fresh_bucket):
        hits = [
            _make_hit(document_name="a.pdf", page_start=1),
            _make_hit(document_name="b.pdf", page_start=1),
            _make_hit(document_name="a.pdf", page_start=1),  # duplicate
        ]
        result = _collect_sources(hits)
        assert result == ["S1", "S2", "S1"]
        assert len(_sources_ctx.get(None)) == 2

    def test_large_batch_of_hits(self, fresh_bucket):
        hits = [_make_hit(document_name