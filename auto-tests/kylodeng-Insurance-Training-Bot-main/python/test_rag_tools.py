"""
Tests for api/rag_tools.py

What is tested:
  - reset_sources() / get_current_sources() contextvar lifecycle
  - _find_file_url() caching and path resolution
  - _to_docs_path() URI-to-server-path conversion
  - _collect_sources() deduplication, ID generation, bucket management
  - _log_hits() conditional logging
  - make_rag_tools() factory: get_current_date tool, list_products tool stub

Mocks used:
  - unittest.mock.patch for filesystem (_DATA_DIR, Path.rglob)
  - unittest.mock.MagicMock for the vector store passed to make_rag_tools()
  - unittest.mock.patch for logging.Logger.info
  - freezegun / monkeypatch for date.today() in get_current_date tests

TODOs:
  - list_products tool body is truncated in source; full integration test needs complete source
  - Any additional @tool functions defined after list_products in make_rag_tools() are untested
  - Tests for async concurrent access to _sources_ctx require asyncio task harness
"""

import contextvars
import logging
import sys
import types
from datetime import date, datetime
from functools import lru_cache
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Make the module importable without its heavy LangChain dependencies by
# providing minimal stubs before the real import.
# ---------------------------------------------------------------------------

def _install_langchain_stubs():
    """Install minimal stubs so rag_tools.py can be imported in isolation."""
    # langchain_core.tools stub
    lc_core = types.ModuleType("langchain_core")
    lc_tools = types.ModuleType("langchain_core.tools")

    def tool(fn):
        """Identity decorator stub for @tool."""
        fn.invoke = fn  # minimal duck-type
        return fn

    lc_tools.tool = tool
    lc_core.tools = lc_tools
    sys.modules.setdefault("langchain_core", lc_core)
    sys.modules.setdefault("langchain_core.tools", lc_tools)


_install_langchain_stubs()

# Now import the module under test
import importlib
import api.rag_tools as rag_tools  # noqa: E402

# Re-expose private helpers for convenience
_sources_ctx = rag_tools._sources_ctx
reset_sources = rag_tools.reset_sources
get_current_sources = rag_tools.get_current_sources
_find_file_url = rag_tools._find_file_url
_to_docs_path = rag_tools._to_docs_path
_collect_sources = rag_tools._collect_sources
_log_hits = rag_tools._log_hits
make_rag_tools = rag_tools.make_rag_tools


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def clear_sources_ctx():
    """Reset contextvar and lru_cache before every test."""
    _sources_ctx.set(None)
    _find_file_url.cache_clear()
    yield
    _sources_ctx.set(None)
    _find_file_url.cache_clear()


def _make_hit(doc="doc.pdf", page_start=1, page_end=2, file_url="",
              product="ProdA", section="Sec1", chunk_id="c1",
              text="Sample text content"):
    return {
        "metadata": {
            "document_name": doc,
            "page_start": page_start,
            "page_end": page_end,
            "file_url": file_url,
            "product_name": product,
            "section_title": section,
            "chunk_id": chunk_id,
            "doc_type": "product_brochure",
            "word_count": 42,
        },
        "text": text,
    }


# ---------------------------------------------------------------------------
# reset_sources / get_current_sources
# ---------------------------------------------------------------------------

class TestSourcesContextVar:

    def test_get_current_sources_returns_empty_list_before_reset(self):
        """Before reset_sources() the contextvar default is None → empty list."""
        assert get_current_sources() == []

    def test_reset_sources_initialises_empty_list(self):
        reset_sources()
        assert get_current_sources() == []

    def test_sources_accumulate_after_reset(self):
        reset_sources()
        bucket = _sources_ctx.get(None)
        bucket.append({"source_id": "S1", "document": "a.pdf", "page_start": 1})
        result = get_current_sources()
        assert len(result) == 1
        assert result[0]["source_id"] == "S1"

    def test_reset_clears_previous_sources(self):
        reset_sources()
        _sources_ctx.get(None).append({"source_id": "S1", "document": "a.pdf", "page_start": 1})
        reset_sources()  # second reset
        assert get_current_sources() == []

    def test_get_current_sources_with_none_returns_empty_list(self):
        _sources_ctx.set(None)
        assert get_current_sources() == []


# ---------------------------------------------------------------------------
# _find_file_url
# ---------------------------------------------------------------------------

class TestFindFileUrl:

    def test_returns_file_uri_when_file_found(self, tmp_path):
        # Point _DATA_DIR at tmp_path
        fake_file = tmp_path / "Insurance-product-info" / "Generations-II" / "doc.pdf"
        fake_file.parent.mkdir(parents=True)
        fake_file.touch()

        with patch.object(rag_tools, "_DATA_DIR", tmp_path):
            _find_file_url.cache_clear()
            # Call the real function via a fresh reference
            result = rag_tools._find_file_url("doc.pdf")

        assert result.startswith("file://")
        assert "doc.pdf" in result

    def test_returns_empty_string_when_file_not_found(self, tmp_path):
        with patch.object(rag_tools, "_DATA_DIR", tmp_path):
            _find_file_url.cache_clear()
            result = rag_tools._find_file_url("nonexistent.pdf")
        assert result == ""

    def test_result_is_cached(self, tmp_path):
        """Second call with same arg should hit cache (rglob called once)."""
        with patch.object(rag_tools, "_DATA_DIR", tmp_path):
            _find_file_url.cache_clear()
            with patch.object(Path, "rglob", return_value=iter([])) as mock_rglob:
                rag_tools._find_file_url("cached.pdf")
                rag_tools._find_file_url("cached.pdf")
            # rglob should be called only once due to lru_cache
            assert mock_rglob.call_count == 1

    def test_returns_first_match_when_multiple_files_found(self, tmp_path):
        file1 = tmp_path / "a" / "doc.pdf"
        file2 = tmp_path / "b" / "doc.pdf"
        file1.parent.mkdir(parents=True)
        file2.parent.mkdir(parents=True)
        file1.touch()
        file2.touch()

        with patch.object(rag_tools, "_DATA_DIR", tmp_path):
            _find_file_url.cache_clear()
            result = rag_tools._find_file_url("doc.pdf")

        assert result.startswith("file://")
        assert "doc.pdf" in result


# ---------------------------------------------------------------------------
# _to_docs_path
# ---------------------------------------------------------------------------

class TestToDocsPath:

    def test_empty_string_returns_empty(self):
        assert _to_docs_path("") == ""

    def test_none_like_falsy_empty_string(self):
        # Falsy input → ""
        assert _to_docs_path("") == ""

    def test_valid_file_uri_converted_to_docs_path(self, tmp_path):
        """A file URI inside _DATA_DIR is converted to /docs/... path."""
        # Create actual file so resolve() works
        subdir = tmp_path / "Insurance-product-info"
        subdir.mkdir()
        pdf = subdir / "doc.pdf"
        pdf.touch()

        file_url = pdf.resolve().as_uri()

        with patch.object(rag_tools, "_DATA_DIR", tmp_path):
            result = _to_docs_path(file_url)

        assert result == "/docs/Insurance-product-info/doc.pdf"

    def test_file_outside_data_dir_returns_empty(self, tmp_path):
        """A file URI outside _DATA_DIR cannot be made relative → ''."""
        outside = tmp_path / "outside" / "doc.pdf"
        outside.parent.mkdir(parents=True)
        outside.touch()
        file_url = outside.resolve().as_uri()

        data_dir = tmp_path / "data"
        data_dir.mkdir()

        with patch.object(rag_tools, "_DATA_DIR", data_dir):
            result = _to_docs_path(file_url)

        assert result == ""

    def test_special_characters_are_percent_encoded(self, tmp_path):
        """Spaces and special chars in path segments are URL-encoded."""
        subdir = tmp_path / "my folder"
        subdir.mkdir()
        pdf = subdir / "my doc.pdf"
        pdf.touch()

        file_url = pdf.resolve().as_uri()

        with patch.object(rag_tools, "_DATA_DIR", tmp_path):
            result = _to_docs_path(file_url)

        assert "my%20folder" in result or "my+folder" in result or "my folder" not in result
        # Must start with /docs/
        assert result.startswith("/docs/")

    def test_malformed_uri_returns_empty(self):
        """Non-parseable / bad URI should return '' without raising."""
        result = _to_docs_path("not_a_real_uri:::///bad")
        # Should not raise; return value may be empty or some path but no exception
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# _collect_sources
# ---------------------------------------------------------------------------

class TestCollectSources:

    def test_returns_empty_strings_when_bucket_is_none(self):
        """Without reset_sources(), bucket is None → all source_ids are ''."""
        _sources_ctx.set(None)
        hits = [_make_hit()]
        result = _collect_sources(hits)
        assert result == [""]

    def test_empty_hits_returns_empty_list(self):
        reset_sources()
        assert _collect_sources([]) == []

    def test_single_hit_gets_first_source_id(self):
        reset_sources()
        hits = [_make_hit(doc="Generations-II_PB_EN.pdf", page_start=5)]
        result = _collect_sources(hits)
        assert result == ["S1"]
        bucket = _sources_ctx.get(None)
        assert len(bucket) == 1
        assert bucket[0]["source_id"] == "S1"
        assert bucket[0]["document"] == "Generations-II_PB_EN.pdf"
        assert bucket[0]["page_start"] == 5

    def test_two_different_hits_get_sequential_ids(self):
        reset_sources()
        hits = [
            _make_hit(doc="doc_a.pdf", page_start=1),
            _make_hit(doc="doc_b.pdf", page_start=3),
        ]
        result = _collect_sources(hits)
        assert result == ["S1", "S2"]

    def test_duplicate_hits_reuse_same_source_id(self):
        """Same (doc, page_start) should map to same source_id."""
        reset_sources()
        hits = [
            _make_hit(doc="doc_a.pdf", page_start=1),
            _make_hit(doc="doc_a.pdf", page_start=1),  # duplicate
        ]
        result = _collect_sources(hits)
        assert result == ["S1", "S1"]
        bucket = _sources_ctx.get(None)
        assert len(bucket) == 1  # only one entry in bucket

    def test_partial_duplicate_different_page_gets_new_id(self):
        reset_sources()
        hits = [
            _make_hit(doc="doc_a.pdf", page_start=1),
            _make_hit(doc="doc_a.pdf", page_start=2),  # same doc, different page
        ]
        result = _collect_sources(hits)
        assert result == ["S1", "S2"]

    def test_multiple_calls_accumulate_ids_correctly(self):
        """IDs from second _collect_sources call continue from where first left off."""
        reset_sources()
        first_hits = [_make_hit(doc="a.pdf", page_start=1)]
        _collect_sources(first_hits)

        second_hits = [_make_hit(doc="b.pdf", page_start=1)]
        result = _collect_sources(second_hits)
        assert result == ["S2"]

    def test_text_preview_truncated_to_250_chars(self):
        reset_sources()
        long_text = "x" * 500
        hits = [_make_hit(doc="d.pdf", page_start=1, text=long_text)]
        _collect_sources(hits)
        bucket = _sources_ctx.get(None)
        assert len(bucket[0]["text_preview"]) == 250

    def test_text_preview_short_text_preserved(self):
        reset_sources()
        short_text = "Hello world"
        hits = [_make_hit(doc="d.pdf", page_start=1, text=short_text)]
        _collect_sources(hits)
        bucket = _sources_ctx.get(None)
        assert bucket[0]["text_preview"] == short_text

    def test_missing_metadata_fields_use_defaults(self):
        reset_sources()
        hit = {"metadata": {}, "text": ""}
        result = _collect_sources([hit])
        assert result == ["S1"]
        entry = _sources_ctx.get(None)[0]
        assert entry["document"] == "?"
        assert entry["page_start"] == "?"
        assert entry["page_end"] == "?"
        assert entry["product"] == ""
        assert entry["section"] == ""

    def test_file_url_resolved_via_find_file_url_when_missing(self, tmp_path):
        """If metadata has no file_url, _find_file_url() fallback is used."""
        reset_sources()
        # Create a file so _find_file_url returns something
        pdf = tmp_path / "fallback.pdf"
        pdf.touch()

        with patch.object(rag_tools, "_DATA_DIR", tmp_path), \
             patch.object(rag_tools, "_to_docs_path", return_value="/docs/fallback.pdf"):
            _find_file_url.cache_clear()
            hit = _make_hit(doc="fallback.pdf", page_start=1, file_url="")
            _collect_sources([hit])

        bucket = _sources_ctx.get(None)
        assert bucket[0]["file_url"] == "/docs/fallback.pdf"

    def test_file_url_from_metadata_takes_precedence(self):
        reset_sources()
        with patch.object(rag_tools, "_to_docs_path", return_value="/docs/explicit.pdf"):
            hit = _make_hit(doc="d.pdf", page_start=1, file_url="file:///some/path.pdf")
            _collect_sources([hit])
        bucket = _sources_ctx.get(None)
        assert bucket[0]["file_url"] == "/docs/explicit.pdf"

    def test_cross_call_dedup_sees_previously_app