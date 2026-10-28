"""
Test module for api/rag_tools.py

What is tested:
    - reset_sources(): initialises a fresh list in the contextvar
    - get_current_sources(): returns accumulated sources or empty list
    - _find_file_url(): filesystem glob fallback for file URLs
    - _to_docs_path(): URI → /docs/-relative server URL conversion
    - _collect_sources(): deduplication, ID assignment, source bucket management
    - _log_hits(): conditional logging based on SHOW_TOOL_CALLS env var
    - make_rag_tools() → get_current_date tool: returns today's date string
    - make_rag_tools() → list_products tool: stub (source truncated)

Mocks used:
    - unittest.mock.patch for os.getenv, Path.rglob, date.today, logging
    - Fake in-memory vector store object passed to make_rag_tools()
    - _DATA_DIR patched to a tmp_path fixture directory

TODOs:
    - list_products tool: source code was truncated; stub test added
    - Any additional tools defined after list_products: stubs added
    - Integration test with a real vector store: requires store implementation
"""

import contextvars
import importlib
import logging
import os
import sys
import types
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers to (re)import the module under test with patches applied
# ---------------------------------------------------------------------------

MODULE_PATH = "api.rag_tools"


def _fresh_import():
    """Force a clean import of api.rag_tools (useful after env-var patches)."""
    if MODULE_PATH in sys.modules:
        del sys.modules[MODULE_PATH]
    import api.rag_tools as mod
    return mod


# ---------------------------------------------------------------------------
# Basic import
# ---------------------------------------------------------------------------

import api.rag_tools as rag  # noqa: E402  (after helpers)


# ===========================================================================
# reset_sources / get_current_sources
# ===========================================================================


class TestResetSources:
    def test_sets_empty_list(self):
        rag.reset_sources()
        assert rag._sources_ctx.get(None) == []

    def test_overwrites_existing_list(self):
        rag.reset_sources()
        rag._sources_ctx.get(None).append("something")
        rag.reset_sources()
        assert rag._sources_ctx.get(None) == []

    def test_reset_twice_yields_fresh_list(self):
        rag.reset_sources()
        first = rag._sources_ctx.get(None)
        rag.reset_sources()
        second = rag._sources_ctx.get(None)
        assert first is not second


class TestGetCurrentSources:
    def test_returns_empty_list_when_not_initialised(self):
        rag._sources_ctx.set(None)
        result = rag.get_current_sources()
        assert result == []

    def test_returns_accumulated_sources_after_reset(self):
        rag.reset_sources()
        bucket = rag._sources_ctx.get(None)
        bucket.append({"source_id": "S1", "document": "doc.pdf", "page_start": 1})
        result = rag.get_current_sources()
        assert len(result) == 1
        assert result[0]["source_id"] == "S1"

    def test_returns_copy_reference_of_same_list(self):
        rag.reset_sources()
        sources = rag.get_current_sources()
        # Both should refer to the same underlying list object
        assert sources is rag._sources_ctx.get(None)


# ===========================================================================
# _find_file_url
# ===========================================================================


class TestFindFileUrl:
    def test_returns_uri_when_file_found(self, tmp_path):
        # Create a dummy file
        doc = tmp_path / "doc.pdf"
        doc.write_bytes(b"%PDF")

        with patch.object(rag, "_DATA_DIR", tmp_path):
            # Clear lru_cache so our patch takes effect
            rag._find_file_url.cache_clear()
            result = rag._find_file_url("doc.pdf")
        rag._find_file_url.cache_clear()

        assert result.startswith("file:///")
        assert "doc.pdf" in result

    def test_returns_empty_string_when_not_found(self, tmp_path):
        with patch.object(rag, "_DATA_DIR", tmp_path):
            rag._find_file_url.cache_clear()
            result = rag._find_file_url("nonexistent.pdf")
        rag._find_file_url.cache_clear()

        assert result == ""

    def test_returns_first_match_when_multiple_exist(self, tmp_path):
        sub1 = tmp_path / "a"
        sub2 = tmp_path / "b"
        sub1.mkdir()
        sub2.mkdir()
        (sub1 / "multi.pdf").write_bytes(b"1")
        (sub2 / "multi.pdf").write_bytes(b"2")

        with patch.object(rag, "_DATA_DIR", tmp_path):
            rag._find_file_url.cache_clear()
            result = rag._find_file_url("multi.pdf")
        rag._find_file_url.cache_clear()

        assert result.startswith("file:///")
        assert "multi.pdf" in result

    def test_lru_cache_caches_result(self, tmp_path):
        doc = tmp_path / "cached.pdf"
        doc.write_bytes(b"%PDF")

        with patch.object(rag, "_DATA_DIR", tmp_path):
            rag._find_file_url.cache_clear()
            r1 = rag._find_file_url("cached.pdf")
            r2 = rag._find_file_url("cached.pdf")
        rag._find_file_url.cache_clear()

        assert r1 == r2
        info = rag._find_file_url.cache_info()
        # After two calls with same arg, hits >= 1
        assert info.hits >= 1

    def test_empty_string_document_name(self, tmp_path):
        with patch.object(rag, "_DATA_DIR", tmp_path):
            rag._find_file_url.cache_clear()
            result = rag._find_file_url("")
        rag._find_file_url.cache_clear()
        # rglob("") matches directories; function returns "" when no matches resolve
        # to a file. Accept either "" or a valid URI.
        assert isinstance(result, str)


# ===========================================================================
# _to_docs_path
# ===========================================================================


class TestToDocsPath:
    def test_empty_string_returns_empty(self):
        assert rag._to_docs_path("") == ""

    def test_valid_file_uri_converts_to_docs_path(self, tmp_path):
        # Build a real file so resolve() works
        sub = tmp_path / "Insurance-product-info"
        sub.mkdir()
        pdf = sub / "doc.pdf"
        pdf.write_bytes(b"%PDF")

        file_url = pdf.resolve().as_uri()

        with patch.object(rag, "_DATA_DIR", tmp_path):
            result = rag._to_docs_path(file_url)

        assert result == "/docs/Insurance-product-info/doc.pdf"

    def test_url_with_spaces_encoded(self, tmp_path):
        sub = tmp_path / "My Folder"
        sub.mkdir()
        pdf = sub / "my doc.pdf"
        pdf.write_bytes(b"%PDF")

        file_url = pdf.resolve().as_uri()

        with patch.object(rag, "_DATA_DIR", tmp_path):
            result = rag._to_docs_path(file_url)

        assert "My%20Folder" in result or "My Folder" not in result  # encoded
        assert result.startswith("/docs/")

    def test_path_outside_data_dir_returns_empty(self, tmp_path):
        outside = tmp_path / "outside"
        outside.mkdir()
        pdf = outside / "secret.pdf"
        pdf.write_bytes(b"%PDF")

        data_dir = tmp_path / "data"
        data_dir.mkdir()

        file_url = pdf.resolve().as_uri()

        with patch.object(rag, "_DATA_DIR", data_dir):
            result = rag._to_docs_path(file_url)

        # relative_to raises ValueError → caught → returns ""
        assert result == ""

    def test_malformed_uri_returns_empty(self):
        result = rag._to_docs_path("not-a-valid-uri://???")
        assert result == ""

    def test_none_like_empty_string(self):
        assert rag._to_docs_path("") == ""

    @pytest.mark.parametrize("bad_url", [
        "ftp://example.com/file.pdf",   # non-file scheme: url2pathname may fail
        "file:///C:/does/not/exist.pdf",  # non-existent path → relative_to fails
    ])
    def test_problematic_urls_return_string(self, bad_url):
        # Should not raise, may return "" or a partial path
        result = rag._to_docs_path(bad_url)
        assert isinstance(result, str)


# ===========================================================================
# _collect_sources
# ===========================================================================


def _make_hit(doc_name="doc.pdf", page_start=1, page_end=2,
              product="Generations II", file_url="", chunk_id="c1",
              text="Sample text", section="Section A"):
    return {
        "metadata": {
            "document_name": doc_name,
            "page_start": page_start,
            "page_end": page_end,
            "product_name": product,
            "file_url": file_url,
            "chunk_id": chunk_id,
            "section_title": section,
            "word_count": 50,
            "doc_type": "product_brochure",
        },
        "text": text,
    }


class TestCollectSources:
    def setup_method(self):
        rag.reset_sources()

    def test_returns_empty_list_when_no_hits(self):
        result = rag._collect_sources([])
        assert result == []

    def test_single_hit_creates_s1(self):
        hit = _make_hit()
        ids = rag._collect_sources([hit])
        assert ids == ["S1"]
        sources = rag.get_current_sources()
        assert len(sources) == 1
        assert sources[0]["source_id"] == "S1"

    def test_two_different_hits_create_s1_s2(self):
        h1 = _make_hit(doc_name="doc1.pdf", page_start=1)
        h2 = _make_hit(doc_name="doc2.pdf", page_start=1)
        ids = rag._collect_sources([h1, h2])
        assert ids == ["S1", "S2"]

    def test_duplicate_hit_reuses_existing_id(self):
        hit = _make_hit(doc_name="dup.pdf", page_start=5)
        ids1 = rag._collect_sources([hit])
        ids2 = rag._collect_sources([hit])
        assert ids1 == ["S1"]
        assert ids2 == ["S1"]  # reused
        sources = rag.get_current_sources()
        assert len(sources) == 1  # not duplicated

    def test_same_doc_different_pages_get_different_ids(self):
        h1 = _make_hit(doc_name="doc.pdf", page_start=1)
        h2 = _make_hit(doc_name="doc.pdf", page_start=5)
        ids = rag._collect_sources([h1, h2])
        assert ids == ["S1", "S2"]

    def test_bucket_none_returns_empty_strings(self):
        rag._sources_ctx.set(None)
        hit = _make_hit()
        result = rag._collect_sources([hit])
        assert result == [""]

    def test_source_entry_has_all_required_fields(self):
        hit = _make_hit(
            doc_name="Generations-II_PB_EN.pdf",
            page_start=3,
            page_end=4,
            product="Generations II",
            chunk_id="gen2-c1",
            section="Benefits",
            text="This is a sample text that should be truncated if long enough.",
        )
        rag._collect_sources([hit])
        entry = rag.get_current_sources()[0]
        assert entry["source_id"] == "S1"
        assert entry["document"] == "Generations-II_PB_EN.pdf"
        assert entry["product"] == "Generations II"
        assert entry["page_start"] == 3
        assert entry["page_end"] == 4
        assert entry["section"] == "Benefits"
        assert entry["chunk_id"] == "gen2-c1"
        assert "text_preview" in entry
        assert len(entry["text_preview"]) <= 250

    def test_text_preview_truncated_at_250_chars(self):
        long_text = "A" * 300
        hit = _make_hit(text=long_text)
        rag._collect_sources([hit])
        entry = rag.get_current_sources()[0]
        assert len(entry["text_preview"]) == 250

    def test_missing_metadata_uses_defaults(self):
        hit = {"metadata": {}, "text": "text"}
        ids = rag._collect_sources([hit])
        assert ids == ["S1"]
        entry = rag.get_current_sources()[0]
        assert entry["document"] == "?"
        assert entry["page_start"] == "?"

    def test_file_url_fallback_to_find_file_url(self):
        hit = _make_hit(doc_name="fallback.pdf", file_url="")
        with patch.object(rag, "_find_file_url", return_value="") as mock_find, \
             patch.object(rag, "_to_docs_path", return_value="/docs/fallback.pdf"):
            rag._collect_sources([hit])
            mock_find.assert_called_once_with("fallback.pdf")

    def test_file_url_in_metadata_skips_fallback(self):
        hit = _make_hit(file_url="file:///data/doc.pdf")
        with patch.object(rag, "_find_file_url") as mock_find, \
             patch.object(rag, "_to_docs_path", return_value="/docs/doc.pdf"):
            rag._collect_sources([hit])
            mock_find.assert_not_called()

    def test_multiple_calls_increment_ids_correctly(self):
        h1 = _make_hit(doc_name="a.pdf", page_start=1)
        h2 = _make_hit(doc_name="b.pdf", page_start=1)
        h3 = _make_hit(doc_name="c.pdf", page_start=1)
        ids1 = rag._collect_sources([h1, h2])
        ids2 = rag._collect_sources([h3])
        assert ids1 == ["S1", "S2"]
        assert ids2 == ["S3"]

    def test_section_title_empty_string_when_none(self):
        hit = {
            "metadata": {
                "document_name": "doc.pdf",
                "page_start": 1,
                "section_title": None,
            },
            "text": "",
        }
        rag._collect_sources([hit])
        entry = rag.get_current_sources()[0]
        assert entry["section"] == ""

    @pytest.mark.parametrize("n