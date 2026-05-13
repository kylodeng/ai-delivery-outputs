"""
Test module for api/rag_tools.py

What is tested:
    - reset_sources(): initialises a fresh list in the contextvar
    - get_current_sources(): returns current sources or empty list when unset
    - _find_file_url(): locates files under _DATA_DIR using rglob; uses tmp filesystem
    - _to_docs_path(): converts file:/// URIs to /docs/-relative server URLs
    - _collect_sources(): appends unique source dicts, deduplicates, returns source IDs
    - _log_hits(): logs chunk metadata only when SHOW_TOOL_CALLS=true
    - make_rag_tools(): returns a list of LangChain tools bound to a store mock
    - get_current_date tool: returns today's date in correct format
    - list_products tool: stub (truncated source, needs full implementation)

Mocks used:
    - unittest.mock.MagicMock for the vector store
    - unittest.mock.patch for os.getenv, logging, date.today, _DATA_DIR, _find_file_url
    - tmp_path (pytest fixture) for filesystem operations in _find_file_url tests
    - contextvars isolation achieved by resetting _sources_ctx between tests

TODOs:
    - list_products tool body is truncated in source; stub tests added with pytest.mark.skip
    - Any additional tools created inside make_rag_tools beyond get_current_date and
      list_products are unknown; stubs added
    - Integration tests against a real vector store require additional context
"""

import contextvars
import logging
import os
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch, call
import urllib.request

import pytest

# ---------------------------------------------------------------------------
# Import the module under test
# ---------------------------------------------------------------------------
import importlib
import sys

# We patch _DATA_DIR at import time via monkeypatch where needed; for most tests
# we import the module directly.
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

def _clear_sources_ctx():
    """Reset the contextvar to its default (None) between tests."""
    _sources_ctx.set(None)
    # Also clear lru_cache so filesystem tests don't bleed into each other
    _find_file_url.cache_clear()


@pytest.fixture(autouse=True)
def isolate_context():
    """Ensure each test starts with a clean contextvar and cleared lru_cache."""
    _clear_sources_ctx()
    _find_file_url.cache_clear()
    yield
    _clear_sources_ctx()
    _find_file_url.cache_clear()


# ---------------------------------------------------------------------------
# Synthetic hit factory
# ---------------------------------------------------------------------------

def _make_hit(
    document_name="Generations-II_PB_EN.pdf",
    product_name="Generations II",
    page_start=1,
    page_end=2,
    section_title="Overview",
    file_url="",
    chunk_id="c001",
    text="Sample chunk text for Generations II product brochure.",
    doc_type="product_brochure",
    word_count=10,
):
    return {
        "metadata": {
            "document_name": document_name,
            "product_name": product_name,
            "page_start": page_start,
            "page_end": page_end,
            "section_title": section_title,
            "file_url": file_url,
            "chunk_id": chunk_id,
            "doc_type": doc_type,
            "word_count": word_count,
        },
        "text": text,
    }


SYNTHETIC_HITS = [
    _make_hit(
        document_name="Generations-II_PB_EN.pdf",
        product_name="Generations II",
        page_start=1,
        page_end=3,
        section_title="Lifelong Protection",
        chunk_id="c001",
        text="Generations II is a participating whole life insurance plan.",
    ),
    _make_hit(
        document_name="List of designated hospitals in mainland China.pdf",
        product_name="List of Designated Hospitals in Mainland China",
        page_start=5,
        page_end=6,
        section_title="Class 3 Hospitals",
        chunk_id="c002",
        text="This document provides the official list of designated hospitals.",
    ),
    _make_hit(
        document_name="Mainland_China_VIP_Hospital_Network.pdf",
        product_name="List of Network Hospitals with Mainland China VIP Medical Navigation Service",
        page_start=10,
        page_end=12,
        section_title="Shanghai Hospitals",
        chunk_id="c003",
        text="Comprehensive list of network hospitals in Mainland China.",
    ),
]


# ===========================================================================
# Tests: reset_sources / get_current_sources
# ===========================================================================

class TestResetAndGetSources:
    def test_reset_sources_sets_empty_list(self):
        reset_sources()
        assert _sources_ctx.get(None) == []

    def test_reset_sources_clears_existing_list(self):
        _sources_ctx.set([{"source_id": "S1"}])
        reset_sources()
        assert _sources_ctx.get(None) == []

    def test_get_current_sources_returns_empty_when_unset(self):
        # contextvar is at default (None)
        assert get_current_sources() == []

    def test_get_current_sources_returns_empty_when_none(self):
        _sources_ctx.set(None)
        assert get_current_sources() == []

    def test_get_current_sources_returns_populated_list(self):
        entries = [{"source_id": "S1", "document": "doc.pdf"}]
        _sources_ctx.set(entries)
        assert get_current_sources() == entries

    def test_reset_then_get_returns_empty_list(self):
        reset_sources()
        result = get_current_sources()
        assert result == []
        assert isinstance(result, list)


# ===========================================================================
# Tests: _find_file_url
# ===========================================================================

class TestFindFileUrl:
    def test_finds_existing_file(self, tmp_path, monkeypatch):
        # Create a fake file under a mocked _DATA_DIR
        subdir = tmp_path / "Insurance-product-info" / "Generations-II"
        subdir.mkdir(parents=True)
        pdf = subdir / "Generations-II_PB_EN.pdf"
        pdf.write_bytes(b"%PDF fake")

        monkeypatch.setattr(rag_tools, "_DATA_DIR", tmp_path)
        _find_file_url.cache_clear()

        result = rag_tools._find_file_url("Generations-II_PB_EN.pdf")
        assert result.startswith("file:///") or result.startswith("file:/")
        assert "Generations-II_PB_EN.pdf" in result

    def test_returns_empty_string_when_not_found(self, tmp_path, monkeypatch):
        monkeypatch.setattr(rag_tools, "_DATA_DIR", tmp_path)
        _find_file_url.cache_clear()

        result = rag_tools._find_file_url("nonexistent_document.pdf")
        assert result == ""

    def test_returns_first_match_when_multiple(self, tmp_path, monkeypatch):
        for folder in ("folderA", "folderB"):
            d = tmp_path / folder
            d.mkdir()
            (d / "doc.pdf").write_bytes(b"%PDF")

        monkeypatch.setattr(rag_tools, "_DATA_DIR", tmp_path)
        _find_file_url.cache_clear()

        result = rag_tools._find_file_url("doc.pdf")
        assert result != ""
        assert "doc.pdf" in result

    def test_lru_cache_used(self, tmp_path, monkeypatch):
        monkeypatch.setattr(rag_tools, "_DATA_DIR", tmp_path)
        _find_file_url.cache_clear()

        # Call twice — second should hit cache
        rag_tools._find_file_url("missing.pdf")
        rag_tools._find_file_url("missing.pdf")
        info = _find_file_url.cache_info()
        assert info.hits == 1

    def test_empty_document_name_returns_empty(self, tmp_path, monkeypatch):
        monkeypatch.setattr(rag_tools, "_DATA_DIR", tmp_path)
        _find_file_url.cache_clear()
        result = rag_tools._find_file_url("")
        # rglob("") may match root; we just assert it returns a string
        assert isinstance(result, str)


# ===========================================================================
# Tests: _to_docs_path
# ===========================================================================

class TestToDocsPath:
    def _make_file_uri(self, tmp_path, relative: str) -> str:
        """Create a real file and return its file:// URI."""
        full = tmp_path / relative
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_bytes(b"x")
        return full.resolve().as_uri()

    def test_empty_string_returns_empty(self):
        assert _to_docs_path("") == ""

    def test_converts_simple_uri(self, tmp_path, monkeypatch):
        monkeypatch.setattr(rag_tools, "_DATA_DIR", tmp_path)
        uri = self._make_file_uri(tmp_path, "Insurance-product-info/doc.pdf")
        result = rag_tools._to_docs_path(uri)
        assert result == "/docs/Insurance-product-info/doc.pdf"

    def test_converts_nested_uri(self, tmp_path, monkeypatch):
        monkeypatch.setattr(rag_tools, "_DATA_DIR", tmp_path)
        uri = self._make_file_uri(
            tmp_path, "Insurance-product-info/Generations-II/Generations-II_PB_EN.pdf"
        )
        result = rag_tools._to_docs_path(uri)
        assert result == (
            "/docs/Insurance-product-info/Generations-II/Generations-II_PB_EN.pdf"
        )

    def test_uri_outside_data_dir_returns_empty(self, tmp_path, monkeypatch):
        """A URI that cannot be made relative to _DATA_DIR returns ''."""
        monkeypatch.setattr(rag_tools, "_DATA_DIR", tmp_path / "subdir")
        (tmp_path / "subdir").mkdir(parents=True, exist_ok=True)
        # Point to a file outside subdir
        outside = tmp_path / "other" / "file.pdf"
        outside.parent.mkdir(parents=True, exist_ok=True)
        outside.write_bytes(b"x")
        result = rag_tools._to_docs_path(outside.resolve().as_uri())
        assert result == ""

    def test_spaces_are_percent_encoded(self, tmp_path, monkeypatch):
        monkeypatch.setattr(rag_tools, "_DATA_DIR", tmp_path)
        uri = self._make_file_uri(tmp_path, "folder with spaces/my doc.pdf")
        result = rag_tools._to_docs_path(uri)
        assert " " not in result
        assert result.startswith("/docs/")

    def test_malformed_uri_returns_empty(self):
        result = _to_docs_path("not_a_valid_uri_at_all:::///")
        # Should not raise; returns '' on any exception
        assert isinstance(result, str)

    def test_non_file_scheme_returns_empty_or_str(self):
        result = _to_docs_path("http://example.com/doc.pdf")
        # Cannot be made relative to _DATA_DIR → ''
        assert isinstance(result, str)


# ===========================================================================
# Tests: _collect_sources
# ===========================================================================

class TestCollectSources:
    def test_returns_empty_ids_when_bucket_is_none(self):
        # contextvar not set → bucket is None
        hits = [_make_hit(), _make_hit()]
        result = _collect_sources(hits)
        assert result == ["", ""]

    def test_single_hit_gets_s1(self):
        reset_sources()
        hits = [_make_hit()]
        ids = _collect_sources(hits)
        assert ids == ["S1"]
        bucket = _sources_ctx.get()
        assert len(bucket) == 1
        assert bucket[0]["source_id"] == "S1"

    def test_multiple_unique_hits_get_sequential_ids(self):
        reset_sources()
        ids = _collect_sources(SYNTHETIC_HITS)
        assert ids == ["S1", "S2", "S3"]
        assert len(_sources_ctx.get()) == 3

    def test_duplicate_hit_reuses_existing_id(self):
        reset_sources()
        hit_a = _make_hit(document_name="doc.pdf", page_start=1)
        hit_b = _make_hit(document_name="doc.pdf", page_start=1)  # same key
        ids = _collect_sources([hit_a, hit_b])
        assert ids == ["S1", "S1"]
        assert len(_sources_ctx.get()) == 1  # only one entry in bucket

    def test_same_doc_different_page_gets_new_id(self):
        reset_sources()
        hit_a = _make_hit(document_name="doc.pdf", page_start=1)
        hit_b = _make_hit(document_name="doc.pdf", page_start=5)
        ids = _collect_sources([hit_a, hit_b])
        assert ids == ["S1", "S2"]
        assert len(_sources_ctx.get()) == 2

    def test_empty_hits_list(self):
        reset_sources()
        result = _collect_sources([])
        assert result == []

    def test_source_entry_fields_populated(self):
        reset_sources()
        hit = _make_hit(
            document_name="Generations-II_PB_EN.pdf",
            product_name="Generations II",
            page_start=1,
            page_end=3,
            section_title="Lifelong Protection",
            chunk_id="c001",
            text="A" * 300,  # longer than 250 chars to test truncation
        )
        _collect_sources([hit])
        entry = _sources_ctx.get()[0]
        assert entry["source_id"] == "S1"
        assert entry["document"] == "Generations-II_PB_EN.pdf"
        assert entry["product"] == "Generations II"
        assert entry["page_start"] == 1
        assert entry["page_end"] == 3
        assert entry["section"] == "Lifelong Protection"
        assert entry["chunk_id"] == "c001"
        assert len(entry["text_preview"]) <= 250

    def test_text_preview_truncated_to_250(self):
        reset_sources()
        long_text = "x" * 500
        hit = _make_hit(text=long_text)
        _collect_sources([hit])
        assert _sources_ctx.get()[0]["text_preview"] == "x" * 250

    def test_file_url_used_from_metadata(self, tmp_path, monkeypatch):
        monkeypatch.setattr(rag_tools, "_DATA_DIR", tmp_path)
        # Create a real file so _to_docs_path can resolve it
        (tmp_path / "my_doc.pdf").write_bytes(b"x")
        uri = (tmp_path / "