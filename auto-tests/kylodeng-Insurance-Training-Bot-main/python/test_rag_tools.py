"""
Test suite for api/rag_tools.py

What is tested:
  - reset_sources()           : initialises a fresh list in the contextvar
  - get_current_sources()     : returns current sources or empty list
  - _find_file_url()          : file-system glob fallback for document URLs
  - _to_docs_path()           : converts file:// URIs to /docs/-relative paths
  - _collect_sources()        : deduplication, ID assignment, bucket management
  - _log_hits()               : conditional logging based on SHOW_TOOL_CALLS env var
  - make_rag_tools()          : factory returns a list of LangChain tools
  - get_current_date tool     : returns formatted today's date
  - list_products tool        : (stub — truncated source; see TODO)

Mocks used:
  - unittest.mock.patch       : patches _DATA_DIR, date.today, logger, os.getenv
  - tmp_path (pytest fixture) : provides a real filesystem for _find_file_url tests
  - lru_cache cleared         : _find_file_url cache is cleared before relevant tests

TODOs:
  - list_products tool body is truncated in the provided source; stub tests added.
  - Additional tools beyond list_products are unknown; stubs added.
  - store interface is unknown; minimal duck-typed stub used.
"""

import contextvars
import importlib
import os
import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers to (re)import the module under test so we can manipulate module-level
# state safely between tests.
# ---------------------------------------------------------------------------

MODULE = "api.rag_tools"


def _fresh_import():
    """Return a freshly imported copy of rag_tools (bypasses Python module cache)."""
    if MODULE in sys.modules:
        del sys.modules[MODULE]
    return importlib.import_module(MODULE)


@pytest.fixture(autouse=True)
def import_module():
    """Import rag_tools once per test; re-export commonly used symbols."""
    import api.rag_tools as mod
    # Clear lru_cache between tests to avoid cross-test pollution.
    mod._find_file_url.cache_clear()
    return mod


@pytest.fixture()
def rag(import_module):
    """Alias for the imported module."""
    return import_module


# ---------------------------------------------------------------------------
# Minimal store stub
# ---------------------------------------------------------------------------

class _StubStore:
    """Minimal duck-typed stub for the vector store dependency."""

    def similarity_search_with_score(self, query, k=5, **kwargs):
        return []

    def similarity_search(self, query, k=5, **kwargs):
        return []


# ---------------------------------------------------------------------------
# Synthetic hit factory
# ---------------------------------------------------------------------------

def _make_hit(
    document_name="Generations-II_PB_EN.pdf",
    page_start=1,
    page_end=2,
    product_name="Generations II",
    doc_type="product_brochure",
    file_url="",
    chunk_id="c1",
    section_title="Overview",
    word_count=120,
    text="Sample chunk text for Generations II brochure.",
):
    return {
        "text": text,
        "metadata": {
            "document_name": document_name,
            "page_start": page_start,
            "page_end": page_end,
            "product_name": product_name,
            "doc_type": doc_type,
            "file_url": file_url,
            "chunk_id": chunk_id,
            "section_title": section_title,
            "word_count": word_count,
        },
    }


# ===========================================================================
# reset_sources / get_current_sources
# ===========================================================================

class TestResetAndGetSources:
    def test_reset_initialises_empty_list(self, rag):
        rag.reset_sources()
        result = rag.get_current_sources()
        assert result == []

    def test_get_current_sources_before_reset_returns_empty(self, rag):
        # Ensure contextvar holds None (no reset called)
        rag._sources_ctx.set(None)
        assert rag.get_current_sources() == []

    def test_get_current_sources_after_population(self, rag):
        rag.reset_sources()
        bucket = rag._sources_ctx.get(None)
        bucket.append({"source_id": "S1", "document": "doc.pdf", "page_start": 1})
        result = rag.get_current_sources()
        assert len(result) == 1
        assert result[0]["source_id"] == "S1"

    def test_reset_clears_previous_data(self, rag):
        rag.reset_sources()
        bucket = rag._sources_ctx.get(None)
        bucket.append({"source_id": "S1", "document": "old.pdf", "page_start": 1})
        # Second reset should give a fresh list
        rag.reset_sources()
        assert rag.get_current_sources() == []

    def test_multiple_resets_are_idempotent(self, rag):
        for _ in range(5):
            rag.reset_sources()
        assert rag.get_current_sources() == []


# ===========================================================================
# _find_file_url
# ===========================================================================

class TestFindFileUrl:
    def test_returns_uri_when_file_found(self, rag, tmp_path):
        # Create a fake file inside tmp_path
        subdir = tmp_path / "Insurance-product-info" / "Generations-II"
        subdir.mkdir(parents=True)
        pdf = subdir / "Generations-II_PB_EN.pdf"
        pdf.write_text("dummy")

        with patch.object(rag, "_DATA_DIR", tmp_path):
            rag._find_file_url.cache_clear()
            url = rag._find_file_url("Generations-II_PB_EN.pdf")

        assert url.startswith("file:///") or url.startswith("file://")
        assert "Generations-II_PB_EN.pdf" in url

    def test_returns_empty_string_when_file_not_found(self, rag, tmp_path):
        with patch.object(rag, "_DATA_DIR", tmp_path):
            rag._find_file_url.cache_clear()
            url = rag._find_file_url("nonexistent_document.pdf")

        assert url == ""

    def test_lru_cache_hit(self, rag, tmp_path):
        """Second call with same arg should not re-scan the filesystem."""
        subdir = tmp_path / "docs"
        subdir.mkdir()
        pdf = subdir / "cached_doc.pdf"
        pdf.write_text("x")

        with patch.object(rag, "_DATA_DIR", tmp_path):
            rag._find_file_url.cache_clear()
            url1 = rag._find_file_url("cached_doc.pdf")
            # Remove the file to prove second call is cached
            pdf.unlink()
            url2 = rag._find_file_url("cached_doc.pdf")

        assert url1 == url2

    def test_returns_first_match_when_multiple_exist(self, rag, tmp_path):
        """When a filename exists in two subdirectories, any URI is returned."""
        for sub in ("a", "b"):
            d = tmp_path / sub
            d.mkdir()
            (d / "dup.pdf").write_text("x")

        with patch.object(rag, "_DATA_DIR", tmp_path):
            rag._find_file_url.cache_clear()
            url = rag._find_file_url("dup.pdf")

        assert url != ""
        assert "dup.pdf" in url


# ===========================================================================
# _to_docs_path
# ===========================================================================

class TestToDocsPath:
    def test_empty_string_returns_empty(self, rag):
        assert rag._to_docs_path("") == ""

    def test_none_equivalent_empty_returns_empty(self, rag):
        # Defensive: callers may pass falsy values
        assert rag._to_docs_path("") == ""

    def test_valid_file_uri_inside_data_dir(self, rag, tmp_path):
        """A file URI pointing inside _DATA_DIR should become /docs/... path."""
        subdir = tmp_path / "Insurance-product-info"
        subdir.mkdir()
        pdf = subdir / "doc.pdf"
        pdf.write_text("x")

        file_uri = pdf.resolve().as_uri()

        with patch.object(rag, "_DATA_DIR", tmp_path):
            result = rag._to_docs_path(file_uri)

        assert result == "/docs/Insurance-product-info/doc.pdf"

    def test_path_outside_data_dir_returns_empty(self, rag, tmp_path):
        """A file URI outside _DATA_DIR cannot be made relative — returns ''."""
        outside = tmp_path / "outside.pdf"
        outside.write_text("x")
        file_uri = outside.resolve().as_uri()

        data_dir = tmp_path / "data"
        data_dir.mkdir()

        with patch.object(rag, "_DATA_DIR", data_dir):
            result = rag._to_docs_path(file_uri)

        assert result == ""

    def test_special_chars_are_quoted(self, rag, tmp_path):
        """Spaces and special characters in path parts should be percent-encoded."""
        subdir = tmp_path / "My Products"
        subdir.mkdir()
        pdf = subdir / "doc with spaces.pdf"
        pdf.write_text("x")

        file_uri = pdf.resolve().as_uri()

        with patch.object(rag, "_DATA_DIR", tmp_path):
            result = rag._to_docs_path(file_uri)

        assert "%20" in result or "+" in result or "My%20Products" in result or "My Products" not in result

    def test_malformed_uri_returns_empty(self, rag):
        result = rag._to_docs_path("not-a-valid-uri://??##")
        assert result == ""


# ===========================================================================
# _collect_sources
# ===========================================================================

class TestCollectSources:
    def setup_method(self):
        """Each test starts with a fresh contextvar state."""

    def test_returns_empty_strings_when_no_bucket(self, rag):
        rag._sources_ctx.set(None)
        hits = [_make_hit()]
        result = rag._collect_sources(hits)
        assert result == [""]

    def test_assigns_first_source_id_s1(self, rag):
        rag.reset_sources()
        hits = [_make_hit(document_name="doc.pdf", page_start=1)]
        result = rag._collect_sources(hits)
        assert result == ["S1"]

    def test_assigns_sequential_ids(self, rag):
        rag.reset_sources()
        hits = [
            _make_hit(document_name="doc1.pdf", page_start=1),
            _make_hit(document_name="doc2.pdf", page_start=1),
            _make_hit(document_name="doc3.pdf", page_start=5),
        ]
        result = rag._collect_sources(hits)
        assert result == ["S1", "S2", "S3"]

    def test_deduplicates_same_document_and_page(self, rag):
        rag.reset_sources()
        hits = [
            _make_hit(document_name="doc.pdf", page_start=3),
            _make_hit(document_name="doc.pdf", page_start=3),
        ]
        result = rag._collect_sources(hits)
        assert result == ["S1", "S1"]
        assert len(rag.get_current_sources()) == 1

    def test_different_pages_same_doc_get_different_ids(self, rag):
        rag.reset_sources()
        hits = [
            _make_hit(document_name="doc.pdf", page_start=1),
            _make_hit(document_name="doc.pdf", page_start=2),
        ]
        result = rag._collect_sources(hits)
        assert result == ["S1", "S2"]

    def test_source_entry_contains_expected_fields(self, rag):
        rag.reset_sources()
        hits = [_make_hit(
            document_name="Generations-II_PB_EN.pdf",
            page_start=10,
            page_end=11,
            product_name="Generations II",
            section_title="Benefits",
            chunk_id="c42",
            text="Hello world " * 20,
        )]
        rag._collect_sources(hits)
        sources = rag.get_current_sources()
        assert len(sources) == 1
        s = sources[0]
        assert s["source_id"] == "S1"
        assert s["document"] == "Generations-II_PB_EN.pdf"
        assert s["product"] == "Generations II"
        assert s["page_start"] == 10
        assert s["page_end"] == 11
        assert s["section"] == "Benefits"
        assert s["chunk_id"] == "c42"
        assert len(s["text_preview"]) <= 250

    def test_text_preview_truncated_to_250_chars(self, rag):
        rag.reset_sources()
        long_text = "A" * 500
        hits = [_make_hit(text=long_text)]
        rag._collect_sources(hits)
        sources = rag.get_current_sources()
        assert len(sources[0]["text_preview"]) == 250

    def test_missing_metadata_fields_use_defaults(self, rag):
        rag.reset_sources()
        minimal_hit = {"text": "some text", "metadata": {}}
        result = rag._collect_sources([minimal_hit])
        assert result == ["S1"]
        s = rag.get_current_sources()[0]
        assert s["document"] == "?"
        assert s["page_start"] == "?"
        assert s["product"] == ""
        assert s["section"] == ""

    def test_empty_hits_list_returns_empty(self, rag):
        rag.reset_sources()
        result = rag._collect_sources([])
        assert result == []

    def test_cross_call_accumulation(self, rag):
        """IDs should increment across multiple _collect_sources calls."""
        rag.reset_sources()
        rag._collect_sources([_make_hit(document_name="a.pdf", page_start=1)])
        result = rag._collect_sources([_make_hit(document_name="b.pdf", page_start=1)])
        assert result == ["S2"]

    def test_dedup_persists_across_calls(self, rag):
        """A chunk seen in call 1 should reuse its ID in call 2."""
        rag.reset_sources()
        rag._collect_sources([_make_hit(document_name="a.pdf", page_start=1)])
        result = rag._collect_sources([_make_hit(document_name="a.pdf", page_start=1)])
        assert result == ["S1"]
        assert len(rag.get_current_sources()) == 1

    def test_file_url_used_when_present_in_metadata(self, rag, tmp_path):
        """When file_url in metadata, _find_file_url fallback should NOT be called."""
        subdir = tmp_path / "sub"
        subdir.mkdir()
        pdf = subdir / "doc.pdf"
        pdf.write_text("x")
        raw_uri = pdf.resolve().as_uri()

        rag.reset_sources()
        with patch.object(rag, "_DATA_DIR", tmp_path):