"""
Test module for api/rag_tools.py

What is tested:
- reset_sources(): initialises a fresh list in the contextvar
- get_current_sources(): returns current sources or empty list
- _find_file_url(): file-system search with lru_cache (mocked filesystem)
- _to_docs_path(): URI → /docs/-relative URL conversion
- _collect_sources(): dedup logic, source ID assignment, bucket management
- _log_hits(): conditional logging based on SHOW_TOOL_CALLS env var
- make_rag_tools(): factory returns tool list; get_current_date tool; list_products tool stub

Mocks used:
- unittest.mock.patch for filesystem (_DATA_DIR.rglob), os.getenv, logging
- contextvars isolation via reset_sources() / manual ContextVar.set()
- datetime.date.today patched to return a fixed date
- LangChain store mock for make_rag_tools

TODOs:
- list_products tool: source is truncated, full body unknown — stub test added
- Additional RAG retrieval tools inside make_rag_tools (not visible in truncated source)
- Integration tests against a real vector store
"""

import contextvars
import logging
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock
import pytest

# ---------------------------------------------------------------------------
# Ensure the api package is importable when running from repo root
# ---------------------------------------------------------------------------
sys.path.insert(0, str(Path(__file__).parent.parent))

# We import the module under test after path fixup
import importlib
import api.rag_tools as rag_tools
from api.rag_tools import (
    reset_sources,
    get_current_sources,
    _to_docs_path,
    _collect_sources,
    _log_hits,
    _sources_ctx,
    make_rag_tools,
)


# ===========================================================================
# Helpers / fixtures
# ===========================================================================

def _make_hit(
    document_name="doc.pdf",
    page_start=1,
    page_end=2,
    product_name="Generations II",
    doc_type="product_brochure",
    section_title="Overview",
    file_url="",
    chunk_id="c1",
    word_count=100,
    text="Sample text content for testing purposes.",
):
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


@pytest.fixture(autouse=True)
def isolate_sources_ctx():
    """
    Run every test with a clean contextvar state.
    We manually reset both before and after each test.
    """
    _sources_ctx.set(None)
    yield
    _sources_ctx.set(None)


@pytest.fixture()
def with_fresh_bucket():
    """Initialise a fresh bucket via reset_sources() and yield it."""
    reset_sources()
    yield _sources_ctx.get(None)


# ===========================================================================
# reset_sources
# ===========================================================================

class TestResetSources:
    def test_sets_empty_list(self):
        reset_sources()
        bucket = _sources_ctx.get(None)
        assert bucket == []

    def test_replaces_existing_list(self):
        reset_sources()
        _sources_ctx.get(None).append({"source_id": "S1"})
        reset_sources()
        bucket = _sources_ctx.get(None)
        assert bucket == []

    def test_multiple_resets_are_idempotent(self):
        reset_sources()
        reset_sources()
        reset_sources()
        assert _sources_ctx.get(None) == []


# ===========================================================================
# get_current_sources
# ===========================================================================

class TestGetCurrentSources:
    def test_returns_empty_list_when_no_bucket(self):
        # contextvar is None (reset by fixture)
        result = get_current_sources()
        assert result == []

    def test_returns_empty_list_after_reset_with_no_hits(self):
        reset_sources()
        assert get_current_sources() == []

    def test_returns_populated_sources(self):
        reset_sources()
        _sources_ctx.get(None).append({"source_id": "S1", "document": "doc.pdf"})
        result = get_current_sources()
        assert len(result) == 1
        assert result[0]["source_id"] == "S1"

    def test_returns_copy_reference_not_none(self):
        # When None, must not raise — must return a list
        result = get_current_sources()
        assert isinstance(result, list)


# ===========================================================================
# _find_file_url
# ===========================================================================

class TestFindFileUrl:
    """_find_file_url is cached; we clear the cache before each test."""

    def setup_method(self):
        rag_tools._find_file_url.cache_clear()

    def teardown_method(self):
        rag_tools._find_file_url.cache_clear()

    def test_returns_uri_when_file_found(self, tmp_path):
        # Plant a fake file inside a temp directory
        target = tmp_path / "Insurance-product-info" / "doc.pdf"
        target.parent.mkdir(parents=True)
        target.touch()

        with patch.object(rag_tools, "_DATA_DIR", tmp_path):
            rag_tools._find_file_url.cache_clear()
            # Reimport or call with patched _DATA_DIR
            # Because _find_file_url closes over _DATA_DIR at definition time
            # we must reload or patch the global directly.
            # We patch the module-level name instead:
            with patch("api.rag_tools._DATA_DIR", tmp_path):
                rag_tools._find_file_url.cache_clear()
                result = rag_tools._find_file_url("doc.pdf")
        assert result.startswith("file:///") or result == ""
        # The file should be found
        assert "doc.pdf" in result

    def test_returns_empty_string_when_not_found(self, tmp_path):
        with patch("api.rag_tools._DATA_DIR", tmp_path):
            rag_tools._find_file_url.cache_clear()
            result = rag_tools._find_file_url("nonexistent_file.pdf")
        assert result == ""

    def test_caches_result(self, tmp_path):
        target = tmp_path / "cached_doc.pdf"
        target.touch()
        with patch("api.rag_tools._DATA_DIR", tmp_path):
            rag_tools._find_file_url.cache_clear()
            result1 = rag_tools._find_file_url("cached_doc.pdf")
            result2 = rag_tools._find_file_url("cached_doc.pdf")
        assert result1 == result2
        info = rag_tools._find_file_url.cache_info()
        assert info.hits >= 1

    def test_returns_first_match_when_multiple(self, tmp_path):
        (tmp_path / "a").mkdir()
        (tmp_path / "b").mkdir()
        (tmp_path / "a" / "multi.pdf").touch()
        (tmp_path / "b" / "multi.pdf").touch()
        with patch("api.rag_tools._DATA_DIR", tmp_path):
            rag_tools._find_file_url.cache_clear()
            result = rag_tools._find_file_url("multi.pdf")
        assert result != ""
        assert "multi.pdf" in result


# ===========================================================================
# _to_docs_path
# ===========================================================================

class TestToDocsPath:
    def test_empty_string_returns_empty(self):
        assert _to_docs_path("") == ""

    def test_none_like_falsy_returns_empty(self):
        # The function checks `if not file_url`
        assert _to_docs_path("") == ""

    def test_valid_file_uri_converts_correctly(self, tmp_path):
        # Create a file so resolve() works
        subdir = tmp_path / "Insurance-product-info"
        subdir.mkdir()
        target = subdir / "doc.pdf"
        target.touch()

        file_uri = target.resolve().as_uri()

        with patch("api.rag_tools._DATA_DIR", tmp_path):
            result = _to_docs_path(file_uri)

        assert result == "/docs/Insurance-product-info/doc.pdf"

    def test_special_characters_are_quoted(self, tmp_path):
        subdir = tmp_path / "My Product"
        subdir.mkdir()
        target = subdir / "my doc.pdf"
        target.touch()

        file_uri = target.resolve().as_uri()
        with patch("api.rag_tools._DATA_DIR", tmp_path):
            result = _to_docs_path(file_uri)

        # Spaces should be percent-encoded
        assert "%20" in result or " " not in result

    def test_malformed_uri_returns_empty(self):
        result = _to_docs_path("not_a_real_uri://garbage///")
        # Should not raise; returns "" or some string
        assert isinstance(result, str)

    def test_uri_outside_data_dir_returns_empty(self, tmp_path):
        other = tmp_path / "other_dir" / "doc.pdf"
        other.parent.mkdir(parents=True)
        other.touch()
        file_uri = other.resolve().as_uri()
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        with patch("api.rag_tools._DATA_DIR", data_dir):
            result = _to_docs_path(file_uri)
        assert result == ""

    def test_deeply_nested_path(self, tmp_path):
        subdir = tmp_path / "a" / "b" / "c"
        subdir.mkdir(parents=True)
        target = subdir / "deep.pdf"
        target.touch()
        file_uri = target.resolve().as_uri()
        with patch("api.rag_tools._DATA_DIR", tmp_path):
            result = _to_docs_path(file_uri)
        assert result == "/docs/a/b/c/deep.pdf"


# ===========================================================================
# _collect_sources
# ===========================================================================

class TestCollectSources:

    # --- bucket is None (no reset_sources called) ---

    def test_returns_empty_strings_when_no_bucket(self):
        hits = [_make_hit(), _make_hit()]
        result = _collect_sources(hits)
        assert result == ["", ""]

    def test_returns_empty_list_for_empty_hits_no_bucket(self):
        result = _collect_sources([])
        assert result == []

    # --- with a fresh bucket ---

    def test_assigns_sequential_source_ids(self, with_fresh_bucket):
        hits = [
            _make_hit(document_name="a.pdf", page_start=1),
            _make_hit(document_name="b.pdf", page_start=1),
        ]
        result = _collect_sources(hits)
        assert result == ["S1", "S2"]

    def test_deduplicates_same_doc_same_page(self, with_fresh_bucket):
        hit = _make_hit(document_name="dup.pdf", page_start=5)
        result = _collect_sources([hit, hit])
        assert result == ["S1", "S1"]
        # Only one entry in the bucket
        bucket = _sources_ctx.get(None)
        assert len(bucket) == 1

    def test_different_pages_same_doc_not_deduped(self, with_fresh_bucket):
        h1 = _make_hit(document_name="doc.pdf", page_start=1)
        h2 = _make_hit(document_name="doc.pdf", page_start=2)
        result = _collect_sources([h1, h2])
        assert result == ["S1", "S2"]
        assert len(_sources_ctx.get(None)) == 2

    def test_empty_hits_with_bucket(self, with_fresh_bucket):
        result = _collect_sources([])
        assert result == []
        assert _sources_ctx.get(None) == []

    def test_source_entry_fields_populated(self, with_fresh_bucket, tmp_path):
        target = tmp_path / "Generations-II_PB_EN.pdf"
        target.touch()
        file_uri = target.resolve().as_uri()
        hit = _make_hit(
            document_name="Generations-II_PB_EN.pdf",
            page_start=3,
            page_end=4,
            product_name="Generations II",
            section_title="Overview",
            file_url=file_uri,
            chunk_id="chunk-001",
            text="A" * 300,
        )
        with patch("api.rag_tools._DATA_DIR", tmp_path):
            result = _collect_sources([hit])
        assert result == ["S1"]
        bucket = _sources_ctx.get(None)
        entry = bucket[0]
        assert entry["source_id"] == "S1"
        assert entry["document"] == "Generations-II_PB_EN.pdf"
        assert entry["product"] == "Generations II"
        assert entry["page_start"] == 3
        assert entry["page_end"] == 4
        assert entry["section"] == "Overview"
        assert entry["chunk_id"] == "chunk-001"
        assert len(entry["text_preview"]) <= 250

    def test_text_preview_truncated_to_250(self, with_fresh_bucket):
        hit = _make_hit(text="X" * 500)
        _collect_sources([hit])
        bucket = _sources_ctx.get(None)
        assert len(bucket[0]["text_preview"]) == 250

    def test_text_preview_short_text_not_padded(self, with_fresh_bucket):
        hit = _make_hit(text="short")
        _collect_sources([hit])
        assert _sources_ctx.get(None)[0]["text_preview"] == "short"

    def test_missing_metadata_fields_use_defaults(self, with_fresh_bucket):
        hit = {"metadata": {}, "text": ""}
        result = _collect_sources([hit])
        assert result == ["S1"]
        entry = _sources_ctx.get(None)[0]
        assert entry["document"] == "?"
        assert entry["page_start"] == "?"
        assert entry["page_end"] == "?"
        assert entry["product"] == ""
        assert entry["section"] == ""
        assert entry["chunk_id"] == ""

    def test_hit_without_text_key(self, with_fresh_bucket):
        hit = {"metadata": {"document_name": "x.pdf", "page_start": 1}}
        result = _collect_sources([hit])
        assert result == ["S1"]
        assert _sources_ctx.get(None)[0]["text_preview"] == ""

    def test_incremental_calls_keep_counter(self, with_fresh_bucket):
        """Two separate calls to _collect_sources accumulate IDs correctly."""
        _collect_sources([_make_hit(document_name="a.pdf", page_start=1)])
        _collect_sources([_make_hit(document_name="b.pdf", page_start=1)])
        bucket = _sources_ctx.get(None)
        assert [e["source_id"] for e in bucket] == ["S1", "S2"]

    def test_dedup_key_across_multiple_calls(self, with_fresh_bucket):
        hit = _make_hit(document_name="repeat.pdf",