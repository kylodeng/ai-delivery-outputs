"""
Test module for api/rag_tools.py

What is tested:
- reset_sources(): initialises a fresh list in the contextvar
- get_current_sources(): returns current sources or empty list when unset
- _find_file_url(): filesystem glob lookup with LRU cache
- _to_docs_path(): URI → /docs/-relative URL conversion
- _collect_sources(): deduplication, source ID assignment, bucket management
- _log_hits(): conditional logging based on SHOW_TOOL_CALLS env var
- make_rag_tools() → get_current_date tool: returns formatted date string
- make_rag_tools() → list_products tool: partially defined (truncated source)

Mocks used:
- unittest.mock.patch for Path.rglob (_find_file_url filesystem calls)
- unittest.mock.patch for date.today (get_current_date tool)
- unittest.mock.MagicMock for the vector store passed to make_rag_tools
- unittest.mock.patch for logger to verify log calls
- monkeypatch for environment variables (SHOW_TOOL_CALLS)
- _sources_ctx contextvar reset between tests

TODOs:
- list_products tool: source is truncated; stub test added
- Any tools beyond list_products defined in make_rag_tools: source is truncated
- Integration test with a real vector store instance
- Async behaviour of _collect_sources across concurrent asyncio tasks
"""

import contextvars
import logging
import os
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Import the module under test
# ---------------------------------------------------------------------------
import importlib, sys

# We import the module; if langchain_core is unavailable we still want tests
# to run via stubs.
try:
    import api.rag_tools as rag_tools
except ImportError:  # pragma: no cover
    pytest.skip("api.rag_tools could not be imported", allow_module_level=True)

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

def _clear_sources_ctx():
    """Reset the contextvar to its default (None)."""
    _sources_ctx.set(None)


def _make_hit(
    document_name: str = "doc.pdf",
    page_start: int = 1,
    page_end: int = 2,
    product_name: str = "Generations II",
    section_title: str = "Overview",
    file_url: str = "",
    chunk_id: str = "c1",
    text: str = "Some sample text for preview.",
    doc_type: str = "product_brochure",
    word_count: int = 50,
) -> dict:
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


@pytest.fixture(autouse=True)
def reset_ctx():
    """Ensure each test starts with a clean contextvar state."""
    _clear_sources_ctx()
    yield
    _clear_sources_ctx()


@pytest.fixture(autouse=True)
def clear_lru_cache():
    """Clear _find_file_url LRU cache between tests."""
    _find_file_url.cache_clear()
    yield
    _find_file_url.cache_clear()


@pytest.fixture()
def mock_store():
    return MagicMock()


# ---------------------------------------------------------------------------
# reset_sources / get_current_sources
# ---------------------------------------------------------------------------

class TestResetSources:
    def test_sets_empty_list(self):
        reset_sources()
        result = _sources_ctx.get(None)
        assert result == []
        assert isinstance(result, list)

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
        assert first is not second


class TestGetCurrentSources:
    def test_returns_empty_list_when_unset(self):
        # contextvar is None by default
        assert get_current_sources() == []

    def test_returns_empty_list_when_none_explicitly(self):
        _sources_ctx.set(None)
        assert get_current_sources() == []

    def test_returns_list_after_reset(self):
        reset_sources()
        assert get_current_sources() == []

    def test_returns_populated_list(self):
        reset_sources()
        _sources_ctx.get(None).append({"source_id": "S1", "document": "doc.pdf"})
        result = get_current_sources()
        assert len(result) == 1
        assert result[0]["source_id"] == "S1"

    def test_returns_copy_reference_same_object(self):
        """get_current_sources returns the same list object (not a copy)."""
        reset_sources()
        bucket = _sources_ctx.get(None)
        bucket.append({"source_id": "S1"})
        returned = get_current_sources()
        assert returned is bucket


# ---------------------------------------------------------------------------
# _find_file_url
# ---------------------------------------------------------------------------

class TestFindFileUrl:
    def test_returns_uri_when_file_found(self, tmp_path):
        # Create a real file and point _DATA_DIR at tmp_path
        sub = tmp_path / "Insurance-product-info"
        sub.mkdir()
        pdf = sub / "Generations-II_PB_EN.pdf"
        pdf.write_bytes(b"%PDF-1.4")

        with patch.object(rag_tools, "_DATA_DIR", tmp_path):
            _find_file_url.cache_clear()
            url = _find_file_url("Generations-II_PB_EN.pdf")
        assert url.startswith("file:///") or url.startswith("file://")
        assert "Generations-II_PB_EN.pdf" in url

    def test_returns_empty_string_when_not_found(self, tmp_path):
        with patch.object(rag_tools, "_DATA_DIR", tmp_path):
            _find_file_url.cache_clear()
            url = _find_file_url("nonexistent_file.pdf")
        assert url == ""

    def test_lru_cache_is_used(self, tmp_path):
        with patch.object(rag_tools, "_DATA_DIR", tmp_path):
            _find_file_url.cache_clear()
            with patch.object(Path, "rglob", return_value=[]) as mock_rglob:
                _find_file_url("cached_doc.pdf")
                _find_file_url("cached_doc.pdf")
                # rglob should only be called once due to LRU cache
                assert mock_rglob.call_count == 1

    def test_returns_first_match_when_multiple_found(self, tmp_path):
        file_a = tmp_path / "a" / "doc.pdf"
        file_b = tmp_path / "b" / "doc.pdf"
        file_a.parent.mkdir()
        file_b.parent.mkdir()
        file_a.write_bytes(b"a")
        file_b.write_bytes(b"b")

        with patch.object(rag_tools, "_DATA_DIR", tmp_path):
            _find_file_url.cache_clear()
            url = _find_file_url("doc.pdf")
        assert url != ""

    def test_different_documents_cached_separately(self, tmp_path):
        with patch.object(rag_tools, "_DATA_DIR", tmp_path):
            _find_file_url.cache_clear()
            url1 = _find_file_url("doc_a.pdf")
            url2 = _find_file_url("doc_b.pdf")
        assert url1 == ""
        assert url2 == ""
        assert _find_file_url.cache_info().currsize == 2


# ---------------------------------------------------------------------------
# _to_docs_path
# ---------------------------------------------------------------------------

class TestToDocsPath:
    def test_empty_string_returns_empty(self):
        assert _to_docs_path("") == ""

    def test_none_equivalent_empty_string(self):
        # Passing empty string
        assert _to_docs_path("") == ""

    def test_valid_file_uri_converts_correctly(self, tmp_path):
        """A file URI rooted under _DATA_DIR should produce a /docs/ path."""
        sub = tmp_path / "Insurance-product-info"
        sub.mkdir()
        pdf = sub / "doc.pdf"
        pdf.write_bytes(b"%PDF")

        file_uri = pdf.resolve().as_uri()
        with patch.object(rag_tools, "_DATA_DIR", tmp_path):
            result = _to_docs_path(file_uri)
        assert result.startswith("/docs/")
        assert "doc.pdf" in result
        assert "Insurance-product-info" in result

    def test_path_outside_data_dir_returns_empty(self, tmp_path):
        """A file URI not under _DATA_DIR should return empty string."""
        outside = tmp_path / "outside" / "secret.pdf"
        outside.parent.mkdir()
        outside.write_bytes(b"data")
        file_uri = outside.resolve().as_uri()
        # Use a data dir that doesn't contain the file
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        with patch.object(rag_tools, "_DATA_DIR", data_dir):
            result = _to_docs_path(file_uri)
        assert result == ""

    def test_special_characters_are_percent_encoded(self, tmp_path):
        sub = tmp_path / "My Folder"
        sub.mkdir()
        pdf = sub / "my doc.pdf"
        pdf.write_bytes(b"%PDF")
        file_uri = pdf.resolve().as_uri()
        with patch.object(rag_tools, "_DATA_DIR", tmp_path):
            result = _to_docs_path(file_uri)
        # Spaces should be encoded as %20
        assert "%20" in result or result == ""  # depends on OS path handling

    def test_malformed_uri_returns_empty(self):
        result = _to_docs_path("not_a_valid_uri://???")
        assert result == ""

    def test_http_uri_returns_empty(self):
        """Non-file URIs cannot be relative to _DATA_DIR."""
        result = _to_docs_path("https://example.com/doc.pdf")
        assert result == ""


# ---------------------------------------------------------------------------
# _collect_sources
# ---------------------------------------------------------------------------

class TestCollectSources:
    def test_returns_empty_strings_when_bucket_is_none(self):
        """Without reset_sources(), bucket is None → all source IDs are ''."""
        hits = [_make_hit(), _make_hit()]
        result = _collect_sources(hits)
        assert result == ["", ""]

    def test_empty_hits_returns_empty_list(self):
        reset_sources()
        result = _collect_sources([])
        assert result == []

    def test_single_hit_gets_s1(self):
        reset_sources()
        hits = [_make_hit(document_name="doc.pdf", page_start=1)]
        result = _collect_sources(hits)
        assert result == ["S1"]
        bucket = _sources_ctx.get(None)
        assert len(bucket) == 1
        assert bucket[0]["source_id"] == "S1"

    def test_two_distinct_hits_get_sequential_ids(self):
        reset_sources()
        hits = [
            _make_hit(document_name="doc_a.pdf", page_start=1),
            _make_hit(document_name="doc_b.pdf", page_start=1),
        ]
        result = _collect_sources(hits)
        assert result == ["S1", "S2"]

    def test_duplicate_hit_reuses_source_id(self):
        reset_sources()
        h = _make_hit(document_name="doc.pdf", page_start=5)
        result = _collect_sources([h, h])
        assert result == ["S1", "S1"]
        # Bucket should only have one entry
        assert len(_sources_ctx.get(None)) == 1

    def test_same_document_different_pages_separate_entries(self):
        reset_sources()
        hits = [
            _make_hit(document_name="doc.pdf", page_start=1),
            _make_hit(document_name="doc.pdf", page_start=2),
        ]
        result = _collect_sources(hits)
        assert result == ["S1", "S2"]

    def test_different_documents_same_page_separate_entries(self):
        reset_sources()
        hits = [
            _make_hit(document_name="doc_a.pdf", page_start=1),
            _make_hit(document_name="doc_b.pdf", page_start=1),
        ]
        result = _collect_sources(hits)
        assert result == ["S1", "S2"]

    def test_source_entry_fields_populated_correctly(self):
        reset_sources()
        h = _make_hit(
            document_name="Generations-II_PB_EN.pdf",
            page_start=3,
            page_end=4,
            product_name="Generations II",
            section_title="Benefits",
            chunk_id="chunk_42",
            text="A" * 300,  # longer than 250 chars → preview truncated
        )
        _collect_sources([h])
        entry = _sources_ctx.get(None)[0]
        assert entry["source_id"] == "S1"
        assert entry["document"] == "Generations-II_PB_EN.pdf"
        assert entry["product"] == "Generations II"
        assert entry["page_start"] == 3
        assert entry["page_end"] == 4
        assert entry["section"] == "Benefits"
        assert entry["chunk_id"] == "chunk_42"
        assert len(entry["text_preview"]) == 250  # truncated to 250

    def test_text_preview_not_truncated_when_short(self):
        reset_sources()
        short_text = "Short text."
        h = _make_hit(text=short_text)
        _collect_sources([h])
        assert _sources_ctx.get(None)[0]["text_preview"] == short_text

    def test_missing_metadata_fields_use_defaults(self):
        reset_sources()
        hit = {"text": "content", "metadata": {}}
        _collect_sources([hit])
        entry = _sources_ctx.get(None)[0]
        assert entry["document"] == "?"
        assert entry["page_start"] == "?"
        assert entry["page_end"] == "?"
        assert entry["product"] == ""
        assert entry["section"] == ""
        assert entry["chunk_id"] == ""

    def test_file_url_from_metadata_used_when_present(self, tmp_path):
        reset_sources()
        sub = tmp_path / "Insurance-product-info"
        sub.mkdir()
        pdf = sub / "doc.pdf"
        pdf.write_bytes(b"%PDF")
        file_uri = pdf.resolve().as_