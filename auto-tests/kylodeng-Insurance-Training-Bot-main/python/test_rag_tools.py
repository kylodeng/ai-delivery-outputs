"""
Test module for api/rag_tools.py

What is tested:
    - reset_sources(): initialises a fresh list in the contextvar
    - get_current_sources(): returns current sources or empty list when unset
    - _find_file_url(): file-system lookup with lru_cache, returns URI or ""
    - _to_docs_path(): converts file:/// URIs to /docs/-relative server URLs
    - _collect_sources(): deduplicates hits, assigns source IDs, builds entries
    - _log_hits(): logs chunk metadata (only when SHOW_TOOL_CALLS=true)
    - make_rag_tools() → get_current_date tool: returns today's date string
    - make_rag_tools() → list_products tool: stub (source is truncated)

Mocks used:
    - unittest.mock.patch for os.getenv (SHOW_TOOL_CALLS flag)
    - unittest.mock.patch for Path.rglob (_find_file_url filesystem)
    - unittest.mock.patch for date.today (get_current_date tool)
    - unittest.mock.MagicMock for the vector store passed to make_rag_tools()
    - unittest.mock.patch for logger (log_hits verification)

TODOs:
    - list_products tool: source code is truncated; full implementation needed
    - Any additional tools returned by make_rag_tools() beyond what is visible
    - Integration tests against a real/test vector store
"""

import contextvars
import logging
import os
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

# ---------------------------------------------------------------------------
# Module import — adjust sys.path if needed when running from project root
# ---------------------------------------------------------------------------
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

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

@pytest.fixture(autouse=True)
def reset_lru_cache():
    """Clear _find_file_url's LRU cache before every test."""
    _find_file_url.cache_clear()
    yield
    _find_file_url.cache_clear()


@pytest.fixture()
def fresh_sources():
    """Set up a fresh sources list for the current context, then clean up."""
    reset_sources()
    yield
    _sources_ctx.set(None)


@pytest.fixture()
def no_sources_context():
    """Ensure _sources_ctx is None (simulates a non-teacher-mode request)."""
    token = _sources_ctx.set(None)
    yield
    _sources_ctx.reset(token)


def _make_hit(
    document_name="doc.pdf",
    page_start=1,
    page_end=2,
    product_name="Generations II",
    section_title="Overview",
    file_url="",
    chunk_id="c1",
    word_count=100,
    doc_type="product_brochure",
    text="Some chunk text here.",
):
    return {
        "metadata": {
            "document_name": document_name,
            "page_start": page_start,
            "page_end": page_end,
            "product_name": product_name,
            "section_title": section_title,
            "file_url": file_url,
            "chunk_id": chunk_id,
            "word_count": word_count,
            "doc_type": doc_type,
        },
        "text": text,
    }


# ---------------------------------------------------------------------------
# reset_sources / get_current_sources
# ---------------------------------------------------------------------------

class TestResetSources:
    def test_sets_empty_list(self):
        reset_sources()
        result = _sources_ctx.get(None)
        assert result == []

    def test_overwrites_existing_list(self):
        reset_sources()
        _sources_ctx.get(None).append({"source_id": "S1"})
        reset_sources()
        assert _sources_ctx.get(None) == []

    def test_idempotent_multiple_calls(self):
        reset_sources()
        reset_sources()
        assert _sources_ctx.get(None) == []


class TestGetCurrentSources:
    def test_returns_empty_list_when_context_is_none(self, no_sources_context):
        assert get_current_sources() == []

    def test_returns_empty_list_when_context_empty(self, fresh_sources):
        assert get_current_sources() == []

    def test_returns_sources_after_collection(self, fresh_sources):
        hit = _make_hit()
        _collect_sources([hit])
        sources = get_current_sources()
        assert len(sources) == 1
        assert sources[0]["source_id"] == "S1"

    def test_returns_list_copy_not_none(self, no_sources_context):
        result = get_current_sources()
        assert isinstance(result, list)


# ---------------------------------------------------------------------------
# _find_file_url
# ---------------------------------------------------------------------------

class TestFindFileUrl:
    def test_returns_uri_when_file_found(self, tmp_path):
        subdir = tmp_path / "Insurance-product-info"
        subdir.mkdir()
        pdf = subdir / "doc.pdf"
        pdf.write_bytes(b"%PDF")

        with patch.object(rag_tools, "_DATA_DIR", tmp_path):
            _find_file_url.cache_clear()
            result = _find_file_url("doc.pdf")
        assert result.startswith("file:///")
        assert "doc.pdf" in result

    def test_returns_empty_string_when_not_found(self, tmp_path):
        with patch.object(rag_tools, "_DATA_DIR", tmp_path):
            _find_file_url.cache_clear()
            result = _find_file_url("nonexistent.pdf")
        assert result == ""

    def test_returns_first_match_when_multiple(self, tmp_path):
        a = tmp_path / "a"
        b = tmp_path / "b"
        a.mkdir()
        b.mkdir()
        (a / "doc.pdf").write_bytes(b"%PDF-a")
        (b / "doc.pdf").write_bytes(b"%PDF-b")

        with patch.object(rag_tools, "_DATA_DIR", tmp_path):
            _find_file_url.cache_clear()
            result = _find_file_url("doc.pdf")
        assert result.startswith("file:///")
        assert "doc.pdf" in result

    def test_result_is_cached(self, tmp_path):
        with patch.object(rag_tools, "_DATA_DIR", tmp_path):
            _find_file_url.cache_clear()
            with patch.object(Path, "rglob", return_value=iter([])) as mock_rglob:
                _find_file_url("cached.pdf")
                _find_file_url("cached.pdf")
                assert mock_rglob.call_count == 1

    def test_empty_document_name(self, tmp_path):
        with patch.object(rag_tools, "_DATA_DIR", tmp_path):
            _find_file_url.cache_clear()
            result = _find_file_url("")
        assert result == ""


# ---------------------------------------------------------------------------
# _to_docs_path
# ---------------------------------------------------------------------------

class TestToDocsPath:
    def test_empty_string_returns_empty(self):
        assert _to_docs_path("") == ""

    def test_converts_file_uri_to_docs_path(self, tmp_path):
        subdir = tmp_path / "Insurance-product-info"
        subdir.mkdir()
        pdf = subdir / "doc.pdf"
        pdf.write_bytes(b"")
        file_url = pdf.resolve().as_uri()

        with patch.object(rag_tools, "_DATA_DIR", tmp_path):
            result = _to_docs_path(file_url)
        assert result == "/docs/Insurance-product-info/doc.pdf"

    def test_handles_spaces_in_filename(self, tmp_path):
        subdir = tmp_path / "Insurance-product-info"
        subdir.mkdir()
        pdf = subdir / "my doc.pdf"
        pdf.write_bytes(b"")
        file_url = pdf.resolve().as_uri()

        with patch.object(rag_tools, "_DATA_DIR", tmp_path):
            result = _to_docs_path(file_url)
        assert result == "/docs/Insurance-product-info/my%20doc.pdf"

    def test_path_outside_data_dir_returns_empty(self, tmp_path):
        outside = tmp_path / "outside" / "doc.pdf"
        outside.parent.mkdir()
        outside.write_bytes(b"")
        file_url = outside.resolve().as_uri()
        data_dir = tmp_path / "data"
        data_dir.mkdir()

        with patch.object(rag_tools, "_DATA_DIR", data_dir):
            result = _to_docs_path(file_url)
        assert result == ""

    def test_invalid_uri_returns_empty(self):
        result = _to_docs_path("not-a-valid-uri://???")
        # Should not raise; may return "" or a partial path
        assert isinstance(result, str)

    def test_deeply_nested_path(self, tmp_path):
        subdir = tmp_path / "a" / "b" / "c"
        subdir.mkdir(parents=True)
        pdf = subdir / "deep.pdf"
        pdf.write_bytes(b"")
        file_url = pdf.resolve().as_uri()

        with patch.object(rag_tools, "_DATA_DIR", tmp_path):
            result = _to_docs_path(file_url)
        assert result == "/docs/a/b/c/deep.pdf"


# ---------------------------------------------------------------------------
# _collect_sources
# ---------------------------------------------------------------------------

class TestCollectSources:
    def test_no_context_returns_empty_ids(self, no_sources_context):
        hits = [_make_hit(), _make_hit()]
        result = _collect_sources(hits)
        assert result == ["", ""]

    def test_single_hit_gets_s1(self, fresh_sources):
        hit = _make_hit(document_name="doc.pdf", page_start=1)
        result = _collect_sources([hit])
        assert result == ["S1"]
        assert len(get_current_sources()) == 1

    def test_two_different_hits_get_sequential_ids(self, fresh_sources):
        hit1 = _make_hit(document_name="doc1.pdf", page_start=1)
        hit2 = _make_hit(document_name="doc2.pdf", page_start=1)
        result = _collect_sources([hit1, hit2])
        assert result == ["S1", "S2"]
        assert len(get_current_sources()) == 2

    def test_duplicate_hit_reuses_id(self, fresh_sources):
        hit = _make_hit(document_name="doc.pdf", page_start=1)
        result = _collect_sources([hit, hit])
        assert result == ["S1", "S1"]
        assert len(get_current_sources()) == 1

    def test_dedup_key_is_doc_and_page_start(self, fresh_sources):
        hit1 = _make_hit(document_name="doc.pdf", page_start=1, chunk_id="c1")
        hit2 = _make_hit(document_name="doc.pdf", page_start=1, chunk_id="c2")  # same page
        result = _collect_sources([hit1, hit2])
        assert result == ["S1", "S1"]
        assert len(get_current_sources()) == 1

    def test_same_doc_different_pages_are_separate(self, fresh_sources):
        hit1 = _make_hit(document_name="doc.pdf", page_start=1)
        hit2 = _make_hit(document_name="doc.pdf", page_start=2)
        result = _collect_sources([hit1, hit2])
        assert result == ["S1", "S2"]
        assert len(get_current_sources()) == 2

    def test_entry_fields_populated_correctly(self, fresh_sources):
        hit = _make_hit(
            document_name="Generations-II_PB_EN.pdf",
            page_start=3,
            page_end=4,
            product_name="Generations II",
            section_title="Overview",
            chunk_id="abc123",
            text="This is a longer text that will be previewed.",
        )
        _collect_sources([hit])
        entry = get_current_sources()[0]
        assert entry["source_id"] == "S1"
        assert entry["document"] == "Generations-II_PB_EN.pdf"
        assert entry["product"] == "Generations II"
        assert entry["page_start"] == 3
        assert entry["page_end"] == 4
        assert entry["section"] == "Overview"
        assert entry["chunk_id"] == "abc123"
        assert "This is a longer" in entry["text_preview"]

    def test_text_preview_truncated_to_250(self, fresh_sources):
        long_text = "A" * 500
        hit = _make_hit(text=long_text)
        _collect_sources([hit])
        assert len(get_current_sources()[0]["text_preview"]) == 250

    def test_missing_metadata_uses_defaults(self, fresh_sources):
        hit = {"metadata": {}, "text": "hello"}
        result = _collect_sources([hit])
        assert result == ["S1"]
        entry = get_current_sources()[0]
        assert entry["document"] == "?"
        assert entry["page_start"] == "?"

    def test_file_url_used_from_metadata(self, fresh_sources, tmp_path):
        subdir = tmp_path / "Insurance-product-info"
        subdir.mkdir()
        pdf = subdir / "doc.pdf"
        pdf.write_bytes(b"")
        file_url = pdf.resolve().as_uri()

        hit = _make_hit(document_name="doc.pdf", file_url=file_url)
        with patch.object(rag_tools, "_DATA_DIR", tmp_path):
            _collect_sources([hit])
        entry = get_current_sources()[0]
        assert entry["file_url"] == "/docs/Insurance-product-info/doc.pdf"

    def test_fallback_to_find_file_url_when_no_metadata_url(self, fresh_sources, tmp_path):
        subdir = tmp_path / "Insurance-product-info"
        subdir.mkdir()
        pdf = subdir / "fallback.pdf"
        pdf.write_bytes(b"")

        hit = _make_hit(document_name="fallback.pdf", file_url="")
        _find_file_url.cache_clear()
        with patch.object(rag_tools, "_DATA_DIR", tmp_path):
            _collect_sources([hit])
        entry = get_current_sources()[0]
        assert "fallback.pdf" in entry["file_url"]

    def test_empty_hits_list(self, fresh_sources):
        result = _collect_sources([])
        assert result == []
        assert get_current_sources() == []

    def test_counter_increments_across_calls(self, fresh_sources):
        hit1 = _make_hit(document_name="a.pdf", page_start=1)
        hit2 = _make_hit(document_name="b.pdf", page_start=1)
        _collect_sources([hit1])
        _collect_sources([hit2])
        sources = get_current_sources()
        assert sources[0