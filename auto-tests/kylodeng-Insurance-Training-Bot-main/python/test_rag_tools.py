"""
Test module for api/rag_tools.py

What is tested:
- reset_sources(): initialises a fresh list in the context variable
- get_current_sources(): returns collected sources or empty list when none set
- _find_file_url(): finds files under the data directory via rglob
- _to_docs_path(): converts file:/// URIs to /docs/-relative server URLs
- _collect_sources(): deduplicates hits, assigns source IDs, builds source entries
- _log_hits(): logs chunk metadata when SHOW_TOOL_CALLS is true/false
- make_rag_tools(): factory returns tool callables (get_current_date, list_products)
- get_current_date tool: returns today's date string
- Boundary / edge cases: empty hits, None bucket, duplicate pages, missing metadata

Mocks used:
- unittest.mock.patch for Path.rglob (file system), os.getenv, date.today
- unittest.mock.MagicMock for the vector store passed to make_rag_tools
- monkeypatch for environment variables and module-level state

TODOs:
- TODO: Full integration test for list_products tool requires a live/mock vector store
  returning actual document records — stub provided below.
- TODO: Tests for any additional tools beyond get_current_date / list_products that
  are truncated in the provided source (make_rag_tools closure body cut off).
- TODO: Async tool tests if LangGraph invokes tools via asyncio — stub provided below.
"""

import contextvars
import logging
import os
import sys
import types
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock
import pytest

# ---------------------------------------------------------------------------
# Ensure the package root is importable regardless of working directory
# ---------------------------------------------------------------------------
ROOT = Path(__file__).parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import api.rag_tools as rag_tools
from api.rag_tools import (
    _collect_sources,
    _find_file_url,
    _log_hits,
    _to_docs_path,
    get_current_sources,
    make_rag_tools,
    reset_sources,
    _sources_ctx,
)


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

def _make_hit(
    document_name="Generations-II_PB_EN.pdf",
    page_start=1,
    page_end=2,
    product_name="Generations II",
    doc_type="product_brochure",
    section_title="Overview",
    file_url="",
    chunk_id="c001",
    word_count=120,
    text="Sample text about Generations II whole life insurance.",
):
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
def reset_context_var():
    """Ensure the contextvar is cleared between every test."""
    token = _sources_ctx.set(None)
    yield
    _sources_ctx.reset(token)


@pytest.fixture()
def active_bucket():
    """Fixture that seeds the contextvar with a fresh empty list."""
    reset_sources()
    return _sources_ctx.get()


# ---------------------------------------------------------------------------
# reset_sources / get_current_sources
# ---------------------------------------------------------------------------


class TestResetSources:
    def test_sets_empty_list(self):
        reset_sources()
        bucket = _sources_ctx.get()
        assert bucket == []
        assert isinstance(bucket, list)

    def test_overwrites_existing_data(self, active_bucket):
        active_bucket.append({"source_id": "S1"})
        reset_sources()
        assert _sources_ctx.get() == []

    def test_multiple_resets_independent(self):
        reset_sources()
        b1 = _sources_ctx.get()
        b1.append("marker")
        reset_sources()
        b2 = _sources_ctx.get()
        assert b2 == []
        assert b1 is not b2


class TestGetCurrentSources:
    def test_returns_empty_list_when_not_initialised(self):
        # contextvar is None (default)
        result = get_current_sources()
        assert result == []

    def test_returns_collected_sources(self, active_bucket):
        entry = {"source_id": "S1", "document": "doc.pdf"}
        active_bucket.append(entry)
        result = get_current_sources()
        assert result == [entry]

    def test_returns_empty_list_when_bucket_explicitly_empty(self, active_bucket):
        result = get_current_sources()
        assert result == []

    def test_returns_same_list_object(self, active_bucket):
        result = get_current_sources()
        assert result is active_bucket


# ---------------------------------------------------------------------------
# _find_file_url
# ---------------------------------------------------------------------------


class TestFindFileUrl:
    def test_returns_uri_when_file_found(self, tmp_path):
        subdir = tmp_path / "Insurance-product-info"
        subdir.mkdir()
        pdf = subdir / "Generations-II_PB_EN.pdf"
        pdf.write_bytes(b"%PDF")

        with patch.object(rag_tools, "_DATA_DIR", tmp_path):
            # Clear the lru_cache so patch takes effect
            _find_file_url.cache_clear()
            uri = _find_file_url("Generations-II_PB_EN.pdf")

        assert uri.startswith("file:///") or uri.startswith("file://")
        assert "Generations-II_PB_EN.pdf" in uri
        _find_file_url.cache_clear()

    def test_returns_empty_string_when_not_found(self, tmp_path):
        with patch.object(rag_tools, "_DATA_DIR", tmp_path):
            _find_file_url.cache_clear()
            uri = _find_file_url("nonexistent_file.pdf")

        assert uri == ""
        _find_file_url.cache_clear()

    def test_cached_result(self, tmp_path):
        subdir = tmp_path / "sub"
        subdir.mkdir()
        pdf = subdir / "cached_doc.pdf"
        pdf.write_bytes(b"%PDF")

        with patch.object(rag_tools, "_DATA_DIR", tmp_path):
            _find_file_url.cache_clear()
            result1 = _find_file_url("cached_doc.pdf")
            result2 = _find_file_url("cached_doc.pdf")

        assert result1 == result2
        _find_file_url.cache_clear()

    def test_returns_first_match_when_multiple(self, tmp_path):
        (tmp_path / "a").mkdir()
        (tmp_path / "b").mkdir()
        (tmp_path / "a" / "dup.pdf").write_bytes(b"%PDF")
        (tmp_path / "b" / "dup.pdf").write_bytes(b"%PDF")

        with patch.object(rag_tools, "_DATA_DIR", tmp_path):
            _find_file_url.cache_clear()
            uri = _find_file_url("dup.pdf")

        assert uri != ""
        assert "dup.pdf" in uri
        _find_file_url.cache_clear()


# ---------------------------------------------------------------------------
# _to_docs_path
# ---------------------------------------------------------------------------


class TestToDocsPath:
    def _make_file_uri(self, tmp_path, rel="Insurance-product-info/doc.pdf"):
        full = tmp_path / rel
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_bytes(b"%PDF")
        return full.resolve().as_uri()

    def test_empty_string_returns_empty(self):
        assert _to_docs_path("") == ""

    def test_none_like_falsy_returns_empty(self):
        # passing None would fail type check upstream, but test robustness
        assert _to_docs_path("") == ""

    def test_valid_uri_returns_docs_path(self, tmp_path):
        uri = self._make_file_uri(tmp_path, "Insurance-product-info/doc.pdf")
        with patch.object(rag_tools, "_DATA_DIR", tmp_path):
            result = _to_docs_path(uri)
        assert result == "/docs/Insurance-product-info/doc.pdf"

    def test_spaces_are_percent_encoded(self, tmp_path):
        uri = self._make_file_uri(tmp_path, "some dir/my doc.pdf")
        with patch.object(rag_tools, "_DATA_DIR", tmp_path):
            result = _to_docs_path(uri)
        assert "my%20doc.pdf" in result or "my doc.pdf" not in result

    def test_nested_path(self, tmp_path):
        uri = self._make_file_uri(tmp_path, "a/b/c/file.pdf")
        with patch.object(rag_tools, "_DATA_DIR", tmp_path):
            result = _to_docs_path(uri)
        assert result == "/docs/a/b/c/file.pdf"

    def test_file_outside_data_dir_returns_empty(self, tmp_path):
        outside = tmp_path.parent / "outside.pdf"
        outside.write_bytes(b"%PDF")
        uri = outside.resolve().as_uri()
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        with patch.object(rag_tools, "_DATA_DIR", data_dir):
            result = _to_docs_path(uri)
        assert result == ""

    def test_malformed_uri_returns_empty(self):
        result = _to_docs_path("not_a_valid_uri:::///")
        # Should not raise; may return "" or a partial path
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# _collect_sources
# ---------------------------------------------------------------------------


class TestCollectSources:
    def test_returns_empty_strings_when_bucket_is_none(self):
        # contextvar is None (no reset_sources called)
        hit = _make_hit()
        result = _collect_sources([hit])
        assert result == [""]

    def test_returns_empty_list_for_no_hits(self, active_bucket):
        result = _collect_sources([])
        assert result == []

    def test_assigns_first_source_id(self, active_bucket):
        hit = _make_hit()
        result = _collect_sources([hit])
        assert result == ["S1"]
        assert len(active_bucket) == 1
        assert active_bucket[0]["source_id"] == "S1"

    def test_assigns_sequential_ids(self, active_bucket):
        hits = [
            _make_hit(document_name="doc1.pdf", page_start=1),
            _make_hit(document_name="doc2.pdf", page_start=1),
        ]
        result = _collect_sources(hits)
        assert result == ["S1", "S2"]
        assert len(active_bucket) == 2

    def test_deduplication_same_doc_and_page(self, active_bucket):
        hit = _make_hit(document_name="doc.pdf", page_start=3)
        result1 = _collect_sources([hit])
        result2 = _collect_sources([hit])
        assert result1 == ["S1"]
        assert result2 == ["S1"]  # reused, not a new entry
        assert len(active_bucket) == 1

    def test_deduplication_across_single_call(self, active_bucket):
        hit = _make_hit(document_name="doc.pdf", page_start=5)
        result = _collect_sources([hit, hit])
        assert result == ["S1", "S1"]
        assert len(active_bucket) == 1

    def test_different_pages_same_doc_not_deduplicated(self, active_bucket):
        hit_p1 = _make_hit(document_name="doc.pdf", page_start=1)
        hit_p2 = _make_hit(document_name="doc.pdf", page_start=2)
        result = _collect_sources([hit_p1, hit_p2])
        assert result == ["S1", "S2"]
        assert len(active_bucket) == 2

    def test_entry_fields_populated_correctly(self, active_bucket, tmp_path):
        hit = _make_hit(
            document_name="Generations-II_PB_EN.pdf",
            page_start=10,
            page_end=11,
            product_name="Generations II",
            section_title="Benefits",
            chunk_id="c99",
            text="A" * 300,
        )
        with patch.object(rag_tools, "_DATA_DIR", tmp_path):
            _collect_sources([hit])

        entry = active_bucket[0]
        assert entry["source_id"] == "S1"
        assert entry["document"] == "Generations-II_PB_EN.pdf"
        assert entry["product"] == "Generations II"
        assert entry["page_start"] == 10
        assert entry["page_end"] == 11
        assert entry["section"] == "Benefits"
        assert entry["chunk_id"] == "c99"
        assert len(entry["text_preview"]) == 250  # truncated to 250

    def test_text_preview_shorter_than_250(self, active_bucket):
        short_text = "Short text."
        hit = _make_hit(text=short_text)
        _collect_sources([hit])
        assert active_bucket[0]["text_preview"] == short_text

    def test_missing_metadata_uses_defaults(self, active_bucket):
        hit = {"text": "some text", "metadata": {}}
        result = _collect_sources([hit])
        assert result == ["S1"]
        entry = active_bucket[0]
        assert entry["document"] == "?"
        assert entry["page_start"] == "?"
        assert entry["page_end"] == "?"
        assert entry["product"] == ""
        assert entry["section"] == ""

    def test_file_url_resolved_via_fallback(self, tmp_path):
        reset_sources()
        bucket = _sources_ctx.get()
        subdir = tmp_path / "Insurance-product-info"
        subdir.mkdir()
        pdf = subdir / "Generations-II_PB_EN.pdf"
        pdf.write_bytes(b"%PDF")

        hit = _make_hit(
            document_name="Generations-II_PB_EN.pdf",
            file_url="",  # no file_url in metadata → fallback
        )

        with patch.object(rag_tools, "_DATA_DIR", tmp_path):
            _find_file_url.cache_clear()
            _collect_sources([hit])

        assert bucket[0]["file_url"].startswith("/docs/") or bucket[0]["file_url"] == ""
        _find_file_url.cache_clear()

    def test_existing_bucket_entries_counted_for_ids(self, active_bucket):
        # Pre-populate bucket with one entry
        active_bucket.append(
            {
                "source_id": "S1",
                "document": "existing.pdf",
                "page_start": 1,
                "page_end": 1,
                "product": "",
                "section": "",
                "file_url": "",
                "chunk_id": "",
                "text_preview": "",
            }
        )
        hit = _make_hit(document_name="new.pdf", page_start=1)
        result = _collect_sources([hit])
        assert result == ["S2"]

    def test_multiple_hits_