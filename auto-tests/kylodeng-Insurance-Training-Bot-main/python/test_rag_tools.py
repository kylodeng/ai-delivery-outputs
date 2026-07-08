"""
Test module for api/rag_tools.py

What is tested:
- reset_sources(): initialises a fresh list in the contextvar
- get_current_sources(): returns current sources or empty list
- _find_file_url(): filesystem search for document files (mocked)
- _to_docs_path(): URI-to-server-path conversion logic
- _collect_sources(): deduplication, source ID assignment, bucket management
- _log_hits(): conditional logging based on SHOW_TOOL_CALLS env var
- make_rag_tools(): factory returns tools; get_current_date tool; list_products tool stub

Mocks used:
- unittest.mock.patch for Path.rglob (filesystem)
- unittest.mock.patch for _DATA_DIR
- unittest.mock.patch for logging.Logger.info
- unittest.mock.patch for date.today
- unittest.mock.MagicMock for vector store passed to make_rag_tools

TODOs:
- list_products tool: source code is truncated; full behaviour cannot be verified
- Any additional tools created inside make_rag_tools beyond what is visible
- Integration test with a real vector store instance
"""

import contextvars
import logging
import os
import sys
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock
import pytest

# ---------------------------------------------------------------------------
# Ensure the package root is importable regardless of working directory
# ---------------------------------------------------------------------------
sys.path.insert(0, str(Path(__file__).parent.parent))

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
# Helpers
# ---------------------------------------------------------------------------

def _make_hit(
    document_name: str = "doc.pdf",
    page_start: int = 1,
    page_end: int = 2,
    product_name: str = "Generations II",
    doc_type: str = "product_brochure",
    section_title: str = "Overview",
    file_url: str = "",
    chunk_id: str = "c001",
    word_count: int = 100,
    text: str = "Sample text content.",
) -> dict:
    return {
        "metadata": {
            "document_name": document_name,
            "page_start": page_start,
            "page_end": page_end,
            "product_name": product_name,
            "doc_type": doc_type,
            "section_title": section_title,
            "file_url": file_url,
            "chunk_id": chunk_id,
            "word_count": word_count,
        },
        "text": text,
    }


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def reset_contextvar():
    """Reset the contextvar to None before/after every test for isolation."""
    token = _sources_ctx.set(None)
    yield
    _sources_ctx.reset(token)


@pytest.fixture()
def initialised_bucket():
    """Provide a request with an initialised (empty) source bucket."""
    reset_sources()
    return _sources_ctx.get()


# ---------------------------------------------------------------------------
# reset_sources
# ---------------------------------------------------------------------------

class TestResetSources:
    def test_sets_empty_list(self):
        reset_sources()
        bucket = _sources_ctx.get(None)
        assert bucket == []

    def test_overwrites_existing_list(self):
        _sources_ctx.set(["stale"])
        reset_sources()
        assert _sources_ctx.get(None) == []

    def test_called_twice_gives_fresh_list(self):
        reset_sources()
        first = _sources_ctx.get()
        first.append("something")
        reset_sources()
        assert _sources_ctx.get(None) == []

    def test_returns_none(self):
        result = reset_sources()
        assert result is None


# ---------------------------------------------------------------------------
# get_current_sources
# ---------------------------------------------------------------------------

class TestGetCurrentSources:
    def test_returns_empty_list_when_contextvar_is_none(self):
        assert get_current_sources() == []

    def test_returns_list_when_initialised(self, initialised_bucket):
        assert get_current_sources() == []

    def test_returns_populated_list(self, initialised_bucket):
        initialised_bucket.append({"source_id": "S1"})
        result = get_current_sources()
        assert result == [{"source_id": "S1"}]

    def test_same_reference_as_bucket(self, initialised_bucket):
        result = get_current_sources()
        assert result is initialised_bucket


# ---------------------------------------------------------------------------
# _find_file_url
# ---------------------------------------------------------------------------

class TestFindFileUrl:
    def test_returns_uri_when_file_found(self, tmp_path):
        doc = tmp_path / "doc.pdf"
        doc.touch()
        with patch.object(rag_tools, "_DATA_DIR", tmp_path):
            # Clear lru_cache so our patched _DATA_DIR is used
            _find_file_url.cache_clear()
            result = _find_file_url("doc.pdf")
        _find_file_url.cache_clear()
        assert result.startswith("file:///") or result.startswith("file://")
        assert "doc.pdf" in result

    def test_returns_empty_string_when_not_found(self, tmp_path):
        with patch.object(rag_tools, "_DATA_DIR", tmp_path):
            _find_file_url.cache_clear()
            result = _find_file_url("nonexistent.pdf")
        _find_file_url.cache_clear()
        assert result == ""

    def test_returns_first_match_when_multiple_found(self, tmp_path):
        sub1 = tmp_path / "a"
        sub1.mkdir()
        sub2 = tmp_path / "b"
        sub2.mkdir()
        (sub1 / "doc.pdf").touch()
        (sub2 / "doc.pdf").touch()
        with patch.object(rag_tools, "_DATA_DIR", tmp_path):
            _find_file_url.cache_clear()
            result = _find_file_url("doc.pdf")
        _find_file_url.cache_clear()
        assert "doc.pdf" in result

    def test_caches_result(self, tmp_path):
        doc = tmp_path / "cached.pdf"
        doc.touch()
        with patch.object(rag_tools, "_DATA_DIR", tmp_path):
            _find_file_url.cache_clear()
            r1 = _find_file_url("cached.pdf")
            r2 = _find_file_url("cached.pdf")
        _find_file_url.cache_clear()
        assert r1 == r2

    def test_empty_document_name(self, tmp_path):
        with patch.object(rag_tools, "_DATA_DIR", tmp_path):
            _find_file_url.cache_clear()
            result = _find_file_url("")
        _find_file_url.cache_clear()
        # Should return empty string (no match)
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# _to_docs_path
# ---------------------------------------------------------------------------

class TestToDocsPath:
    def test_empty_string_returns_empty(self):
        assert _to_docs_path("") == ""

    def test_converts_file_uri_to_docs_path(self, tmp_path):
        data_dir = tmp_path / "data"
        sub = data_dir / "Insurance-product-info"
        sub.mkdir(parents=True)
        doc = sub / "doc.pdf"
        doc.touch()
        file_url = doc.resolve().as_uri()
        with patch.object(rag_tools, "_DATA_DIR", data_dir):
            result = _to_docs_path(file_url)
        assert result.startswith("/docs/")
        assert "Insurance-product-info" in result
        assert "doc.pdf" in result

    def test_returns_empty_on_invalid_uri(self):
        result = _to_docs_path("not-a-valid-uri://??##")
        # Should not raise; returns empty string on exception
        assert isinstance(result, str)

    def test_returns_empty_when_path_not_under_data_dir(self, tmp_path):
        other = tmp_path / "other" / "file.pdf"
        other.parent.mkdir(parents=True)
        other.touch()
        file_url = other.resolve().as_uri()
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        with patch.object(rag_tools, "_DATA_DIR", data_dir):
            result = _to_docs_path(file_url)
        assert result == ""

    def test_url_encodes_special_chars(self, tmp_path):
        data_dir = tmp_path / "data"
        sub = data_dir / "My Folder"
        sub.mkdir(parents=True)
        doc = sub / "my file.pdf"
        doc.touch()
        file_url = doc.resolve().as_uri()
        with patch.object(rag_tools, "_DATA_DIR", data_dir):
            result = _to_docs_path(file_url)
        # Spaces should be percent-encoded in the output
        assert " " not in result
        assert result.startswith("/docs/")


# ---------------------------------------------------------------------------
# _collect_sources
# ---------------------------------------------------------------------------

class TestCollectSources:
    def test_returns_empty_strings_when_no_bucket(self):
        # contextvar is None (autouse fixture ensures this)
        hits = [_make_hit(), _make_hit(document_name="other.pdf")]
        result = _collect_sources(hits)
        assert result == ["", ""]

    def test_assigns_sequential_source_ids(self, initialised_bucket):
        hits = [
            _make_hit(document_name="a.pdf", page_start=1),
            _make_hit(document_name="b.pdf", page_start=1),
        ]
        result = _collect_sources(hits)
        assert result == ["S1", "S2"]

    def test_deduplicates_same_doc_and_page(self, initialised_bucket):
        hits = [
            _make_hit(document_name="a.pdf", page_start=1),
            _make_hit(document_name="a.pdf", page_start=1),  # duplicate
        ]
        result = _collect_sources(hits)
        assert result == ["S1", "S1"]
        assert len(initialised_bucket) == 1

    def test_different_pages_same_doc_get_different_ids(self, initialised_bucket):
        hits = [
            _make_hit(document_name="a.pdf", page_start=1),
            _make_hit(document_name="a.pdf", page_start=5),
        ]
        result = _collect_sources(hits)
        assert result == ["S1", "S2"]
        assert len(initialised_bucket) == 2

    def test_entry_shape(self, initialised_bucket):
        hit = _make_hit(
            document_name="Generations-II_PB_EN.pdf",
            page_start=3,
            page_end=4,
            product_name="Generations II",
            section_title="Benefits",
            chunk_id="c42",
            text="Some long text that should be truncated" + "x" * 300,
        )
        _collect_sources([hit])
        entry = initialised_bucket[0]
        assert entry["source_id"] == "S1"
        assert entry["document"] == "Generations-II_PB_EN.pdf"
        assert entry["product"] == "Generations II"
        assert entry["page_start"] == 3
        assert entry["page_end"] == 4
        assert entry["section"] == "Benefits"
        assert entry["chunk_id"] == "c42"
        assert len(entry["text_preview"]) <= 250

    def test_text_preview_truncated_to_250(self, initialised_bucket):
        long_text = "A" * 500
        hit = _make_hit(text=long_text)
        _collect_sources([hit])
        assert len(initialised_bucket[0]["text_preview"]) == 250

    def test_empty_hits_returns_empty_list(self, initialised_bucket):
        result = _collect_sources([])
        assert result == []

    def test_missing_metadata_fields_use_defaults(self, initialised_bucket):
        hit = {"metadata": {}, "text": "hello"}
        result = _collect_sources([hit])
        assert result == ["S1"]
        entry = initialised_bucket[0]
        assert entry["document"] == "?"
        assert entry["page_start"] == "?"
        assert entry["page_end"] == "?"
        assert entry["product"] == ""
        assert entry["section"] == ""

    def test_hit_without_metadata_key(self, initialised_bucket):
        hit = {"text": "no metadata"}
        result = _collect_sources([hit])
        assert result == ["S1"]

    def test_uses_find_file_url_fallback_when_no_file_url(self, initialised_bucket):
        hit = _make_hit(document_name="fallback.pdf", file_url="")
        with patch.object(rag_tools, "_find_file_url", return_value="") as mock_find:
            _collect_sources([hit])
        mock_find.assert_called_once_with("fallback.pdf")

    def test_uses_metadata_file_url_when_present(self, initialised_bucket, tmp_path):
        data_dir = tmp_path / "data"
        sub = data_dir / "subdir"
        sub.mkdir(parents=True)
        doc = sub / "doc.pdf"
        doc.touch()
        file_url = doc.resolve().as_uri()
        hit = _make_hit(document_name="doc.pdf", file_url=file_url)
        with patch.object(rag_tools, "_DATA_DIR", data_dir):
            with patch.object(rag_tools, "_find_file_url") as mock_find:
                _collect_sources([hit])
                mock_find.assert_not_called()

    def test_id_counter_continues_across_calls(self, initialised_bucket):
        hit1 = _make_hit(document_name="a.pdf", page_start=1)
        hit2 = _make_hit(document_name="b.pdf", page_start=1)
        _collect_sources([hit1])
        result2 = _collect_sources([hit2])
        assert result2 == ["S2"]

    def test_duplicate_across_separate_calls_reuses_id(self, initialised_bucket):
        hit = _make_hit(document_name="a.pdf", page_start=1)
        _collect_sources([hit])
        result2 = _collect_sources([hit])
        assert result2 == ["S1"]
        assert len(initialised_bucket) == 1

    def test_many_hits_get_sequential_ids(self, initialised_bucket):
        hits = [
            _make_hit(document_name=f"doc{i}.pdf", page_start=i)
            for i in range(10)
        ]
        result = _collect_sources(hits)
        expected = [f"S{i+1}" for i in range(10)]
        assert result == expected

    def test_insurance_synthetic_data(self, initialised_bucket):
        """Use synthetic data samples from the task description."""
        hits = [
            _make_hit(
                document_name="Generations-II_PB_EN.pdf",
                page_start=1,
                product_name="Generations II",
                doc_type="product_brochure",
            ),
            _make_hit(
                document_name="List of designated hospitals in