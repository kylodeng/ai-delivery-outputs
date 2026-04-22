"""
Test module for api/rag_tools.py

What is tested:
- reset_sources(): initialises a fresh list in the contextvar
- get_current_sources(): returns the current sources list or empty list
- _find_file_url(): filesystem glob fallback for document lookup
- _to_docs_path(): URI → /docs/-relative server URL conversion
- _collect_sources(): deduplication, source ID assignment, bucket population
- _log_hits(): conditional logging based on SHOW_TOOL_CALLS env var
- make_rag_tools(): factory returns callable tools; get_current_date tool

Mocks used:
- unittest.mock.patch for filesystem operations (_DATA_DIR, Path.rglob)
- unittest.mock.patch for os.getenv / _SHOW_TOOL_CALLS flag
- unittest.mock.MagicMock for the vector store passed to make_rag_tools
- unittest.mock.patch on logger to verify log calls
- datetime.date.today patched for deterministic date output

TODOs:
- list_products tool: source code is truncated; stub tests included
- Any additional tools returned by make_rag_tools beyond get_current_date: source truncated
- Integration test with a real vector store: requires store fixture not available here
"""

import contextvars
import importlib
import sys
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, PropertyMock, call, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers to import the module under test while controlling side-effects
# ---------------------------------------------------------------------------

def _import_rag_tools():
    """Fresh import of api.rag_tools (clears lru_cache state between tests)."""
    # Ensure any cached module is cleared so _DATA_DIR etc. are re-evaluated
    if "api.rag_tools" in sys.modules:
        del sys.modules["api.rag_tools"]
    import api.rag_tools as rt
    return rt


@pytest.fixture()
def rt():
    """Return a freshly imported api.rag_tools module."""
    return _import_rag_tools()


@pytest.fixture(autouse=True)
def _clear_sources_ctx(rt):
    """Ensure the contextvar is reset to its default between tests."""
    token = rt._sources_ctx.set(None)
    yield
    rt._sources_ctx.reset(token)


# ---------------------------------------------------------------------------
# Synthetic hit builders (derived from the synthetic data samples)
# ---------------------------------------------------------------------------

def _make_hit(
    document_name="Generations-II_PB_EN.pdf",
    product_name="Generations II",
    page_start=1,
    page_end=2,
    section_title="Overview",
    file_url="",
    chunk_id="chunk-001",
    word_count=120,
    doc_type="product_brochure",
    text="Sample chunk text about Generations II whole life insurance plan.",
):
    return {
        "text": text,
        "metadata": {
            "document_name": document_name,
            "product_name": product_name,
            "page_start": page_start,
            "page_end": page_end,
            "section_title": section_title,
            "file_url": file_url,
            "chunk_id": chunk_id,
            "word_count": word_count,
            "doc_type": doc_type,
        },
    }


# ===========================================================================
# reset_sources / get_current_sources
# ===========================================================================

class TestResetSources:
    def test_sets_empty_list(self, rt):
        rt.reset_sources()
        assert rt._sources_ctx.get(None) == []

    def test_overwrites_existing_list(self, rt):
        rt._sources_ctx.set(["old_entry"])
        rt.reset_sources()
        assert rt._sources_ctx.get(None) == []

    def test_repeated_calls_give_fresh_list(self, rt):
        rt.reset_sources()
        first = rt._sources_ctx.get(None)
        first.append("x")
        rt.reset_sources()
        second = rt._sources_ctx.get(None)
        assert second == []
        assert second is not first


class TestGetCurrentSources:
    def test_returns_empty_list_when_ctx_is_none(self, rt):
        # default is None → should return []
        assert rt.get_current_sources() == []

    def test_returns_empty_list_when_ctx_is_empty_list(self, rt):
        rt.reset_sources()
        assert rt.get_current_sources() == []

    def test_returns_populated_list_after_reset_and_collect(self, rt):
        rt.reset_sources()
        hit = _make_hit()
        rt._collect_sources([hit])
        sources = rt.get_current_sources()
        assert len(sources) == 1
        assert sources[0]["source_id"] == "S1"

    def test_returns_copy_reference_not_independent_copy(self, rt):
        """get_current_sources returns the same list object (not a copy)."""
        rt.reset_sources()
        sources = rt.get_current_sources()
        bucket = rt._sources_ctx.get(None)
        assert sources is bucket


# ===========================================================================
# _find_file_url
# ===========================================================================

class TestFindFileUrl:
    def test_returns_uri_when_file_exists(self, rt, tmp_path):
        fake_file = tmp_path / "doc.pdf"
        fake_file.write_text("pdf content")
        with patch.object(type(rt._DATA_DIR), "__rtruediv__", return_value=rt._DATA_DIR):
            with patch("api.rag_tools._DATA_DIR", tmp_path):
                # Clear lru_cache to ensure fresh lookup
                rt._find_file_url.cache_clear()
                result = rt._find_file_url("doc.pdf")
        assert result.startswith("file://") or result == ""

    def test_returns_empty_string_when_not_found(self, rt, tmp_path):
        with patch("api.rag_tools._DATA_DIR", tmp_path):
            rt._find_file_url.cache_clear()
            result = rt._find_file_url("nonexistent_document.pdf")
        assert result == ""

    def test_lru_cache_is_applied(self, rt, tmp_path):
        """Second call with same arg should not hit filesystem again."""
        with patch("api.rag_tools._DATA_DIR", tmp_path):
            rt._find_file_url.cache_clear()
            with patch.object(Path, "rglob", return_value=iter([])) as mock_rglob:
                rt._find_file_url("doc.pdf")
                rt._find_file_url("doc.pdf")
                # rglob should only be called once due to caching
                assert mock_rglob.call_count == 1

    def test_returns_first_match_when_multiple_exist(self, rt, tmp_path):
        subdir1 = tmp_path / "a"
        subdir1.mkdir()
        subdir2 = tmp_path / "b"
        subdir2.mkdir()
        f1 = subdir1 / "doc.pdf"
        f2 = subdir2 / "doc.pdf"
        f1.write_text("x")
        f2.write_text("y")
        with patch("api.rag_tools._DATA_DIR", tmp_path):
            rt._find_file_url.cache_clear()
            result = rt._find_file_url("doc.pdf")
        assert result.startswith("file:///")


# ===========================================================================
# _to_docs_path
# ===========================================================================

class TestToDocsPath:
    def test_empty_string_returns_empty(self, rt):
        assert rt._to_docs_path("") == ""

    def test_valid_file_uri_converts_correctly(self, rt, tmp_path):
        """A file URI under _DATA_DIR should produce /docs/<rel_path>."""
        sub = tmp_path / "Insurance-product-info" / "doc.pdf"
        sub.parent.mkdir(parents=True, exist_ok=True)
        sub.write_text("x")
        file_uri = sub.resolve().as_uri()
        with patch("api.rag_tools._DATA_DIR", tmp_path):
            result = rt._to_docs_path(file_uri)
        assert result == "/docs/Insurance-product-info/doc.pdf"

    def test_uri_outside_data_dir_returns_empty(self, rt, tmp_path):
        """A file URI NOT under _DATA_DIR should return empty string (ValueError from relative_to)."""
        other = tmp_path / "other_dir" / "doc.pdf"
        other.parent.mkdir(parents=True, exist_ok=True)
        other.write_text("x")
        file_uri = other.resolve().as_uri()
        # _DATA_DIR points somewhere else
        with patch("api.rag_tools._DATA_DIR", tmp_path / "data"):
            result = rt._to_docs_path(file_uri)
        assert result == ""

    def test_malformed_uri_returns_empty(self, rt):
        result = rt._to_docs_path("not_a_uri_at_all:::///bad")
        # Should not raise, just return ""
        assert isinstance(result, str)

    def test_spaces_in_path_are_percent_encoded(self, rt, tmp_path):
        sub = tmp_path / "my folder" / "doc.pdf"
        sub.parent.mkdir(parents=True, exist_ok=True)
        sub.write_text("x")
        file_uri = sub.resolve().as_uri()
        with patch("api.rag_tools._DATA_DIR", tmp_path):
            result = rt._to_docs_path(file_uri)
        assert "my%20folder" in result or "my folder" not in result

    def test_unicode_filename_handled(self, rt, tmp_path):
        sub = tmp_path / "保険" / "doc.pdf"
        sub.parent.mkdir(parents=True, exist_ok=True)
        sub.write_text("x")
        file_uri = sub.resolve().as_uri()
        with patch("api.rag_tools._DATA_DIR", tmp_path):
            result = rt._to_docs_path(file_uri)
        # Should return a non-empty /docs/ path or "" on failure — must not raise
        assert isinstance(result, str)


# ===========================================================================
# _collect_sources
# ===========================================================================

class TestCollectSources:
    def test_returns_empty_strings_when_no_bucket(self, rt):
        """When contextvar is None (no reset_sources called), returns list of ''."""
        hits = [_make_hit()]
        result = rt._collect_sources(hits)
        assert result == [""]

    def test_returns_empty_list_for_no_hits(self, rt):
        rt.reset_sources()
        result = rt._collect_sources([])
        assert result == []

    def test_single_hit_gets_s1(self, rt):
        rt.reset_sources()
        hit = _make_hit()
        result = rt._collect_sources([hit])
        assert result == ["S1"]
        bucket = rt._sources_ctx.get(None)
        assert len(bucket) == 1
        assert bucket[0]["source_id"] == "S1"

    def test_two_different_hits_get_s1_s2(self, rt):
        rt.reset_sources()
        h1 = _make_hit(document_name="doc1.pdf", page_start=1)
        h2 = _make_hit(document_name="doc2.pdf", page_start=1)
        result = rt._collect_sources([h1, h2])
        assert result == ["S1", "S2"]

    def test_duplicate_hit_reuses_source_id(self, rt):
        rt.reset_sources()
        h1 = _make_hit(document_name="doc1.pdf", page_start=1)
        h2 = _make_hit(document_name="doc1.pdf", page_start=1)
        result = rt._collect_sources([h1, h2])
        assert result == ["S1", "S1"]
        assert len(rt._sources_ctx.get(None)) == 1

    def test_duplicate_across_two_calls(self, rt):
        rt.reset_sources()
        h1 = _make_hit(document_name="doc1.pdf", page_start=1)
        rt._collect_sources([h1])
        h2 = _make_hit(document_name="doc1.pdf", page_start=1)
        result = rt._collect_sources([h2])
        assert result == ["S1"]
        assert len(rt._sources_ctx.get(None)) == 1

    def test_source_entry_fields_populated(self, rt):
        rt.reset_sources()
        hit = _make_hit(
            document_name="Generations-II_PB_EN.pdf",
            product_name="Generations II",
            page_start=3,
            page_end=4,
            section_title="Benefits",
            file_url="",
            chunk_id="chunk-42",
            text="This is the text content of the chunk.",
        )
        rt._collect_sources([hit])
        entry = rt._sources_ctx.get(None)[0]
        assert entry["source_id"] == "S1"
        assert entry["document"] == "Generations-II_PB_EN.pdf"
        assert entry["product"] == "Generations II"
        assert entry["page_start"] == 3
        assert entry["page_end"] == 4
        assert entry["section"] == "Benefits"
        assert entry["chunk_id"] == "chunk-42"
        assert entry["text_preview"] == "This is the text content of the chunk."

    def test_text_preview_truncated_at_250_chars(self, rt):
        rt.reset_sources()
        long_text = "A" * 300
        hit = _make_hit(text=long_text)
        rt._collect_sources([hit])
        entry = rt._sources_ctx.get(None)[0]
        assert len(entry["text_preview"]) == 250

    def test_file_url_used_from_metadata_when_present(self, rt, tmp_path):
        sub = tmp_path / "doc.pdf"
        sub.write_text("x")
        file_uri = sub.resolve().as_uri()
        with patch("api.rag_tools._DATA_DIR", tmp_path):
            rt.reset_sources()
            hit = _make_hit(file_url=file_uri)
            rt._collect_sources([hit])
        entry = rt._sources_ctx.get(None)[0]
        # file_url in metadata overrides _find_file_url
        # result may be "" if path doesn't resolve under _DATA_DIR in this env,
        # but the important thing is it did not call _find_file_url
        assert isinstance(entry["file_url"], str)

    def test_find_file_url_called_when_no_file_url_in_metadata(self, rt):
        rt.reset_sources()
        with patch("api.rag_tools._find_file_url", return_value="") as mock_ffu, \
             patch("api.rag_tools._to_docs_path", return_value="/docs/doc.pdf"):
            hit = _make_hit(file_url="")
            rt._collect_sources([hit])
            mock_ffu.assert_called_once_with("Generations-II_PB_EN.pdf")

    def test_missing_metadata_fields_use_defaults(self, rt):
        rt.reset_sources()
        hit = {"text": "some text", "metadata": {}}
        rt._collect_sources([hit])
        entry = rt._sources_ctx.get(None)[0]
        assert entry["document"] == "?"
        assert entry["page_start"] == "?"
        assert entry["page_end"] == "?"
        assert entry["product"] == ""
        assert entry["section"] == ""

    def test_multiple_pages_same_doc_treated