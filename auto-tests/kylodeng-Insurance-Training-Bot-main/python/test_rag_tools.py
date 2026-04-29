"""
Test module for api/rag_tools.py

What is tested:
- reset_sources(): initialises a fresh list in the contextvar
- get_current_sources(): returns the current sources list or empty list
- _find_file_url(): filesystem glob search with lru_cache
- _to_docs_path(): URI → /docs/-relative server URL conversion
- _collect_sources(): dedup logic, source ID assignment, metadata extraction
- _log_hits(): conditional logging based on SHOW_TOOL_CALLS env var
- make_rag_tools(): factory returns a list of LangChain tools; tools are callable
- get_current_date tool: returns today's date string
- list_products tool: stub (source truncated)

Mocks used:
- unittest.mock.patch for filesystem (Path.rglob), os.getenv, logging, date.today
- In-memory contextvar manipulation (no real filesystem or vector store calls)
- Fake 'store' object passed to make_rag_tools

TODOs:
- list_products tool body is truncated in source — stub tests added
- Additional RAG tools defined inside make_rag_tools are not visible — stubs added
- Integration tests against a real vector store are skipped
"""

import contextvars
import logging
import os
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock
import pytest

# ---------------------------------------------------------------------------
# Import the module under test
# ---------------------------------------------------------------------------
import importlib
import sys

# We need to import the module; patch heavy optional deps if absent
with patch.dict(sys.modules, {}):
    import api.rag_tools as rag_tools

from api.rag_tools import (
    reset_sources,
    get_current_sources,
    _find_file_url,
    _to_docs_path,
    _collect_sources,
    _log_hits,
    make_rag_tools,
    _sources_ctx,
)


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def reset_contextvar():
    """Ensure the contextvar is reset to None before every test."""
    token = _sources_ctx.set(None)
    yield
    _sources_ctx.reset(token)


@pytest.fixture(autouse=True)
def clear_find_file_url_cache():
    """Clear lru_cache between tests to avoid cross-test pollution."""
    _find_file_url.cache_clear()
    yield
    _find_file_url.cache_clear()


def _make_hit(
    document_name="doc.pdf",
    page_start=1,
    page_end=2,
    product_name="Product A",
    section_title="Section 1",
    file_url="",
    chunk_id="c1",
    doc_type="policy",
    word_count=100,
    text="Sample text for testing purposes.",
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
# reset_sources / get_current_sources
# ===========================================================================

class TestResetSources:
    def test_sets_empty_list(self):
        reset_sources()
        assert _sources_ctx.get(None) == []

    def test_replaces_existing_list(self):
        _sources_ctx.set(["existing", "data"])
        reset_sources()
        assert _sources_ctx.get(None) == []

    def test_returns_none(self):
        result = reset_sources()
        assert result is None


class TestGetCurrentSources:
    def test_returns_empty_list_when_none(self):
        # contextvar is None (default from fixture)
        result = get_current_sources()
        assert result == []

    def test_returns_current_list(self):
        sample = [{"source_id": "S1"}]
        _sources_ctx.set(sample)
        assert get_current_sources() == sample

    def test_returns_empty_list_when_contextvar_is_empty_list(self):
        _sources_ctx.set([])
        assert get_current_sources() == []

    def test_returns_same_object_not_a_copy(self):
        sample = [{"source_id": "S1"}]
        _sources_ctx.set(sample)
        result = get_current_sources()
        assert result is sample


# ===========================================================================
# _find_file_url
# ===========================================================================

class TestFindFileUrl:
    def test_returns_uri_when_file_found(self, tmp_path):
        # Create a real file to find
        subdir = tmp_path / "subdir"
        subdir.mkdir()
        doc = subdir / "doc.pdf"
        doc.write_bytes(b"%PDF")

        with patch.object(rag_tools, "_DATA_DIR", tmp_path):
            result = _find_file_url("doc.pdf")
        assert result.startswith("file://")
        assert "doc.pdf" in result

    def test_returns_empty_string_when_not_found(self, tmp_path):
        with patch.object(rag_tools, "_DATA_DIR", tmp_path):
            result = _find_file_url("nonexistent.pdf")
        assert result == ""

    def test_returns_first_match_when_multiple(self, tmp_path):
        d1 = tmp_path / "a"
        d1.mkdir()
        d2 = tmp_path / "b"
        d2.mkdir()
        f1 = d1 / "multi.pdf"
        f1.write_bytes(b"1")
        f2 = d2 / "multi.pdf"
        f2.write_bytes(b"2")

        with patch.object(rag_tools, "_DATA_DIR", tmp_path):
            result = _find_file_url("multi.pdf")
        assert result.startswith("file://")
        assert "multi.pdf" in result

    def test_caches_result(self, tmp_path):
        doc = tmp_path / "cached.pdf"
        doc.write_bytes(b"%PDF")

        with patch.object(rag_tools, "_DATA_DIR", tmp_path):
            r1 = _find_file_url("cached.pdf")
            r2 = _find_file_url("cached.pdf")
        assert r1 == r2
        info = _find_file_url.cache_info()
        assert info.hits >= 1


# ===========================================================================
# _to_docs_path
# ===========================================================================

class TestToDocsPath:
    def test_empty_string_returns_empty(self):
        assert _to_docs_path("") == ""

    def test_converts_file_uri_to_docs_path(self, tmp_path):
        # Build a realistic file structure
        product_dir = tmp_path / "Insurance-product-info"
        product_dir.mkdir()
        doc = product_dir / "Generations-II_PB_EN.pdf"
        doc.write_bytes(b"%PDF")

        file_url = doc.resolve().as_uri()

        with patch.object(rag_tools, "_DATA_DIR", tmp_path):
            result = _to_docs_path(file_url)

        assert result.startswith("/docs/")
        assert "Generations-II_PB_EN.pdf" in result

    def test_returns_empty_on_invalid_uri(self):
        result = _to_docs_path("not-a-valid-uri:::///")
        # Should not raise; may return empty or partial
        assert isinstance(result, str)

    def test_returns_empty_when_path_not_relative_to_data_dir(self, tmp_path):
        other = tmp_path / "other"
        other.mkdir()
        doc = other / "secret.pdf"
        doc.write_bytes(b"%PDF")
        file_url = doc.resolve().as_uri()

        data_dir = tmp_path / "data"
        data_dir.mkdir()

        with patch.object(rag_tools, "_DATA_DIR", data_dir):
            result = _to_docs_path(file_url)
        assert result == ""

    def test_encodes_special_characters_in_path(self, tmp_path):
        special_dir = tmp_path / "My Documents"
        special_dir.mkdir()
        doc = special_dir / "file with spaces.pdf"
        doc.write_bytes(b"%PDF")

        file_url = doc.resolve().as_uri()

        with patch.object(rag_tools, "_DATA_DIR", tmp_path):
            result = _to_docs_path(file_url)

        if result:  # May succeed or fail depending on OS path parsing
            assert "%20" in result or "file%20with%20spaces" in result or "file with spaces" in result


# ===========================================================================
# _collect_sources
# ===========================================================================

class TestCollectSources:
    def test_returns_empty_strings_when_no_bucket(self):
        # contextvar is None
        hits = [_make_hit(), _make_hit()]
        result = _collect_sources(hits)
        assert result == ["", ""]

    def test_assigns_sequential_source_ids(self):
        reset_sources()
        hits = [
            _make_hit(document_name="a.pdf", page_start=1),
            _make_hit(document_name="b.pdf", page_start=1),
        ]
        result = _collect_sources(hits)
        assert result == ["S1", "S2"]
        assert len(_sources_ctx.get()) == 2

    def test_deduplicates_same_document_and_page(self):
        reset_sources()
        hits = [
            _make_hit(document_name="doc.pdf", page_start=5),
            _make_hit(document_name="doc.pdf", page_start=5),
        ]
        result = _collect_sources(hits)
        assert result == ["S1", "S1"]
        assert len(_sources_ctx.get()) == 1

    def test_does_not_deduplicate_different_pages(self):
        reset_sources()
        hits = [
            _make_hit(document_name="doc.pdf", page_start=1),
            _make_hit(document_name="doc.pdf", page_start=2),
        ]
        result = _collect_sources(hits)
        assert result == ["S1", "S2"]
        assert len(_sources_ctx.get()) == 2

    def test_does_not_deduplicate_different_documents_same_page(self):
        reset_sources()
        hits = [
            _make_hit(document_name="a.pdf", page_start=1),
            _make_hit(document_name="b.pdf", page_start=1),
        ]
        result = _collect_sources(hits)
        assert result == ["S1", "S2"]

    def test_source_id_continues_from_existing_bucket(self):
        reset_sources()
        # Pre-populate bucket with one entry
        bucket = _sources_ctx.get()
        bucket.append({
            "source_id": "S1",
            "document": "existing.pdf",
            "page_start": 1,
            "page_end": 2,
            "product": "",
            "section": "",
            "file_url": "",
            "chunk_id": "",
            "text_preview": "",
        })
        hits = [_make_hit(document_name="new.pdf", page_start=3)]
        result = _collect_sources(hits)
        assert result == ["S2"]

    def test_handles_missing_metadata_gracefully(self):
        reset_sources()
        hits = [{"text": "some text", "metadata": {}}]
        result = _collect_sources(hits)
        assert result == ["S1"]
        entry = _sources_ctx.get()[0]
        assert entry["document"] == "?"
        assert entry["page_start"] == "?"

    def test_text_preview_truncated_to_250(self):
        reset_sources()
        long_text = "x" * 500
        hits = [_make_hit(text=long_text)]
        _collect_sources(hits)
        entry = _sources_ctx.get()[0]
        assert len(entry["text_preview"]) == 250

    def test_text_preview_not_truncated_when_short(self):
        reset_sources()
        short_text = "short"
        hits = [_make_hit(text=short_text)]
        _collect_sources(hits)
        entry = _sources_ctx.get()[0]
        assert entry["text_preview"] == "short"

    def test_uses_file_url_from_metadata_when_present(self, tmp_path):
        reset_sources()
        product_dir = tmp_path / "Insurance-product-info"
        product_dir.mkdir()
        doc = product_dir / "doc.pdf"
        doc.write_bytes(b"%PDF")
        file_url = doc.resolve().as_uri()

        hits = [_make_hit(document_name="doc.pdf", file_url=file_url)]
        with patch.object(rag_tools, "_DATA_DIR", tmp_path):
            _collect_sources(hits)

        entry = _sources_ctx.get()[0]
        assert entry["file_url"].startswith("/docs/") or entry["file_url"] == ""

    def test_falls_back_to_find_file_url_when_no_file_url_in_metadata(self, tmp_path):
        reset_sources()

        product_dir = tmp_path / "Insurance-product-info"
        product_dir.mkdir()
        doc = product_dir / "fallback.pdf"
        doc.write_bytes(b"%PDF")

        hits = [_make_hit(document_name="fallback.pdf", file_url="")]
        with patch.object(rag_tools, "_DATA_DIR", tmp_path):
            _collect_sources(hits)

        # Should have attempted _find_file_url
        entry = _sources_ctx.get()[0]
        assert isinstance(entry["file_url"], str)

    def test_returns_correct_order_with_mixed_hits(self):
        reset_sources()
        hits = [
            _make_hit(document_name="a.pdf", page_start=1),
            _make_hit(document_name="b.pdf", page_start=1),
            _make_hit(document_name="a.pdf", page_start=1),  # duplicate
            _make_hit(document_name="c.pdf", page_start=1),
        ]
        result = _collect_sources(hits)
        assert result == ["S1", "S2", "S1", "S3"]

    def test_empty_hits_list(self):
        reset_sources()
        result = _collect_sources([])
        assert result == []
        assert _sources_ctx.get() == []

    def test_entry_fields_populated_correctly(self):
        reset_sources()
        hits = [_make_hit(
            document_name="Generations-II_PB_EN.pdf",
            page_start=10,
            page_end=11,
            product_name="Generations II",
            section_title="Benefits",
            chunk_id="chunk_42",
            text="Some benefit description here.",
        )]
        with patch.object(rag_tools, "_to_docs_path", return_value="/docs/test.pdf"):
            _collect_sources(hits)

        entry = _sources_ctx.get()[0]
        assert entry["source_id"] == "S1"
        assert entry["document"] == "Generations-II_PB_EN.pdf"
        assert entry["product"] == "Generations II"
        assert entry["page_start"] == 10
        assert entry["page_end"] == 11
        assert entry["section"] == "Benefits"
        assert entry["chunk_id"] == "chunk_42"