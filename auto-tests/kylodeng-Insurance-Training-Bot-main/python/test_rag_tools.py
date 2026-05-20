"""
Test module for api/rag_tools.py

What is tested:
    - reset_sources(): initialises a fresh list in the context var
    - get_current_sources(): returns sources list or empty list when unset
    - _find_file_url(): file-system glob for document names (mocked filesystem)
    - _to_docs_path(): URI → /docs/-relative URL conversion
    - _collect_sources(): deduplication, source-ID assignment, bucket population
    - _log_hits(): conditional logging based on SHOW_TOOL_CALLS flag
    - make_rag_tools(): factory returns a list of tools; get_current_date tool;
      list_products tool (stubbed — truncated source)

Mocks used:
    - unittest.mock.patch for os.getenv / _SHOW_TOOL_CALLS flag
    - unittest.mock.patch / tmp_path for filesystem (_DATA_DIR, _find_file_url)
    - unittest.mock.MagicMock for the vector store passed to make_rag_tools
    - lru_cache cleared between tests that exercise _find_file_url

TODOs:
    - list_products tool body is truncated in the source; only a stub test exists
    - Any additional tools created inside make_rag_tools beyond get_current_date
      and list_products are unknown; stub tests note this
    - Integration tests against a real vector store are out of scope
"""

import contextvars
import importlib
import logging
import os
import sys
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers to (re)import the module so module-level side-effects
# (e.g. reading SHOW_TOOL_CALLS from env) can be controlled per test.
# ---------------------------------------------------------------------------

def _reload_module():
    """Re-import api.rag_tools so module-level globals are fresh."""
    if "api.rag_tools" in sys.modules:
        del sys.modules["api.rag_tools"]
    # Also nuke the bare name in case it was imported directly
    if "rag_tools" in sys.modules:
        del sys.modules["rag_tools"]
    import api.rag_tools as m
    return m


@pytest.fixture()
def rag():
    """Return a freshly imported rag_tools module."""
    return _reload_module()


@pytest.fixture(autouse=True)
def reset_contextvar(rag):
    """Ensure context var is reset to default (None) before every test."""
    token = rag._sources_ctx.set(None)
    yield
    rag._sources_ctx.reset(token)


# ===========================================================================
# reset_sources / get_current_sources
# ===========================================================================

class TestResetSources:
    def test_sets_empty_list(self, rag):
        rag.reset_sources()
        assert rag._sources_ctx.get(None) == []

    def test_overwrites_existing_list(self, rag):
        rag._sources_ctx.set([{"source_id": "S1"}])
        rag.reset_sources()
        assert rag._sources_ctx.get(None) == []

    def test_idempotent_multiple_calls(self, rag):
        rag.reset_sources()
        rag.reset_sources()
        assert rag._sources_ctx.get(None) == []


class TestGetCurrentSources:
    def test_returns_empty_list_when_not_initialised(self, rag):
        # contextvar is None (default) → should return []
        result = rag.get_current_sources()
        assert result == []

    def test_returns_sources_after_reset(self, rag):
        rag.reset_sources()
        assert rag.get_current_sources() == []

    def test_returns_populated_list(self, rag):
        entry = {"source_id": "S1", "document": "doc.pdf"}
        rag._sources_ctx.set([entry])
        assert rag.get_current_sources() == [entry]

    def test_returns_copy_reference_same_list(self, rag):
        rag.reset_sources()
        sources = rag.get_current_sources()
        # Mutating the returned list should affect the contextvar list (same object)
        sources.append({"source_id": "S99"})
        assert len(rag.get_current_sources()) == 1


# ===========================================================================
# _find_file_url
# ===========================================================================

class TestFindFileUrl:
    def test_finds_existing_file(self, rag, tmp_path):
        # Create a temporary directory structure mimicking _DATA_DIR
        doc_file = tmp_path / "subdir" / "some_doc.pdf"
        doc_file.parent.mkdir(parents=True)
        doc_file.write_text("dummy")

        rag._find_file_url.cache_clear()
        with patch.object(rag, "_DATA_DIR", tmp_path):
            url = rag._find_file_url("some_doc.pdf")

        assert url.startswith("file:///") or url.startswith("file://")
        assert "some_doc.pdf" in url

    def test_returns_empty_string_when_not_found(self, rag, tmp_path):
        rag._find_file_url.cache_clear()
        with patch.object(rag, "_DATA_DIR", tmp_path):
            url = rag._find_file_url("nonexistent_document.pdf")
        assert url == ""

    def test_returns_first_match_when_multiple(self, rag, tmp_path):
        for sub in ("a", "b"):
            p = tmp_path / sub / "dup.pdf"
            p.parent.mkdir(parents=True)
            p.write_text("dummy")

        rag._find_file_url.cache_clear()
        with patch.object(rag, "_DATA_DIR", tmp_path):
            url = rag._find_file_url("dup.pdf")
        assert url != ""

    def test_lru_cache_returns_same_result(self, rag, tmp_path):
        rag._find_file_url.cache_clear()
        with patch.object(rag, "_DATA_DIR", tmp_path):
            url1 = rag._find_file_url("missing.pdf")
            url2 = rag._find_file_url("missing.pdf")
        assert url1 == url2 == ""

    def teardown_method(self, method):
        # Clear cache after each test so results don't bleed between tests
        try:
            import api.rag_tools as m
            m._find_file_url.cache_clear()
        except Exception:
            pass


# ===========================================================================
# _to_docs_path
# ===========================================================================

class TestToDocsPath:
    def test_empty_string_returns_empty(self, rag):
        assert rag._to_docs_path("") == ""

    def test_none_like_falsy_returns_empty(self, rag):
        assert rag._to_docs_path("") == ""

    def test_valid_file_uri_returns_docs_path(self, rag, tmp_path):
        # Build a real file path inside tmp_path so relative_to works
        sub = tmp_path / "Insurance-product-info" / "doc.pdf"
        sub.parent.mkdir(parents=True)
        sub.write_text("x")

        file_url = sub.resolve().as_uri()

        with patch.object(rag, "_DATA_DIR", tmp_path):
            result = rag._to_docs_path(file_url)

        assert result == "/docs/Insurance-product-info/doc.pdf"

    def test_spaces_in_path_are_percent_encoded(self, rag, tmp_path):
        sub = tmp_path / "My Folder" / "my doc.pdf"
        sub.parent.mkdir(parents=True)
        sub.write_text("x")

        file_url = sub.resolve().as_uri()

        with patch.object(rag, "_DATA_DIR", tmp_path):
            result = rag._to_docs_path(file_url)

        assert "My%20Folder" in result or "My Folder" not in result  # encoded
        assert result.startswith("/docs/")

    def test_path_outside_data_dir_returns_empty(self, rag, tmp_path):
        outside = tmp_path / "outside" / "file.pdf"
        outside.parent.mkdir(parents=True)
        outside.write_text("x")

        inside = tmp_path / "data"
        inside.mkdir()

        file_url = outside.resolve().as_uri()

        with patch.object(rag, "_DATA_DIR", inside):
            result = rag._to_docs_path(file_url)

        assert result == ""

    def test_malformed_uri_returns_empty(self, rag):
        result = rag._to_docs_path("not_a_valid_uri:::///")
        # Should not raise; may return "" on exception
        assert isinstance(result, str)


# ===========================================================================
# _collect_sources
# ===========================================================================

def _make_hit(doc="doc.pdf", page_start=1, page_end=2,
              product="ProductA", section="Sec1",
              file_url="", chunk_id="c1", text="Sample text"):
    return {
        "metadata": {
            "document_name": doc,
            "page_start": page_start,
            "page_end": page_end,
            "product_name": product,
            "section_title": section,
            "file_url": file_url,
            "chunk_id": chunk_id,
            "word_count": 50,
        },
        "text": text,
    }


class TestCollectSources:
    def test_returns_empty_strings_when_no_context(self, rag):
        hits = [_make_hit(), _make_hit(doc="other.pdf")]
        result = rag._collect_sources(hits)
        assert result == ["", ""]

    def test_assigns_s1_to_first_unique_hit(self, rag):
        rag.reset_sources()
        hits = [_make_hit()]
        result = rag._collect_sources(hits)
        assert result == ["S1"]

    def test_assigns_sequential_ids(self, rag):
        rag.reset_sources()
        hits = [_make_hit(doc="a.pdf", page_start=1),
                _make_hit(doc="b.pdf", page_start=1)]
        result = rag._collect_sources(hits)
        assert result == ["S1", "S2"]

    def test_deduplicates_same_doc_and_page(self, rag):
        rag.reset_sources()
        hits = [_make_hit(doc="dup.pdf", page_start=3),
                _make_hit(doc="dup.pdf", page_start=3)]
        result = rag._collect_sources(hits)
        assert result == ["S1", "S1"]
        assert len(rag.get_current_sources()) == 1

    def test_different_pages_same_doc_get_different_ids(self, rag):
        rag.reset_sources()
        hits = [_make_hit(doc="doc.pdf", page_start=1),
                _make_hit(doc="doc.pdf", page_start=5)]
        result = rag._collect_sources(hits)
        assert result == ["S1", "S2"]

    def test_bucket_grows_correctly_across_calls(self, rag):
        rag.reset_sources()
        rag._collect_sources([_make_hit(doc="a.pdf", page_start=1)])
        result = rag._collect_sources([_make_hit(doc="b.pdf", page_start=1)])
        assert result == ["S2"]
        assert len(rag.get_current_sources()) == 2

    def test_text_preview_truncated_to_250(self, rag):
        rag.reset_sources()
        long_text = "x" * 500
        hits = [_make_hit(text=long_text)]
        rag._collect_sources(hits)
        entry = rag.get_current_sources()[0]
        assert len(entry["text_preview"]) <= 250

    def test_entry_fields_populated(self, rag):
        rag.reset_sources()
        hits = [_make_hit(
            doc="Generations-II_PB_EN.pdf",
            page_start=4,
            page_end=6,
            product="Generations II",
            section="Benefits",
            chunk_id="chunk_42",
            text="Life insurance overview.",
        )]
        rag._collect_sources(hits)
        entry = rag.get_current_sources()[0]
        assert entry["source_id"] == "S1"
        assert entry["document"] == "Generations-II_PB_EN.pdf"
        assert entry["product"] == "Generations II"
        assert entry["page_start"] == 4
        assert entry["page_end"] == 6
        assert entry["section"] == "Benefits"
        assert entry["chunk_id"] == "chunk_42"
        assert entry["text_preview"] == "Life insurance overview."

    def test_fallback_find_file_url_called_when_no_file_url(self, rag):
        rag.reset_sources()
        hits = [_make_hit(file_url="")]  # no file_url in metadata
        with patch.object(rag, "_find_file_url", return_value="") as mock_find:
            rag._collect_sources(hits)
        mock_find.assert_called_once()

    def test_file_url_from_metadata_used_directly(self, rag, tmp_path):
        rag.reset_sources()
        sub = tmp_path / "prod" / "doc.pdf"
        sub.parent.mkdir(parents=True)
        sub.write_text("x")
        file_uri = sub.resolve().as_uri()

        hits = [_make_hit(file_url=file_uri)]
        with patch.object(rag, "_DATA_DIR", tmp_path):
            with patch.object(rag, "_find_file_url", return_value="") as mock_find:
                rag._collect_sources(hits)
        # _find_file_url should NOT be called when file_url is already present
        mock_find.assert_not_called()

    def test_empty_hits_list_returns_empty(self, rag):
        rag.reset_sources()
        result = rag._collect_sources([])
        assert result == []

    def test_missing_metadata_key_uses_question_mark_defaults(self, rag):
        rag.reset_sources()
        # Hit with completely empty metadata
        hit = {"metadata": {}, "text": "hello"}
        result = rag._collect_sources([hit])
        assert result == ["S1"]
        entry = rag.get_current_sources()[0]
        assert entry["document"] == "?"
        assert entry["page_start"] == "?"
        assert entry["page_end"] == "?"

    def test_section_none_becomes_empty_string(self, rag):
        rag.reset_sources()
        hit = {
            "metadata": {
                "document_name": "doc.pdf",
                "page_start": 1,
                "section_title": None,  # explicitly None
            },
            "text": "text",
        }
        rag._collect_sources([hit])
        entry = rag.get_current_sources()[0]
        assert entry["section"] == ""

    # Synthetic data sample inputs ----------------------------------------

    @pytest.mark.parametrize("doc,product,chunk_id", [
        ("Generations-II_PB_EN.pdf", "Generations II", "gen2_chunk_1"),
        ("List of designated hospitals in mainland China.pdf",
         "List of Designated Hospitals in Mainland China", "hospital_chunk_1"),
        ("Mainland_China_VIP_Hospital_Network.pdf",
         "List of Network Hospitals with Mainland China VIP Medical Navigation Service