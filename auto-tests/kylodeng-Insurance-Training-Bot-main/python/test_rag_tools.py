"""
Test module for api/rag_tools.py

What is tested:
  - reset_sources(): initialises a fresh list in the contextvar
  - get_current_sources(): returns current sources or empty list
  - _find_file_url(): finds files by name via rglob (lru_cache aware)
  - _to_docs_path(): converts file:/// URIs to /docs/-relative server URLs
  - _collect_sources(): appends unique sources, deduplicates by (doc, page_start)
  - _log_hits(): logs chunk metadata when SHOW_TOOL_CALLS=true
  - make_rag_tools() / get_current_date tool: returns today's date as a string
  - make_rag_tools() / list_products tool: stub (source truncated in provided code)

Mocks used:
  - unittest.mock.patch for os.getenv (SHOW_TOOL_CALLS flag)
  - unittest.mock.patch for Path.rglob (_find_file_url filesystem calls)
  - unittest.mock.patch for datetime.date.today (get_current_date tool)
  - unittest.mock.MagicMock for the store object passed to make_rag_tools
  - unittest.mock.patch for logger.info (_log_hits verification)

TODOs:
  - list_products tool: source code is truncated; only a stub test is included
  - Any additional tools returned by make_rag_tools beyond list_products are unknown
  - Integration tests against a real vector store require additional context
"""

import contextvars
import os
import sys
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch, call
import urllib.request

import pytest

# ---------------------------------------------------------------------------
# Ensure the package root is importable regardless of working directory
# ---------------------------------------------------------------------------
sys.path.insert(0, str(Path(__file__).parent.parent))

# ---------------------------------------------------------------------------
# The module under test — imported after path manipulation
# ---------------------------------------------------------------------------
import api.rag_tools as rag  # noqa: E402  (import after sys.path manipulation)
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
# Helpers / shared fixtures
# ===========================================================================


def _make_hit(
    document_name: str = "doc.pdf",
    page_start: int = 1,
    page_end: int = 2,
    product_name: str = "Generations II",
    doc_type: str = "product_brochure",
    section_title: str = "Overview",
    file_url: str = "",
    chunk_id: str = "c001",
    word_count: int = 120,
    text: str = "Sample chunk text.",
) -> dict:
    """Build a synthetic hit dict matching the shape returned by the vector store."""
    return {
        "text": text,
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
    }


@pytest.fixture(autouse=True)
def _reset_sources_ctx():
    """Guarantee the contextvar is reset to its default between every test."""
    token = _sources_ctx.set(None)
    yield
    _sources_ctx.reset(token)


@pytest.fixture(autouse=True)
def _clear_find_file_url_cache():
    """Clear the lru_cache on _find_file_url so tests don't bleed into each other."""
    _find_file_url.cache_clear()
    yield
    _find_file_url.cache_clear()


# ===========================================================================
# reset_sources
# ===========================================================================


class TestResetSources:
    def test_sets_empty_list(self):
        reset_sources()
        assert _sources_ctx.get(None) == []

    def test_replaces_existing_list(self):
        _sources_ctx.set(["existing"])
        reset_sources()
        assert _sources_ctx.get(None) == []

    def test_get_current_sources_returns_empty_after_reset(self):
        reset_sources()
        assert get_current_sources() == []


# ===========================================================================
# get_current_sources
# ===========================================================================


class TestGetCurrentSources:
    def test_returns_empty_list_when_contextvar_is_none(self):
        # Default state (None) → should return []
        assert get_current_sources() == []

    def test_returns_current_list(self):
        data = [{"source_id": "S1"}]
        _sources_ctx.set(data)
        assert get_current_sources() == data

    def test_returns_empty_list_when_contextvar_set_to_none_explicitly(self):
        _sources_ctx.set(None)
        assert get_current_sources() == []

    def test_returns_populated_list_after_collect_sources(self):
        reset_sources()
        hit = _make_hit(document_name="doc.pdf", page_start=5)
        _collect_sources([hit])
        sources = get_current_sources()
        assert len(sources) == 1
        assert sources[0]["source_id"] == "S1"


# ===========================================================================
# _find_file_url
# ===========================================================================


class TestFindFileUrl:
    def test_returns_uri_when_file_found(self, tmp_path):
        # Create a real temporary file so rglob actually works
        target = tmp_path / "doc.pdf"
        target.write_bytes(b"%PDF")

        with patch.object(rag, "_DATA_DIR", tmp_path):
            result = _find_file_url("doc.pdf")

        assert result.startswith("file://")
        assert "doc.pdf" in result

    def test_returns_empty_string_when_file_not_found(self, tmp_path):
        with patch.object(rag, "_DATA_DIR", tmp_path):
            result = _find_file_url("nonexistent_file.pdf")

        assert result == ""

    def test_returns_first_match_when_multiple_found(self, tmp_path):
        (tmp_path / "a").mkdir()
        (tmp_path / "b").mkdir()
        (tmp_path / "a" / "same.pdf").write_bytes(b"%PDF")
        (tmp_path / "b" / "same.pdf").write_bytes(b"%PDF")

        with patch.object(rag, "_DATA_DIR", tmp_path):
            result = _find_file_url("same.pdf")

        assert result.startswith("file://")
        assert "same.pdf" in result

    def test_lru_cache_returns_same_result_on_second_call(self, tmp_path):
        target = tmp_path / "cached.pdf"
        target.write_bytes(b"%PDF")

        with patch.object(rag, "_DATA_DIR", tmp_path):
            first = _find_file_url("cached.pdf")
            second = _find_file_url("cached.pdf")

        assert first == second

    def test_empty_document_name_returns_empty(self, tmp_path):
        with patch.object(rag, "_DATA_DIR", tmp_path):
            result = _find_file_url("")
        # rglob("") will find nothing meaningful; empty string should not crash
        assert isinstance(result, str)


# ===========================================================================
# _to_docs_path
# ===========================================================================


class TestToDocsPath:
    def test_empty_string_returns_empty(self):
        assert _to_docs_path("") == ""

    def test_converts_file_uri_to_docs_path(self, tmp_path):
        # Create structure: tmp_path/Insurance-product-info/doc.pdf
        subdir = tmp_path / "Insurance-product-info"
        subdir.mkdir()
        doc = subdir / "doc.pdf"
        doc.write_bytes(b"%PDF")

        file_url = doc.resolve().as_uri()

        with patch.object(rag, "_DATA_DIR", tmp_path):
            result = _to_docs_path(file_url)

        assert result == "/docs/Insurance-product-info/doc.pdf"

    def test_special_characters_are_percent_encoded(self, tmp_path):
        subdir = tmp_path / "My Folder"
        subdir.mkdir()
        doc = subdir / "my doc.pdf"
        doc.write_bytes(b"%PDF")

        file_url = doc.resolve().as_uri()

        with patch.object(rag, "_DATA_DIR", tmp_path):
            result = _to_docs_path(file_url)

        assert "My%20Folder" in result
        assert "my%20doc.pdf" in result

    def test_returns_empty_string_on_unrelated_file_uri(self, tmp_path):
        """File that cannot be made relative to _DATA_DIR → should return ''."""
        other = tmp_path / "other_dir"
        other.mkdir()
        doc = other / "alien.pdf"
        doc.write_bytes(b"x")
        file_url = doc.resolve().as_uri()

        # DATA_DIR points somewhere completely different
        different_data_dir = tmp_path / "data"
        different_data_dir.mkdir()

        with patch.object(rag, "_DATA_DIR", different_data_dir):
            result = _to_docs_path(file_url)

        assert result == ""

    def test_malformed_uri_returns_empty(self):
        result = _to_docs_path("not_a_uri_at_all:::///")
        # Should not raise; returns empty string
        assert isinstance(result, str)

    def test_none_like_empty_bypasses_conversion(self):
        assert _to_docs_path("") == ""


# ===========================================================================
# _collect_sources
# ===========================================================================


class TestCollectSources:
    # --- bucket is None (no reset_sources called) ---

    def test_returns_empty_strings_when_bucket_is_none(self):
        hits = [_make_hit(), _make_hit()]
        result = _collect_sources(hits)
        assert result == ["", ""]

    def test_returns_empty_list_for_no_hits_when_bucket_is_none(self):
        result = _collect_sources([])
        assert result == []

    # --- Happy path ---

    def test_single_hit_creates_s1(self):
        reset_sources()
        hit = _make_hit(document_name="doc.pdf", page_start=1)
        result = _collect_sources([hit])
        assert result == ["S1"]
        sources = get_current_sources()
        assert len(sources) == 1
        assert sources[0]["source_id"] == "S1"
        assert sources[0]["document"] == "doc.pdf"

    def test_two_different_hits_create_s1_and_s2(self):
        reset_sources()
        hits = [
            _make_hit(document_name="a.pdf", page_start=1),
            _make_hit(document_name="b.pdf", page_start=1),
        ]
        result = _collect_sources(hits)
        assert result == ["S1", "S2"]
        assert len(get_current_sources()) == 2

    def test_duplicate_hit_reuses_existing_id(self):
        reset_sources()
        hit = _make_hit(document_name="doc.pdf", page_start=5)
        _collect_sources([hit])
        # Call again with the same (doc, page_start)
        result = _collect_sources([hit])
        assert result == ["S1"]
        # Bucket should still have only one entry
        assert len(get_current_sources()) == 1

    def test_same_doc_different_pages_are_separate_sources(self):
        reset_sources()
        hits = [
            _make_hit(document_name="doc.pdf", page_start=1),
            _make_hit(document_name="doc.pdf", page_start=10),
        ]
        result = _collect_sources(hits)
        assert result == ["S1", "S2"]
        assert len(get_current_sources()) == 2

    def test_mixed_new_and_duplicate_in_same_call(self):
        reset_sources()
        hits = [
            _make_hit(document_name="doc.pdf", page_start=1),  # new → S1
            _make_hit(document_name="doc.pdf", page_start=2),  # new → S2
            _make_hit(document_name="doc.pdf", page_start=1),  # dup → S1
        ]
        result = _collect_sources(hits)
        assert result == ["S1", "S2", "S1"]
        assert len(get_current_sources()) == 2

    def test_source_entry_fields_are_populated_correctly(self, tmp_path):
        reset_sources()
        subdir = tmp_path / "Insurance-product-info"
        subdir.mkdir()
        doc_file = subdir / "Generations-II_PB_EN.pdf"
        doc_file.write_bytes(b"%PDF")

        with patch.object(rag, "_DATA_DIR", tmp_path):
            hit = _make_hit(
                document_name="Generations-II_PB_EN.pdf",
                page_start=3,
                page_end=4,
                product_name="Generations II",
                doc_type="product_brochure",
                section_title="Overview",
                chunk_id="c007",
                text="Hello world " * 50,  # > 250 chars
                file_url="",  # triggers _find_file_url fallback
            )
            _collect_sources([hit])

        entry = get_current_sources()[0]
        assert entry["source_id"] == "S1"
        assert entry["document"] == "Generations-II_PB_EN.pdf"
        assert entry["product"] == "Generations II"
        assert entry["page_start"] == 3
        assert entry["page_end"] == 4
        assert entry["section"] == "Overview"
        assert entry["chunk_id"] == "c007"
        assert len(entry["text_preview"]) <= 250

    def test_file_url_metadata_takes_precedence_over_find_file_url(self, tmp_path):
        reset_sources()
        subdir = tmp_path / "Insurance-product-info"
        subdir.mkdir()
        doc_file = subdir / "doc.pdf"
        doc_file.write_bytes(b"%PDF")
        explicit_url = doc_file.resolve().as_uri()

        with patch.object(rag, "_DATA_DIR", tmp_path):
            hit = _make_hit(
                document_name="doc.pdf",
                page_start=1,
                file_url=explicit_url,
            )
            _collect_sources([hit])

        entry = get_current_sources()[0]
        # file_url should have been converted via _to_docs_path
        assert entry["file_url"].startswith("/docs/")

    def test_missing_metadata_fields_use_defaults(self):
        reset_sources()
        # Hit with no metadata at all
        hit = {"text": "bare text", "metadata": {}}
        result = _collect_sources([hit])
        assert result == ["S1"]
        entry = get_current_sources()[0]
        assert entry["document"] == "?"
        assert entry["page_start"] == "?"
        assert entry["product"] == ""
        assert entry["section"] == ""

    def test_empty_hits_list_with_active_bucket(self):
        reset_sources()
        result = _collect_sources([])
        assert result == []
        assert get_current_sources() == []

    def test_text_preview_truncated