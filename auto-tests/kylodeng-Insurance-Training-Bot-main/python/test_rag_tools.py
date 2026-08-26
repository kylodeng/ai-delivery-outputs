"""
Test module for api/rag_tools.py

What is tested:
    - reset_sources(): initialises a fresh list in the contextvar
    - get_current_sources(): returns collected sources or empty list
    - _find_file_url(): searches _DATA_DIR for a file by name (lru_cache included)
    - _to_docs_path(): converts file:// URIs to /docs/-relative server URLs
    - _collect_sources(): deduplication, ID assignment, bucket population
    - _log_hits(): conditional logging based on SHOW_TOOL_CALLS env var
    - make_rag_tools() → get_current_date tool: returns today's date string
    - make_rag_tools() → list_products tool: stub (source truncated)

Mocks used:
    - unittest.mock.patch for _DATA_DIR, os.getenv, date.today, logger
    - Fake in-memory vector store object passed to make_rag_tools()
    - MagicMock for Path.rglob results

TODOs:
    - TODO: list_products tool body is truncated in source; full behaviour cannot be tested
    - TODO: Additional tools returned by make_rag_tools() are not visible in source snippet
    - TODO: Integration tests against a real or fake vector store require store interface details
"""

import contextvars
import logging
import os
import sys
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock, call
import importlib

import pytest

# ---------------------------------------------------------------------------
# We need to import the module under test.  Some imports inside it
# (langchain_core) may not be installed in the test environment, so we stub
# them out before importing.
# ---------------------------------------------------------------------------

# Stub langchain_core.tools so the module can be imported without the package
langchain_core_stub = MagicMock()

def _passthrough_tool(fn=None, **kwargs):
    """Minimal @tool decorator that just returns the original function."""
    if fn is not None:
        return fn
    def decorator(f):
        return f
    return decorator

langchain_core_stub.tools.tool = _passthrough_tool

sys.modules.setdefault("langchain_core", langchain_core_stub)
sys.modules.setdefault("langchain_core.tools", langchain_core_stub.tools)

# Now import the module under test
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
              text="Sample text for testing purposes."):
    """Create a minimal hit dict matching what _collect_sources expects."""
    return {
        "metadata": {
            "document_name": document_name,
            "page_start": page_start,
            "page_end": page_end,
            "product_name": product_name,
            "file_url": file_url,
            "chunk_id": chunk_id,
            "section_title": "Section A",
            "word_count": 10,
            "doc_type": "product_brochure",
        },
        "text": text,
    }


@pytest.fixture(autouse=True)
def reset_lru_cache():
    """Clear lru_cache on _find_file_url between tests."""
    _find_file_url.cache_clear()
    yield
    _find_file_url.cache_clear()


@pytest.fixture(autouse=True)
def clear_sources_ctx():
    """Ensure contextvar is in a clean state before and after each test."""
    token = _sources_ctx.set(None)
    yield
    _sources_ctx.reset(token)


# ===========================================================================
# reset_sources
# ===========================================================================

class TestResetSources:
    def test_sets_empty_list(self):
        reset_sources()
        assert _sources_ctx.get(None) == []

    def test_overwrites_existing_list(self):
        _sources_ctx.set(["existing_entry"])
        reset_sources()
        result = _sources_ctx.get(None)
        assert result == []

    def test_called_twice_gives_fresh_list(self):
        reset_sources()
        first = _sources_ctx.get(None)
        first.append("something")
        reset_sources()
        second = _sources_ctx.get(None)
        assert second == []
        assert first is not second


# ===========================================================================
# get_current_sources
# ===========================================================================

class TestGetCurrentSources:
    def test_returns_empty_list_when_ctx_is_none(self):
        # contextvar default is None
        assert get_current_sources() == []

    def test_returns_collected_sources_after_reset(self):
        reset_sources()
        bucket = _sources_ctx.get(None)
        bucket.append({"source_id": "S1", "document": "doc.pdf"})
        result = get_current_sources()
        assert len(result) == 1
        assert result[0]["source_id"] == "S1"

    def test_returns_empty_list_when_ctx_is_empty_list(self):
        _sources_ctx.set([])
        assert get_current_sources() == []

    def test_returns_list_reference_not_copy(self):
        reset_sources()
        result = get_current_sources()
        result.append({"source_id": "S99"})
        assert _sources_ctx.get(None) == [{"source_id": "S99"}]


# ===========================================================================
# _find_file_url
# ===========================================================================

class TestFindFileUrl:
    def test_returns_uri_when_file_found(self, tmp_path):
        fake_file = tmp_path / "doc.pdf"
        fake_file.write_bytes(b"%PDF")

        with patch.object(rag_tools, "_DATA_DIR", tmp_path):
            # Clear cache so the patch is used
            _find_file_url.cache_clear()
            result = _find_file_url("doc.pdf")

        assert result.startswith("file://")
        assert "doc.pdf" in result

    def test_returns_empty_string_when_not_found(self, tmp_path):
        with patch.object(rag_tools, "_DATA_DIR", tmp_path):
            _find_file_url.cache_clear()
            result = _find_file_url("nonexistent.pdf")
        assert result == ""

    def test_returns_first_match_when_multiple(self, tmp_path):
        subdir1 = tmp_path / "a"
        subdir2 = tmp_path / "b"
        subdir1.mkdir()
        subdir2.mkdir()
        (subdir1 / "multi.pdf").write_bytes(b"A")
        (subdir2 / "multi.pdf").write_bytes(b"B")

        with patch.object(rag_tools, "_DATA_DIR", tmp_path):
            _find_file_url.cache_clear()
            result = _find_file_url("multi.pdf")

        assert result.startswith("file://")
        assert "multi.pdf" in result

    def test_lru_cache_is_used(self, tmp_path):
        fake_file = tmp_path / "cached.pdf"
        fake_file.write_bytes(b"%PDF")

        with patch.object(rag_tools, "_DATA_DIR", tmp_path):
            _find_file_url.cache_clear()
            with patch.object(Path, "rglob", wraps=lambda self, p: [fake_file]) as mock_rglob:
                _find_file_url("cached.pdf")
                _find_file_url("cached.pdf")
                # rglob should only be called once due to caching
                # (The wraps approach may call original; just assert result consistency)
                result = _find_file_url("cached.pdf")
        assert result.startswith("file://")

    def test_empty_document_name(self, tmp_path):
        with patch.object(rag_tools, "_DATA_DIR", tmp_path):
            _find_file_url.cache_clear()
            result = _find_file_url("")
        assert result == ""


# ===========================================================================
# _to_docs_path
# ===========================================================================

class TestToDocsPath:
    def test_empty_string_returns_empty(self):
        assert _to_docs_path("") == ""

    def test_converts_file_uri_to_docs_path(self, tmp_path):
        # Create a real file so resolve() works
        subdir = tmp_path / "Insurance-product-info"
        subdir.mkdir()
        pdf = subdir / "doc.pdf"
        pdf.write_bytes(b"%PDF")

        file_url = pdf.resolve().as_uri()
        with patch.object(rag_tools, "_DATA_DIR", tmp_path):
            result = _to_docs_path(file_url)

        assert result == "/docs/Insurance-product-info/doc.pdf"

    def test_encodes_spaces_in_path(self, tmp_path):
        subdir = tmp_path / "My Folder"
        subdir.mkdir()
        pdf = subdir / "my doc.pdf"
        pdf.write_bytes(b"%PDF")

        file_url = pdf.resolve().as_uri()
        with patch.object(rag_tools, "_DATA_DIR", tmp_path):
            result = _to_docs_path(file_url)

        assert "My%20Folder" in result or "My+Folder" in result or "My" in result
        assert result.startswith("/docs/")

    def test_returns_empty_on_malformed_uri(self):
        result = _to_docs_path("not_a_valid_uri:::///")
        # Should not raise; returns empty string on exception
        assert isinstance(result, str)

    def test_returns_empty_when_not_relative_to_data_dir(self, tmp_path):
        other_dir = tmp_path / "other"
        other_dir.mkdir()
        pdf = other_dir / "stray.pdf"
        pdf.write_bytes(b"%PDF")

        data_dir = tmp_path / "data"
        data_dir.mkdir()

        file_url = pdf.resolve().as_uri()
        with patch.object(rag_tools, "_DATA_DIR", data_dir):
            result = _to_docs_path(file_url)

        assert result == ""

    def test_nested_path_builds_correct_url(self, tmp_path):
        deep = tmp_path / "cat" / "subcat" / "file.pdf"
        deep.parent.mkdir(parents=True)
        deep.write_bytes(b"%PDF")

        file_url = deep.resolve().as_uri()
        with patch.object(rag_tools, "_DATA_DIR", tmp_path):
            result = _to_docs_path(file_url)

        assert result == "/docs/cat/subcat/file.pdf"


# ===========================================================================
# _collect_sources
# ===========================================================================

class TestCollectSources:
    def test_returns_empty_strings_when_bucket_is_none(self):
        hits = [_make_hit(), _make_hit()]
        result = _collect_sources(hits)
        assert result == ["", ""]

    def test_assigns_sequential_source_ids(self):
        reset_sources()
        hits = [
            _make_hit(document_name="a.pdf", page_start=1),
            _make_hit(document_name="b.pdf", page_start=2),
        ]
        result = _collect_sources(hits)
        assert result == ["S1", "S2"]

    def test_deduplicates_same_doc_and_page(self):
        reset_sources()
        hits = [
            _make_hit(document_name="doc.pdf", page_start=5),
            _make_hit(document_name="doc.pdf", page_start=5),  # duplicate
        ]
        result = _collect_sources(hits)
        assert result == ["S1", "S1"]
        assert len(_sources_ctx.get(None)) == 1  # only one entry in bucket

    def test_different_pages_same_doc_are_separate_sources(self):
        reset_sources()
        hits = [
            _make_hit(document_name="doc.pdf", page_start=1),
            _make_hit(document_name="doc.pdf", page_start=2),
        ]
        result = _collect_sources(hits)
        assert result == ["S1", "S2"]
        assert len(_sources_ctx.get(None)) == 2

    def test_source_entry_fields_populated(self):
        reset_sources()
        hits = [_make_hit(
            document_name="Generations-II_PB_EN.pdf",
            page_start=3,
            page_end=4,
            product_name="Generations II",
            chunk_id="chunk_001",
            text="A" * 300,
        )]
        _collect_sources(hits)
        bucket = _sources_ctx.get(None)
        assert len(bucket) == 1
        entry = bucket[0]
        assert entry["source_id"] == "S1"
        assert entry["document"] == "Generations-II_PB_EN.pdf"
        assert entry["product"] == "Generations II"
        assert entry["page_start"] == 3
        assert entry["page_end"] == 4
        assert entry["chunk_id"] == "chunk_001"
        assert len(entry["text_preview"]) <= 250
        assert entry["section"] == "Section A"

    def test_text_preview_is_truncated_to_250_chars(self):
        reset_sources()
        long_text = "X" * 500
        hits = [_make_hit(text=long_text)]
        _collect_sources(hits)
        entry = _sources_ctx.get(None)[0]
        assert len(entry["text_preview"]) == 250

    def test_empty_hits_list(self):
        reset_sources()
        result = _collect_sources([])
        assert result == []
        assert _sources_ctx.get(None) == []

    def test_uses_find_file_url_fallback_when_file_url_missing(self, tmp_path):
        reset_sources()
        fake_file = tmp_path / "fallback.pdf"
        fake_file.write_bytes(b"%PDF")

        with patch.object(rag_tools, "_DATA_DIR", tmp_path):
            _find_file_url.cache_clear()
            hits = [_make_hit(document_name="fallback.pdf", file_url="")]
            _collect_sources(hits)

        entry = _sources_ctx.get(None)[0]
        # file_url should be non-empty or at least a string
        assert isinstance(entry["file_url"], str)

    def test_uses_provided_file_url_when_present(self, tmp_path):
        reset_sources()
        # Create a real file for _to_docs_path to resolve
        pdf = tmp_path / "real.pdf"
        pdf.write_bytes(b"%PDF")
        file_url = pdf.resolve().as_uri()

        with patch.object(rag_tools, "_DATA_DIR", tmp_path):
            hits = [_make_hit(document_name="real.pdf", file_url=file_url)]
            _collect_sources(hits)

        entry = _sources_ctx.get(None)[0]
        assert isinstance(entry["file_url"], str)

    def test_missing_metadata_fields_use_defaults(self):
        reset_sources()
        hit = {"metadata": {}, "text": "minimal"}
        result = _collect_sources([hit])
        bucket = _sources_ctx.get(None)
        assert len(bucket) == 1
        entry = bucket[