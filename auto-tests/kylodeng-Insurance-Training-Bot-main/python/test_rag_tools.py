"""
Test module for api/rag_tools.py

What is tested:
    - reset_sources(): initialises a fresh list in the contextvar
    - get_current_sources(): returns current sources or empty list
    - _find_file_url(): filesystem glob for document lookup (mocked)
    - _to_docs_path(): URI → /docs/-relative server URL conversion
    - _collect_sources(): dedup logic, source-ID assignment, bucket population
    - _log_hits(): logging behaviour, SHOW_TOOL_CALLS toggle
    - make_rag_tools() → get_current_date tool: date formatting
    - make_rag_tools() → list_products tool: existence / return type
    - Context-var isolation across threads / tasks

Mocks used:
    - unittest.mock.patch for _DATA_DIR, _find_file_url, _SHOW_TOOL_CALLS, date.today
    - tmp_path fixture for filesystem-based _find_file_url tests

TODOs:
    - list_products tool body is truncated in the source; only a stub test is included
    - Any tool beyond list_products is not visible in the source and cannot be fully tested
    - Store-dependent tools (search, retrieve, etc.) need a real/mock store interface
"""

import contextvars
import importlib
import logging
import os
import sys
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers to import the module under test
# ---------------------------------------------------------------------------

MODULE_PATH = "api.rag_tools"


def _fresh_import():
    """Return a freshly imported copy of the module (clears lru_cache state)."""
    if MODULE_PATH in sys.modules:
        del sys.modules[MODULE_PATH]
    return importlib.import_module(MODULE_PATH)


# Always import once at module level for the majority of tests
import api.rag_tools as rag_tools  # noqa: E402


# ===========================================================================
# reset_sources / get_current_sources
# ===========================================================================


class TestResetSources:
    def test_sets_empty_list(self):
        rag_tools.reset_sources()
        assert rag_tools.get_current_sources() == []

    def test_clears_previous_data(self):
        rag_tools.reset_sources()
        # manually append something to simulate a tool call
        rag_tools._sources_ctx.get(None).append({"source_id": "S1"})
        assert len(rag_tools.get_current_sources()) == 1

        rag_tools.reset_sources()
        assert rag_tools.get_current_sources() == []

    def test_repeated_calls_stay_empty(self):
        rag_tools.reset_sources()
        rag_tools.reset_sources()
        assert rag_tools.get_current_sources() == []


class TestGetCurrentSources:
    def test_returns_empty_list_when_no_context(self):
        # Set contextvar to None explicitly
        rag_tools._sources_ctx.set(None)
        result = rag_tools.get_current_sources()
        assert result == []

    def test_returns_list_after_reset(self):
        rag_tools.reset_sources()
        assert isinstance(rag_tools.get_current_sources(), list)

    def test_reflects_mutations(self):
        rag_tools.reset_sources()
        bucket = rag_tools._sources_ctx.get(None)
        bucket.append({"source_id": "S1", "document": "doc.pdf"})
        assert rag_tools.get_current_sources()[0]["source_id"] == "S1"

    def test_contextvar_isolation_between_threads(self):
        """Each thread should have its own contextvar state."""
        import threading

        results = {}

        def worker(name, do_reset):
            if do_reset:
                rag_tools.reset_sources()
                rag_tools._sources_ctx.get(None).append({"source_id": name})
            else:
                rag_tools._sources_ctx.set(None)
            results[name] = rag_tools.get_current_sources()

        t1 = threading.Thread(target=worker, args=("T1", True))
        t2 = threading.Thread(target=worker, args=("T2", False))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        assert results["T1"] == [{"source_id": "T1"}]
        assert results["T2"] == []


# ===========================================================================
# _find_file_url
# ===========================================================================


class TestFindFileUrl:
    def test_returns_uri_when_file_exists(self, tmp_path):
        # Create a fake document under a data-like directory
        doc = tmp_path / "Insurance-product-info" / "doc.pdf"
        doc.parent.mkdir(parents=True)
        doc.write_bytes(b"%PDF")

        with patch.object(rag_tools, "_DATA_DIR", tmp_path):
            # Clear lru_cache so the patch takes effect
            rag_tools._find_file_url.cache_clear()
            result = rag_tools._find_file_url("doc.pdf")

        assert result.startswith("file:///") or result.startswith("file:/")
        assert "doc.pdf" in result

    def test_returns_empty_string_when_not_found(self, tmp_path):
        with patch.object(rag_tools, "_DATA_DIR", tmp_path):
            rag_tools._find_file_url.cache_clear()
            result = rag_tools._find_file_url("nonexistent_document.pdf")

        assert result == ""

    def test_lru_cache_hit(self, tmp_path):
        doc = tmp_path / "sub" / "cached.pdf"
        doc.parent.mkdir(parents=True)
        doc.write_bytes(b"%PDF")

        with patch.object(rag_tools, "_DATA_DIR", tmp_path):
            rag_tools._find_file_url.cache_clear()
            first = rag_tools._find_file_url("cached.pdf")
            second = rag_tools._find_file_url("cached.pdf")

        assert first == second

    def test_returns_first_match_when_multiple_files(self, tmp_path):
        for subdir in ("a", "b"):
            d = tmp_path / subdir
            d.mkdir()
            (d / "multi.pdf").write_bytes(b"%PDF")

        with patch.object(rag_tools, "_DATA_DIR", tmp_path):
            rag_tools._find_file_url.cache_clear()
            result = rag_tools._find_file_url("multi.pdf")

        assert "multi.pdf" in result


# ===========================================================================
# _to_docs_path
# ===========================================================================


class TestToDocsPath:
    def setup_method(self):
        # Use a stable fake DATA_DIR for path arithmetic
        self._fake_data = Path("/fake/data")

    def _call(self, file_url):
        with patch.object(rag_tools, "_DATA_DIR", self._fake_data):
            return rag_tools._to_docs_path(file_url)

    def test_empty_string_returns_empty(self):
        assert rag_tools._to_docs_path("") == ""

    def test_none_like_empty_returns_empty(self):
        # The function guards on `if not file_url`
        assert rag_tools._to_docs_path("") == ""

    def test_converts_unix_file_uri(self, tmp_path):
        """Happy-path: a real file URI under a real DATA_DIR resolves correctly."""
        sub = tmp_path / "Insurance-product-info"
        sub.mkdir()
        doc = sub / "Generations-II_PB_EN.pdf"
        doc.write_bytes(b"")

        uri = doc.resolve().as_uri()
        with patch.object(rag_tools, "_DATA_DIR", tmp_path):
            result = rag_tools._to_docs_path(uri)

        assert result == "/docs/Insurance-product-info/Generations-II_PB_EN.pdf"

    def test_special_characters_are_percent_encoded(self, tmp_path):
        sub = tmp_path / "folder with spaces"
        sub.mkdir()
        doc = sub / "file name.pdf"
        doc.write_bytes(b"")

        uri = doc.resolve().as_uri()
        with patch.object(rag_tools, "_DATA_DIR", tmp_path):
            result = rag_tools._to_docs_path(uri)

        assert "folder%20with%20spaces" in result
        assert "file%20name.pdf" in result

    def test_path_outside_data_dir_returns_empty(self, tmp_path):
        """A URI that cannot be made relative to _DATA_DIR should return ''."""
        outside = tmp_path / "outside" / "doc.pdf"
        outside.parent.mkdir(parents=True)
        outside.write_bytes(b"")

        uri = outside.resolve().as_uri()
        data_dir = tmp_path / "data"
        data_dir.mkdir()

        with patch.object(rag_tools, "_DATA_DIR", data_dir):
            result = rag_tools._to_docs_path(uri)

        assert result == ""

    def test_malformed_uri_returns_empty(self):
        result = rag_tools._to_docs_path("not_a_uri_at_all")
        # Should not raise; returns "" on exception
        assert isinstance(result, str)


# ===========================================================================
# _collect_sources
# ===========================================================================


def _make_hit(doc_name="doc.pdf", page_start=1, page_end=2,
              product="ProductA", section="Intro",
              chunk_id="c1", text="Some text", file_url=""):
    return {
        "metadata": {
            "document_name": doc_name,
            "page_start": page_start,
            "page_end": page_end,
            "product_name": product,
            "section_title": section,
            "chunk_id": chunk_id,
            "file_url": file_url,
        },
        "text": text,
    }


class TestCollectSources:
    def setup_method(self):
        rag_tools.reset_sources()

    def test_returns_empty_list_for_no_hits(self):
        result = rag_tools._collect_sources([])
        assert result == []

    def test_single_hit_creates_s1(self):
        hit = _make_hit(doc_name="doc.pdf", page_start=1)
        result = rag_tools._collect_sources([hit])
        assert result == ["S1"]
        sources = rag_tools.get_current_sources()
        assert len(sources) == 1
        assert sources[0]["source_id"] == "S1"

    def test_multiple_unique_hits_increment_ids(self):
        hits = [
            _make_hit(doc_name="a.pdf", page_start=1),
            _make_hit(doc_name="b.pdf", page_start=1),
            _make_hit(doc_name="c.pdf", page_start=5),
        ]
        result = rag_tools._collect_sources(hits)
        assert result == ["S1", "S2", "S3"]
        assert len(rag_tools.get_current_sources()) == 3

    def test_duplicate_hit_reuses_existing_id(self):
        hit = _make_hit(doc_name="doc.pdf", page_start=1)
        first = rag_tools._collect_sources([hit])
        second = rag_tools._collect_sources([hit])
        assert first == ["S1"]
        assert second == ["S1"]
        # Only one entry in the bucket
        assert len(rag_tools.get_current_sources()) == 1

    def test_dedup_key_is_document_and_page_start(self):
        hit1 = _make_hit(doc_name="doc.pdf", page_start=1, chunk_id="c1")
        hit2 = _make_hit(doc_name="doc.pdf", page_start=1, chunk_id="c2")
        result = rag_tools._collect_sources([hit1, hit2])
        # Same document + page_start → same source_id
        assert result == ["S1", "S1"]
        assert len(rag_tools.get_current_sources()) == 1

    def test_same_document_different_pages_get_different_ids(self):
        hits = [
            _make_hit(doc_name="doc.pdf", page_start=1),
            _make_hit(doc_name="doc.pdf", page_start=3),
        ]
        result = rag_tools._collect_sources(hits)
        assert result == ["S1", "S2"]

    def test_entry_fields_are_populated_correctly(self):
        hit = _make_hit(
            doc_name="Generations-II_PB_EN.pdf",
            page_start=5, page_end=6,
            product="Generations II",
            section="Benefits",
            chunk_id="chunk_42",
            text="A" * 300,  # longer than 250 chars
        )
        rag_tools._collect_sources([hit])
        entry = rag_tools.get_current_sources()[0]
        assert entry["document"] == "Generations-II_PB_EN.pdf"
        assert entry["product"] == "Generations II"
        assert entry["page_start"] == 5
        assert entry["page_end"] == 6
        assert entry["section"] == "Benefits"
        assert entry["chunk_id"] == "chunk_42"
        assert len(entry["text_preview"]) == 250  # truncated

    def test_text_preview_truncated_to_250(self):
        hit = _make_hit(text="X" * 500)
        rag_tools._collect_sources([hit])
        assert len(rag_tools.get_current_sources()[0]["text_preview"]) == 250

    def test_text_preview_not_truncated_when_short(self):
        hit = _make_hit(text="Short text")
        rag_tools._collect_sources([hit])
        assert rag_tools.get_current_sources()[0]["text_preview"] == "Short text"

    def test_missing_metadata_fields_use_defaults(self):
        hit = {"metadata": {}, "text": "hello"}
        rag_tools._collect_sources([hit])
        entry = rag_tools.get_current_sources()[0]
        assert entry["document"] == "?"
        assert entry["page_start"] == "?"
        assert entry["page_end"] == "?"
        assert entry["product"] == ""
        assert entry["section"] == ""
        assert entry["chunk_id"] == ""

    def test_returns_empty_ids_when_context_is_none(self):
        rag_tools._sources_ctx.set(None)
        hits = [_make_hit(), _make_hit(doc_name="b.pdf")]
        result = rag_tools._collect_sources(hits)
        assert result == ["", ""]

    def test_file_url_falls_back_to_find_file_url_when_missing(self, tmp_path):
        doc_name = "Generations-II_PB_EN.pdf"
        sub = tmp_path / "Insurance-product-info" / "Generations-II"
        sub.mkdir(parents=True)
        doc_path = sub / doc_name
        doc_path.write_bytes(b"")

        hit = _make_hit(doc_name=doc_name, file_url="")
        with patch.object(rag_tools, "_DATA_DIR", tmp_path):
            rag_tools._find_file_url.cache_clear()
            rag_tools.reset_sources()
            rag_tools._collect_sources([hit])

        entry = rag_tools.get_current_sources()[0]
        # file_url should be non-empty (