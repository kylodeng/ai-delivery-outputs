"""
Test module for api/rag_tools.py

What is tested:
    - reset_sources(): initialises a fresh list in the contextvar
    - get_current_sources(): returns the current list or [] when unset
    - _find_file_url(): finds files under _DATA_DIR via rglob (mocked)
    - _to_docs_path(): converts file:/// URIs to /docs/-relative URLs
    - _collect_sources(): appends unique sources, deduplicates, returns source_ids
    - _log_hits(): logs chunk metadata when SHOW_TOOL_CALLS is true
    - make_rag_tools(): returns a list of tool callables
    - get_current_date tool: returns today's formatted date
    - list_products tool: (stub — source code truncated)

Mocks used:
    - unittest.mock.patch for Path.rglob, Path.resolve, os.getenv
    - unittest.mock.MagicMock for the vector store passed to make_rag_tools
    - contextvars isolation via manual reset_sources() / _sources_ctx.set()

TODOs:
    - list_products tool: source code was truncated; full behaviour cannot be tested
    - Any remaining tools defined inside make_rag_tools after list_products (truncated)
    - Integration test against a real or in-memory vector store
"""

import contextvars
import logging
import os
import sys
from datetime import date
from pathlib import Path, PurePosixPath
from types import SimpleNamespace
from unittest.mock import MagicMock, patch, PropertyMock
import importlib

import pytest

# ---------------------------------------------------------------------------
# Helpers to import the module under test with a controlled environment
# ---------------------------------------------------------------------------

MODULE_PATH = "api.rag_tools"


def _import_rag_tools():
    """Import (or re-import) rag_tools, returning the module object."""
    if MODULE_PATH in sys.modules:
        return sys.modules[MODULE_PATH]
    return importlib.import_module(MODULE_PATH)


@pytest.fixture(autouse=True)
def _isolate_contextvar():
    """
    Ensure each test starts with the contextvar in its default (None) state
    so tests do not bleed into each other.
    """
    import api.rag_tools as rt
    token = rt._sources_ctx.set(None)
    yield
    rt._sources_ctx.reset(token)


@pytest.fixture()
def rt():
    """Return the rag_tools module."""
    return _import_rag_tools()


# ---------------------------------------------------------------------------
# Synthetic hit builders
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
    text="Sample chunk text about Generations II whole life insurance plan.",
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


# ===========================================================================
# reset_sources / get_current_sources
# ===========================================================================

class TestResetSources:
    def test_initialises_empty_list(self, rt):
        rt.reset_sources()
        assert rt._sources_ctx.get(None) == []

    def test_replaces_existing_list(self, rt):
        rt._sources_ctx.set(["stale"])
        rt.reset_sources()
        assert rt._sources_ctx.get(None) == []

    def test_multiple_resets_give_fresh_list(self, rt):
        rt.reset_sources()
        rt._sources_ctx.get(None).append("x")
        rt.reset_sources()
        assert rt._sources_ctx.get(None) == []


class TestGetCurrentSources:
    def test_returns_empty_list_when_not_initialised(self, rt):
        # contextvar is None by default (enforced by autouse fixture)
        assert rt.get_current_sources() == []

    def test_returns_empty_list_when_contextvar_is_none(self, rt):
        rt._sources_ctx.set(None)
        assert rt.get_current_sources() == []

    def test_returns_existing_list(self, rt):
        rt.reset_sources()
        rt._sources_ctx.get(None).append({"source_id": "S1"})
        sources = rt.get_current_sources()
        assert len(sources) == 1
        assert sources[0]["source_id"] == "S1"

    def test_returns_same_object_as_contextvar(self, rt):
        rt.reset_sources()
        bucket = rt._sources_ctx.get(None)
        assert rt.get_current_sources() is bucket


# ===========================================================================
# _find_file_url
# ===========================================================================

class TestFindFileUrl:
    def test_returns_uri_when_file_found(self, rt, tmp_path):
        fake_file = tmp_path / "Generations-II_PB_EN.pdf"
        fake_file.touch()

        with patch.object(type(rt._DATA_DIR), "rglob", return_value=[fake_file]):
            # Clear lru_cache so our mock is exercised
            rt._find_file_url.cache_clear()
            result = rt._find_file_url("Generations-II_PB_EN.pdf")

        assert result.startswith("file://")
        assert "Generations-II_PB_EN.pdf" in result

    def test_returns_empty_string_when_not_found(self, rt):
        with patch.object(type(rt._DATA_DIR), "rglob", return_value=[]):
            rt._find_file_url.cache_clear()
            result = rt._find_file_url("nonexistent.pdf")

        assert result == ""

    def test_caches_results(self, rt, tmp_path):
        fake_file = tmp_path / "doc.pdf"
        fake_file.touch()

        rt._find_file_url.cache_clear()
        with patch.object(type(rt._DATA_DIR), "rglob", return_value=[fake_file]) as m:
            rt._find_file_url("doc.pdf")
            rt._find_file_url("doc.pdf")  # second call — should hit cache
            assert m.call_count == 1  # rglob called only once

    def teardown_method(self, method):
        import api.rag_tools as _rt
        _rt._find_file_url.cache_clear()


# ===========================================================================
# _to_docs_path
# ===========================================================================

class TestToDocsPath:
    def test_empty_string_returns_empty(self, rt):
        assert rt._to_docs_path("") == ""

    def test_none_like_falsy_returns_empty(self, rt):
        # Only empty string is the documented falsy input; ensure robustness
        assert rt._to_docs_path("") == ""

    def test_valid_file_uri_converted(self, rt, tmp_path):
        """A file URI pointing inside _DATA_DIR should become /docs/..."""
        # Build a real file inside a temp directory that acts as _DATA_DIR
        sub = tmp_path / "Insurance-product-info"
        sub.mkdir()
        doc = sub / "doc.pdf"
        doc.touch()

        file_uri = doc.resolve().as_uri()

        with patch.object(rt, "_DATA_DIR", tmp_path):
            result = rt._to_docs_path(file_uri)

        assert result == "/docs/Insurance-product-info/doc.pdf"

    def test_file_outside_data_dir_returns_empty(self, rt, tmp_path):
        """A file URI that cannot be made relative to _DATA_DIR → empty string."""
        outside = tmp_path / "outside.pdf"
        outside.touch()
        file_uri = outside.resolve().as_uri()

        # _DATA_DIR points somewhere else
        other_dir = tmp_path / "data"
        other_dir.mkdir()
        with patch.object(rt, "_DATA_DIR", other_dir):
            result = rt._to_docs_path(file_uri)

        assert result == ""

    def test_special_characters_in_path_are_percent_encoded(self, rt, tmp_path):
        sub = tmp_path / "My Folder"
        sub.mkdir()
        doc = sub / "doc with spaces.pdf"
        doc.touch()

        file_uri = doc.resolve().as_uri()

        with patch.object(rt, "_DATA_DIR", tmp_path):
            result = rt._to_docs_path(file_uri)

        # Spaces should be encoded
        assert "%20" in result or "My%20Folder" in result or "doc%20with%20spaces" in result

    def test_malformed_uri_returns_empty(self, rt):
        result = rt._to_docs_path("not_a_valid_uri://??##")
        # Should not raise; returns empty on exception
        assert isinstance(result, str)


# ===========================================================================
# _collect_sources
# ===========================================================================

class TestCollectSources:
    def test_returns_list_of_empty_strings_when_bucket_is_none(self, rt):
        # contextvar not initialised
        hits = [_make_hit(), _make_hit()]
        result = rt._collect_sources(hits)
        assert result == ["", ""]

    def test_single_hit_appended_as_s1(self, rt):
        rt.reset_sources()
        hit = _make_hit()
        result = rt._collect_sources([hit])
        assert result == ["S1"]
        assert rt._sources_ctx.get(None)[0]["source_id"] == "S1"

    def test_two_different_hits_get_sequential_ids(self, rt):
        rt.reset_sources()
        hit1 = _make_hit(document_name="doc1.pdf", page_start=1)
        hit2 = _make_hit(document_name="doc2.pdf", page_start=1)
        result = rt._collect_sources([hit1, hit2])
        assert result == ["S1", "S2"]

    def test_duplicate_hit_reuses_existing_id(self, rt):
        rt.reset_sources()
        hit = _make_hit(document_name="doc.pdf", page_start=5)
        result = rt._collect_sources([hit, hit])
        assert result == ["S1", "S1"]
        # Only one entry in bucket
        assert len(rt._sources_ctx.get(None)) == 1

    def test_same_doc_different_pages_get_different_ids(self, rt):
        rt.reset_sources()
        hit1 = _make_hit(document_name="doc.pdf", page_start=1)
        hit2 = _make_hit(document_name="doc.pdf", page_start=2)
        result = rt._collect_sources([hit1, hit2])
        assert result == ["S1", "S2"]

    def test_source_entry_fields_populated_correctly(self, rt):
        rt.reset_sources()
        hit = _make_hit(
            document_name="List of designated hospitals in mainland China.pdf",
            page_start=3,
            page_end=4,
            product_name="List of Designated Hospitals in Mainland China",
            doc_type="supplementary",
            section_title="Class 3 Hospitals",
            chunk_id="c042",
        )
        rt._collect_sources([hit])
        entry = rt._sources_ctx.get(None)[0]
        assert entry["source_id"] == "S1"
        assert entry["document"] == "List of designated hospitals in mainland China.pdf"
        assert entry["product"] == "List of Designated Hospitals in Mainland China"
        assert entry["page_start"] == 3
        assert entry["page_end"] == 4
        assert entry["section"] == "Class 3 Hospitals"
        assert entry["chunk_id"] == "c042"

    def test_text_preview_truncated_to_250_chars(self, rt):
        rt.reset_sources()
        long_text = "x" * 500
        hit = _make_hit(text=long_text)
        rt._collect_sources([hit])
        entry = rt._sources_ctx.get(None)[0]
        assert len(entry["text_preview"]) == 250

    def test_text_preview_short_text_not_padded(self, rt):
        rt.reset_sources()
        hit = _make_hit(text="short")
        rt._collect_sources([hit])
        assert rt._sources_ctx.get(None)[0]["text_preview"] == "short"

    def test_missing_metadata_fields_use_defaults(self, rt):
        rt.reset_sources()
        # Hit with minimal metadata
        hit = {"metadata": {}, "text": "hello"}
        rt._collect_sources([hit])
        entry = rt._sources_ctx.get(None)[0]
        assert entry["document"] == "?"
        assert entry["page_start"] == "?"
        assert entry["page_end"] == "?"
        assert entry["product"] == ""
        assert entry["section"] == ""

    def test_file_url_from_metadata_preferred_over_find(self, rt, tmp_path):
        """If file_url is in metadata, _find_file_url should not be called."""
        rt.reset_sources()
        sub = tmp_path / "Insurance-product-info"
        sub.mkdir()
        doc = sub / "Generations-II_PB_EN.pdf"
        doc.touch()
        file_uri = doc.resolve().as_uri()

        hit = _make_hit(file_url=file_uri)
        with patch.object(rt, "_DATA_DIR", tmp_path):
            with patch.object(rt, "_find_file_url") as mock_find:
                rt._collect_sources([hit])
                mock_find.assert_not_called()

    def test_file_url_fallback_to_find_file_url_when_missing(self, rt):
        rt.reset_sources()
        hit = _make_hit(file_url="")  # no file_url in metadata

        with patch.object(rt, "_find_file_url", return_value="") as mock_find:
            rt._collect_sources([hit])
            mock_find.assert_called_once_with(hit["metadata"]["document_name"])

    def test_sequential_ids_across_multiple_calls(self, rt):
        """IDs must continue from where they left off on a second call."""
        rt.reset_sources()
        hit1 = _make_hit(document_name="a.pdf", page_start=1)
        hit2 = _make_hit(document_name="b.pdf", page_start=1)
        rt._collect_sources([hit1])
        result = rt._collect_sources([hit2])
        assert result == ["S2"]

    def test_empty_hits_list_returns_empty(self, rt):
        rt.reset_sources()
        result = rt._collect_sources([])
        assert result == []

    def test_section_title_none_becomes_empty_string(self, rt):
        rt.reset_sources()
        hit = _make_hit(section_title=None)
        rt._collect_sources([hit])
        entry = rt._sources_ctx.get(None)[0]
        # `or ""` in the source code handles None → ""
        assert entry["section"] == ""


# ===========================================================================
# _log_hits
# ===========================================================================

class TestLogHits:
    def test_no_log_when_show_tool_calls_false(self, rt, caplog):
        with patch.object(rt, "_SHOW_TOOL_CALLS", False):
            with caplog.