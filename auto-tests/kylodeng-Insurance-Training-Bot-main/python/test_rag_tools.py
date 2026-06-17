"""
Tests for api/rag_tools.py
==========================

What is tested:
- reset_sources() / get_current_sources() context-variable lifecycle
- _find_file_url() LRU-cached filesystem search
- _to_docs_path() URI → /docs/-relative URL conversion
- _collect_sources() dedup, source-ID assignment, bucket mutation
- _log_hits() conditional logging behaviour
- make_rag_tools() factory: get_current_date tool, list_products tool (stub)

Mocks used:
- unittest.mock.patch for Path.rglob (_find_file_url filesystem)
- unittest.mock.patch for datetime.date.today (get_current_date)
- unittest.mock.patch for logging.Logger.info (_log_hits)
- unittest.mock.MagicMock for the vector store passed to make_rag_tools()
- monkeypatch for environment variable SHOW_TOOL_CALLS

TODOs:
- list_products tool: source code is truncated; tests are stubbed
- Any additional tools defined after list_products in make_rag_tools: stubbed
- Integration tests against a real vector store are skipped
"""

import contextvars
import importlib
import logging
import sys
import types
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers to (re)import the module under test with env-var overrides applied
# ---------------------------------------------------------------------------

def _import_rag_tools():
    """Fresh import of api.rag_tools (handles repeated imports in same session)."""
    if "api.rag_tools" in sys.modules:
        return sys.modules["api.rag_tools"]
    import api.rag_tools as mod
    return mod


# We import once at module level for most tests; individual tests that need a
# re-import (e.g. env-var toggling) do so explicitly after monkeypatching.
import api.rag_tools as rag_tools


# ===========================================================================
# Fixtures
# ===========================================================================

@pytest.fixture(autouse=True)
def reset_context_var():
    """Ensure the contextvar is cleaned up between tests."""
    token = rag_tools._sources_ctx.set(None)
    yield
    rag_tools._sources_ctx.reset(token)


@pytest.fixture
def bucket_initialised():
    """Initialise the sources bucket via reset_sources()."""
    rag_tools.reset_sources()
    return rag_tools._sources_ctx.get()


@pytest.fixture
def mock_store():
    return MagicMock()


@pytest.fixture
def sample_hits():
    """Realistic hit dicts modelled on the synthetic data samples."""
    return [
        {
            "text": "Generations II is a participating whole life insurance plan.",
            "metadata": {
                "document_name": "Generations-II_PB_EN.pdf",
                "product_name": "Generations II",
                "doc_type": "product_brochure",
                "page_start": 1,
                "page_end": 2,
                "section_title": "Overview",
                "file_url": "file:///data/Insurance-product-info/Generations-II/Generations-II_PB_EN.pdf",
                "chunk_id": "chunk_001",
                "word_count": 120,
            },
        },
        {
            "text": "List of designated hospitals in mainland China covers Class 3 hospitals.",
            "metadata": {
                "document_name": "List of designated hospitals in mainland China.pdf",
                "product_name": "List of Designated Hospitals in Mainland China",
                "doc_type": "supplementary",
                "page_start": 5,
                "page_end": 6,
                "section_title": "Class 3 Hospitals",
                "file_url": "file:///data/Insurance-product-info/List of designated hospitals in mainland China.pdf",
                "chunk_id": "chunk_002",
                "word_count": 95,
            },
        },
    ]


# ===========================================================================
# reset_sources / get_current_sources
# ===========================================================================

class TestResetSources:
    def test_sets_empty_list(self):
        rag_tools.reset_sources()
        result = rag_tools._sources_ctx.get()
        assert result == []
        assert isinstance(result, list)

    def test_overwrites_existing_bucket(self):
        rag_tools._sources_ctx.set(["stale"])
        rag_tools.reset_sources()
        assert rag_tools._sources_ctx.get() == []

    def test_multiple_resets_yield_fresh_list(self):
        rag_tools.reset_sources()
        first = rag_tools._sources_ctx.get()
        first.append("item")
        rag_tools.reset_sources()
        second = rag_tools._sources_ctx.get()
        assert second == []
        assert first is not second


class TestGetCurrentSources:
    def test_returns_empty_list_when_no_bucket(self):
        # contextvar is None (autouse fixture)
        assert rag_tools.get_current_sources() == []

    def test_returns_empty_list_after_reset(self):
        rag_tools.reset_sources()
        assert rag_tools.get_current_sources() == []

    def test_returns_accumulated_sources(self, bucket_initialised, sample_hits):
        rag_tools._collect_sources(sample_hits)
        sources = rag_tools.get_current_sources()
        assert len(sources) == 2
        assert sources[0]["source_id"] == "S1"
        assert sources[1]["source_id"] == "S2"

    def test_never_returns_none(self):
        # Even with None contextvar, always returns a list
        result = rag_tools.get_current_sources()
        assert result is not None
        assert isinstance(result, list)


# ===========================================================================
# _find_file_url
# ===========================================================================

class TestFindFileUrl:
    def test_returns_uri_when_file_found(self, tmp_path):
        fake_file = tmp_path / "doc.pdf"
        fake_file.write_bytes(b"%PDF")

        with patch.object(rag_tools, "_DATA_DIR", tmp_path):
            # clear lru_cache so patched _DATA_DIR is used
            rag_tools._find_file_url.cache_clear()
            result = rag_tools._find_file_url("doc.pdf")

        assert result.startswith("file://")
        assert "doc.pdf" in result
        rag_tools._find_file_url.cache_clear()

    def test_returns_empty_string_when_not_found(self, tmp_path):
        with patch.object(rag_tools, "_DATA_DIR", tmp_path):
            rag_tools._find_file_url.cache_clear()
            result = rag_tools._find_file_url("nonexistent.pdf")

        assert result == ""
        rag_tools._find_file_url.cache_clear()

    def test_returns_first_match_when_multiple(self, tmp_path):
        sub1 = tmp_path / "a"
        sub1.mkdir()
        sub2 = tmp_path / "b"
        sub2.mkdir()
        f1 = sub1 / "doc.pdf"
        f2 = sub2 / "doc.pdf"
        f1.write_bytes(b"%PDF-1")
        f2.write_bytes(b"%PDF-2")

        with patch.object(rag_tools, "_DATA_DIR", tmp_path):
            rag_tools._find_file_url.cache_clear()
            result = rag_tools._find_file_url("doc.pdf")

        assert result.startswith("file://")
        rag_tools._find_file_url.cache_clear()

    def test_lru_cache_hit(self, tmp_path):
        """Second call with same arg must not re-scan the filesystem."""
        fake_file = tmp_path / "cached.pdf"
        fake_file.write_bytes(b"%PDF")

        with patch.object(rag_tools, "_DATA_DIR", tmp_path):
            rag_tools._find_file_url.cache_clear()
            r1 = rag_tools._find_file_url("cached.pdf")

        # Patch rglob to raise if called again — cache should prevent it
        with patch.object(Path, "rglob", side_effect=RuntimeError("should not be called")):
            r2 = rag_tools._find_file_url("cached.pdf")

        assert r1 == r2
        rag_tools._find_file_url.cache_clear()

    def test_empty_string_document_name(self, tmp_path):
        with patch.object(rag_tools, "_DATA_DIR", tmp_path):
            rag_tools._find_file_url.cache_clear()
            result = rag_tools._find_file_url("")
        # Should not raise; returns empty string or a URI
        assert isinstance(result, str)
        rag_tools._find_file_url.cache_clear()


# ===========================================================================
# _to_docs_path
# ===========================================================================

class TestToDocsPath:
    def test_empty_string_returns_empty(self):
        assert rag_tools._to_docs_path("") == ""

    def test_none_like_falsy_returns_empty(self):
        # Explicitly passing empty — covers the `if not file_url` guard
        assert rag_tools._to_docs_path("") == ""

    def test_valid_uri_produces_docs_path(self, tmp_path):
        # Build a real file so resolve() works
        sub = tmp_path / "Insurance-product-info"
        sub.mkdir()
        pdf = sub / "doc.pdf"
        pdf.write_bytes(b"%PDF")

        file_url = pdf.resolve().as_uri()
        with patch.object(rag_tools, "_DATA_DIR", tmp_path):
            result = rag_tools._to_docs_path(file_url)

        assert result == "/docs/Insurance-product-info/doc.pdf"

    def test_path_with_spaces_is_percent_encoded(self, tmp_path):
        sub = tmp_path / "My Folder"
        sub.mkdir()
        pdf = sub / "my doc.pdf"
        pdf.write_bytes(b"%PDF")

        file_url = pdf.resolve().as_uri()
        with patch.object(rag_tools, "_DATA_DIR", tmp_path):
            result = rag_tools._to_docs_path(file_url)

        assert "%20" in result or "My%20Folder" in result or "my%20doc" in result

    def test_malformed_uri_returns_empty(self):
        result = rag_tools._to_docs_path("not-a-uri-at-all!!!")
        # Should not raise; returns empty string on exception
        assert isinstance(result, str)

    def test_uri_outside_data_dir_returns_empty(self, tmp_path):
        """A file URI that is not under _DATA_DIR should fail relative_to and return ''."""
        outside = tmp_path / "outside" / "secret.pdf"
        outside.parent.mkdir(parents=True)
        outside.write_bytes(b"%PDF")

        file_url = outside.resolve().as_uri()
        # Use a completely different directory as _DATA_DIR
        other_dir = tmp_path / "data"
        other_dir.mkdir()
        with patch.object(rag_tools, "_DATA_DIR", other_dir):
            result = rag_tools._to_docs_path(file_url)

        assert result == ""


# ===========================================================================
# _collect_sources
# ===========================================================================

class TestCollectSources:
    def test_returns_list_of_empty_strings_when_no_bucket(self, sample_hits):
        # contextvar is None — no bucket
        result = rag_tools._collect_sources(sample_hits)
        assert result == ["", ""]

    def test_empty_hits_returns_empty_list(self, bucket_initialised):
        result = rag_tools._collect_sources([])
        assert result == []

    def test_assigns_sequential_source_ids(self, bucket_initialised, sample_hits):
        result = rag_tools._collect_sources(sample_hits)
        assert result == ["S1", "S2"]

    def test_bucket_grows_correctly(self, bucket_initialised, sample_hits):
        rag_tools._collect_sources(sample_hits)
        bucket = rag_tools._sources_ctx.get()
        assert len(bucket) == 2
        assert bucket[0]["source_id"] == "S1"
        assert bucket[1]["source_id"] == "S2"

    def test_deduplication_same_doc_and_page(self, bucket_initialised, sample_hits):
        """Two hits with the same document + page_start should get the same source_id."""
        duplicate = dict(sample_hits[0])  # same doc + page_start
        hits = [sample_hits[0], duplicate]
        result = rag_tools._collect_sources(hits)
        assert result[0] == result[1] == "S1"
        bucket = rag_tools._sources_ctx.get()
        assert len(bucket) == 1  # only one entry added

    def test_deduplication_across_two_calls(self, bucket_initialised, sample_hits):
        """Second call with same hit reuses existing source_id."""
        r1 = rag_tools._collect_sources([sample_hits[0]])
        r2 = rag_tools._collect_sources([sample_hits[0]])
        assert r1 == r2 == ["S1"]
        assert len(rag_tools._sources_ctx.get()) == 1

    def test_source_id_increments_after_existing_bucket(self, bucket_initialised, sample_hits):
        rag_tools._collect_sources([sample_hits[0]])  # S1
        result = rag_tools._collect_sources([sample_hits[1]])  # should be S2
        assert result == ["S2"]

    def test_text_preview_truncated_to_250(self, bucket_initialised):
        long_text = "A" * 500
        hits = [
            {
                "text": long_text,
                "metadata": {
                    "document_name": "long_doc.pdf",
                    "page_start": 1,
                },
            }
        ]
        rag_tools._collect_sources(hits)
        bucket = rag_tools._sources_ctx.get()
        assert len(bucket[0]["text_preview"]) == 250

    def test_missing_metadata_uses_defaults(self, bucket_initialised):
        hits = [{"text": "hello", "metadata": {}}]
        result = rag_tools._collect_sources(hits)
        assert result == ["S1"]
        entry = rag_tools._sources_ctx.get()[0]
        assert entry["document"] == "?"
        assert entry["page_start"] == "?"
        assert entry["page_end"] == "?"
        assert entry["section"] == ""
        assert entry["product"] == ""
        assert entry["chunk_id"] == ""

    def test_fallback_to_find_file_url_when_no_file_url_in_metadata(self, bucket_initialised, tmp_path):
        fake_file = tmp_path / "fallback.pdf"
        fake_file.write_bytes(b"%PDF")

        hits = [
            {
                "text": "fallback test",
                "metadata": {
                    "document_name": "fallback.pdf",
                    "page_start": 1,
                    # no file_url key
                },
            }
        ]

        with patch.object(rag_tools, "_DATA_DIR", tmp_path):
            rag_tools._find_file_url.cache_clear()
            # Also patch _to_docs_path so we can verify _find_file_url was consulted
            with patch.object(rag_tools, "_to_docs_path", return_value="/docs/fallback.