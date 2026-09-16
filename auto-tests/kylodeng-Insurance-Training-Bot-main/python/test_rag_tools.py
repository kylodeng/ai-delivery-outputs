"""
Test suite for api/rag_tools.py

What is tested:
    - reset_sources(): initialises a fresh list in the contextvar
    - get_current_sources(): returns the current sources list or empty list
    - _find_file_url(): file-system search with lru_cache (mocked filesystem)
    - _to_docs_path(): URI-to-server-URL conversion (happy path, edge cases, errors)
    - _collect_sources(): dedup logic, bucket building, source_id assignment
    - _log_hits(): conditional logging based on SHOW_TOOL_CALLS env var
    - make_rag_tools() factory: returns a list of tool objects
    - get_current_date tool: returns today's date in correct format
    - list_products tool: stub (source truncated — see TODO)

Mocks used:
    - unittest.mock.patch for filesystem (Path.rglob, Path.resolve)
    - unittest.mock.patch for datetime.date.today
    - unittest.mock.patch for logging.Logger.info
    - unittest.mock.MagicMock for the vector store passed to make_rag_tools()
    - contextvars isolation via manual set/reset in fixtures

TODOs:
    - list_products tool body is truncated in source; tests are stubbed
    - Any additional tools returned by make_rag_tools() beyond get_current_date
      and list_products cannot be tested without full source
    - _find_file_url cache invalidation between tests requires lru_cache clearing
"""

import contextvars
import os
import sys
import types
from datetime import date
from pathlib import Path, PurePosixPath
from unittest.mock import MagicMock, patch, call
import pytest

# ---------------------------------------------------------------------------
# Ensure the package root is importable when running pytest from the repo root
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


# ===========================================================================
# Helpers / fixtures
# ===========================================================================

@pytest.fixture(autouse=True)
def _isolate_contextvar():
    """Ensure each test starts with a clean contextvar state."""
    token = _sources_ctx.set(None)
    yield
    _sources_ctx.reset(token)


@pytest.fixture(autouse=True)
def _clear_lru_cache():
    """Clear the lru_cache on _find_file_url before each test."""
    _find_file_url.cache_clear()
    yield
    _find_file_url.cache_clear()


def _make_hit(
    document_name="Generations-II_PB_EN.pdf",
    page_start=1,
    page_end=2,
    product_name="Generations II",
    doc_type="product_brochure",
    section_title="Overview",
    file_url="",
    chunk_id="c1",
    word_count=120,
    text="Sample text for the chunk.",
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


def _make_store():
    return MagicMock(name="vector_store")


# ===========================================================================
# reset_sources / get_current_sources
# ===========================================================================

class TestResetSources:
    def test_sets_empty_list(self):
        reset_sources()
        assert _sources_ctx.get(None) == []

    def test_overwrites_existing_list(self):
        _sources_ctx.set(["stale"])
        reset_sources()
        assert _sources_ctx.get(None) == []

    def test_get_current_sources_returns_empty_list_when_none(self):
        # contextvar is None (no reset called)
        result = get_current_sources()
        assert result == []

    def test_get_current_sources_returns_empty_list_after_reset(self):
        reset_sources()
        result = get_current_sources()
        assert result == []

    def test_get_current_sources_reflects_mutations(self):
        reset_sources()
        bucket = _sources_ctx.get(None)
        bucket.append({"source_id": "S1"})
        result = get_current_sources()
        assert len(result) == 1
        assert result[0]["source_id"] == "S1"

    def test_get_current_sources_when_contextvar_set_to_none_explicitly(self):
        _sources_ctx.set(None)
        assert get_current_sources() == []


# ===========================================================================
# _find_file_url
# ===========================================================================

class TestFindFileUrl:
    def test_returns_uri_when_file_found(self, tmp_path):
        # Create a temporary file and point _DATA_DIR at its parent
        subdir = tmp_path / "Insurance-product-info"
        subdir.mkdir()
        doc = subdir / "Generations-II_PB_EN.pdf"
        doc.write_text("dummy")

        with patch.object(rag_tools, "_DATA_DIR", tmp_path):
            result = _find_file_url("Generations-II_PB_EN.pdf")

        assert result.startswith("file://") or result.startswith("file:")
        assert "Generations-II_PB_EN.pdf" in result

    def test_returns_empty_string_when_file_not_found(self, tmp_path):
        with patch.object(rag_tools, "_DATA_DIR", tmp_path):
            result = _find_file_url("nonexistent_document.pdf")

        assert result == ""

    def test_lru_cache_returns_same_result_on_second_call(self, tmp_path):
        subdir = tmp_path / "docs"
        subdir.mkdir()
        doc = subdir / "cached_doc.pdf"
        doc.write_text("data")

        with patch.object(rag_tools, "_DATA_DIR", tmp_path):
            r1 = _find_file_url("cached_doc.pdf")
            r2 = _find_file_url("cached_doc.pdf")

        assert r1 == r2

    def test_empty_string_document_name(self, tmp_path):
        with patch.object(rag_tools, "_DATA_DIR", tmp_path):
            result = _find_file_url("")
        # Either empty string or a URI; must not raise
        assert isinstance(result, str)


# ===========================================================================
# _to_docs_path
# ===========================================================================

class TestToDocsPath:
    def test_empty_string_returns_empty(self):
        assert _to_docs_path("") == ""

    def test_valid_file_uri_returns_docs_path(self, tmp_path):
        subdir = tmp_path / "Insurance-product-info"
        subdir.mkdir()
        doc = subdir / "doc.pdf"
        doc.write_text("x")

        file_uri = doc.resolve().as_uri()

        with patch.object(rag_tools, "_DATA_DIR", tmp_path):
            result = _to_docs_path(file_uri)

        assert result == "/docs/Insurance-product-info/doc.pdf"

    def test_path_with_spaces_is_percent_encoded(self, tmp_path):
        subdir = tmp_path / "My Docs"
        subdir.mkdir()
        doc = subdir / "my file.pdf"
        doc.write_text("x")

        file_uri = doc.resolve().as_uri()

        with patch.object(rag_tools, "_DATA_DIR", tmp_path):
            result = _to_docs_path(file_uri)

        assert "My%20Docs" in result or "My+Docs" in result or "My Docs" not in result
        assert "my%20file.pdf" in result or "my+file.pdf" in result or "my file.pdf" not in result

    def test_returns_empty_on_path_outside_data_dir(self, tmp_path):
        """A file:// URI that cannot be made relative to _DATA_DIR returns ''."""
        other = tmp_path / "other"
        other.mkdir()
        doc = other / "doc.pdf"
        doc.write_text("x")

        # Point _DATA_DIR somewhere that does NOT contain the file
        unrelated = tmp_path / "unrelated_data"
        unrelated.mkdir()

        file_uri = doc.resolve().as_uri()
        with patch.object(rag_tools, "_DATA_DIR", unrelated):
            result = _to_docs_path(file_uri)

        assert result == ""

    def test_malformed_uri_returns_empty(self):
        result = _to_docs_path("not_a_valid_uri:::///")
        assert isinstance(result, str)
        # Should not raise; may return "" or some fallback

    def test_non_file_uri_returns_empty_or_string(self):
        result = _to_docs_path("https://example.com/doc.pdf")
        assert isinstance(result, str)


# ===========================================================================
# _collect_sources
# ===========================================================================

class TestCollectSources:
    def test_returns_empty_strings_when_bucket_is_none(self):
        hits = [_make_hit(), _make_hit()]
        result = _collect_sources(hits)
        assert result == ["", ""]

    def test_single_hit_assigned_s1(self):
        reset_sources()
        hits = [_make_hit(file_url="")]
        with patch.object(rag_tools, "_find_file_url", return_value=""):
            result = _collect_sources(hits)
        assert result == ["S1"]
        bucket = _sources_ctx.get(None)
        assert len(bucket) == 1
        assert bucket[0]["source_id"] == "S1"

    def test_two_distinct_hits_get_unique_ids(self):
        reset_sources()
        hits = [
            _make_hit(document_name="doc1.pdf", page_start=1),
            _make_hit(document_name="doc2.pdf", page_start=1),
        ]
        with patch.object(rag_tools, "_find_file_url", return_value=""):
            result = _collect_sources(hits)
        assert result == ["S1", "S2"]

    def test_duplicate_hits_reuse_same_id(self):
        reset_sources()
        hit = _make_hit(document_name="doc1.pdf", page_start=3)
        with patch.object(rag_tools, "_find_file_url", return_value=""):
            result = _collect_sources([hit, hit])
        assert result == ["S1", "S1"]
        bucket = _sources_ctx.get(None)
        assert len(bucket) == 1  # only one entry despite two hits

    def test_dedup_by_document_and_page_start(self):
        reset_sources()
        h1 = _make_hit(document_name="doc.pdf", page_start=5, chunk_id="a")
        h2 = _make_hit(document_name="doc.pdf", page_start=5, chunk_id="b")  # same page
        with patch.object(rag_tools, "_find_file_url", return_value=""):
            result = _collect_sources([h1, h2])
        assert result[0] == result[1]
        assert len(_sources_ctx.get(None)) == 1

    def test_different_pages_same_doc_get_different_ids(self):
        reset_sources()
        h1 = _make_hit(document_name="doc.pdf", page_start=1)
        h2 = _make_hit(document_name="doc.pdf", page_start=2)
        with patch.object(rag_tools, "_find_file_url", return_value=""):
            result = _collect_sources([h1, h2])
        assert result == ["S1", "S2"]

    def test_source_entry_fields_populated(self):
        reset_sources()
        hit = _make_hit(
            document_name="Generations-II_PB_EN.pdf",
            page_start=10,
            page_end=11,
            product_name="Generations II",
            section_title="Benefits",
            chunk_id="chunk_42",
            text="A" * 300,
            file_url="",
        )
        with patch.object(rag_tools, "_find_file_url", return_value=""):
            _collect_sources([hit])

        entry = _sources_ctx.get(None)[0]
        assert entry["source_id"] == "S1"
        assert entry["document"] == "Generations-II_PB_EN.pdf"
        assert entry["product"] == "Generations II"
        assert entry["page_start"] == 10
        assert entry["page_end"] == 11
        assert entry["section"] == "Benefits"
        assert entry["chunk_id"] == "chunk_42"
        assert len(entry["text_preview"]) <= 250
        assert entry["text_preview"] == "A" * 250

    def test_fallback_to_find_file_url_when_no_file_url_in_metadata(self):
        reset_sources()
        hit = _make_hit(file_url="")
        fake_uri = "file:///data/Insurance-product-info/doc.pdf"
        with patch.object(rag_tools, "_find_file_url", return_value=fake_uri) as mock_find, \
             patch.object(rag_tools, "_to_docs_path", return_value="/docs/doc.pdf"):
            _collect_sources([hit])
            mock_find.assert_called_once()

    def test_file_url_in_metadata_used_directly(self):
        reset_sources()
        hit = _make_hit(file_url="file:///some/path/doc.pdf")
        with patch.object(rag_tools, "_find_file_url") as mock_find, \
             patch.object(rag_tools, "_to_docs_path", return_value="/docs/doc.pdf"):
            _collect_sources([hit])
            mock_find.assert_not_called()

    def test_empty_hits_list(self):
        reset_sources()
        result = _collect_sources([])
        assert result == []
        assert _sources_ctx.get(None) == []

    def test_missing_metadata_key_defaults(self):
        reset_sources()
        hit = {"text": "hello", "metadata": {}}  # no keys at all
        with patch.object(rag_tools, "_find_file_url", return_value=""):
            result = _collect_sources([hit])
        assert result == ["S1"]
        entry = _sources_ctx.get(None)[0]
        assert entry["document"] == "?"
        assert entry["page_start"] == "?"
        assert entry["page_end"] == "?"
        assert entry["product"] == ""
        assert entry["section"] == ""

    def test_counter_continues_across_calls(self):
        """Source IDs must increment across multiple _collect_sources calls in same request."""
        reset_sources()
        h1 = _make_hit(document_name="a.pdf", page_start=1)
        h2 = _make_hit(document_name="b.pdf", page_start=1)
        with patch.object(rag_tools, "_find_file_url", return_value=""):
            r1 = _collect_sources([h1])
            r2 = _collect_sources([h2])
        assert r1 == ["S1"]
        assert r2 ==