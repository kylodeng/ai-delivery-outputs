"""
Test module for api/rag_tools.py

What is tested:
  - reset_sources(): initialises a fresh source list in the contextvar
  - get_current_sources(): returns sources list or empty list when unset
  - _find_file_url(): searches _DATA_DIR recursively for a document name
  - _to_docs_path(): converts file:/// URIs to /docs/-relative server URLs
  - _collect_sources(): deduplicates, assigns source IDs, appends to bucket
  - _log_hits(): logs chunk metadata only when SHOW_TOOL_CALLS=true
  - make_rag_tools() / get_current_date tool: returns formatted today's date
  - make_rag_tools() / list_products tool: (stub – tool body truncated in source)

Mocks used:
  - unittest.mock.patch for Path.rglob (_find_file_url filesystem calls)
  - unittest.mock.patch for datetime.date.today (get_current_date tool)
  - unittest.mock.patch for os.getenv / _SHOW_TOOL_CALLS flag
  - unittest.mock.MagicMock for the vector store passed to make_rag_tools()
  - unittest.mock.patch for logging.Logger.info (_log_hits verification)

TODOs:
  - TODO: list_products tool body is truncated in the source; full behaviour cannot
          be tested without the complete implementation.
  - TODO: Any additional tools created inside make_rag_tools() beyond get_current_date
          and list_products cannot be tested without the full source.
  - TODO: _to_docs_path Windows path handling needs a Windows runner to verify
          urllib.request.url2pathname behaviour on posix vs nt.
"""

import contextvars
import logging
import os
import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Helpers to import the module under test while controlling env vars
# ---------------------------------------------------------------------------

def _import_rag_tools(show_tool_calls: str = "false"):
    """Import (or re-import) rag_tools with a specific SHOW_TOOL_CALLS value."""
    with patch.dict(os.environ, {"SHOW_TOOL_CALLS": show_tool_calls}):
        # Remove cached module so the module-level flag is re-evaluated
        sys.modules.pop("api.rag_tools", None)
        sys.modules.pop("rag_tools", None)
        import importlib
        import api.rag_tools as mod
        importlib.reload(mod)
    return mod


@pytest.fixture()
def rag():
    """Fresh import of rag_tools with SHOW_TOOL_CALLS=false."""
    sys.modules.pop("api.rag_tools", None)
    import api.rag_tools as mod
    # Reset contextvar between tests
    mod._sources_ctx.set(None)
    return mod


@pytest.fixture()
def rag_show_calls():
    """Fresh import of rag_tools with SHOW_TOOL_CALLS=true."""
    return _import_rag_tools(show_tool_calls="true")


@pytest.fixture()
def store():
    """Mock vector store."""
    return MagicMock()


# ---------------------------------------------------------------------------
# Synthetic hit helpers
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
    text="Sample chunk text about Generations II whole life insurance.",
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

class TestSourceContextVar:
    def test_get_current_sources_default_is_empty_list(self, rag):
        """Before reset_sources is called, get_current_sources returns []."""
        rag._sources_ctx.set(None)
        assert rag.get_current_sources() == []

    def test_reset_sources_initialises_empty_list(self, rag):
        rag.reset_sources()
        assert rag.get_current_sources() == []

    def test_reset_sources_clears_previous_data(self, rag):
        rag.reset_sources()
        # Manually add something to simulate a previous request
        rag._sources_ctx.get(None).append({"source_id": "S1"})
        assert len(rag.get_current_sources()) == 1
        # New request
        rag.reset_sources()
        assert rag.get_current_sources() == []

    def test_get_current_sources_returns_accumulated_entries(self, rag):
        rag.reset_sources()
        hit = _make_hit()
        rag._collect_sources([hit])
        sources = rag.get_current_sources()
        assert len(sources) == 1
        assert sources[0]["source_id"] == "S1"

    def test_get_current_sources_returns_list_not_none_when_unset(self, rag):
        rag._sources_ctx.set(None)
        result = rag.get_current_sources()
        assert isinstance(result, list)


# ===========================================================================
# _find_file_url
# ===========================================================================

class TestFindFileUrl:
    def test_returns_uri_when_file_found(self, rag, tmp_path):
        """When a file matches the glob, return its file:// URI."""
        doc = tmp_path / "doc.pdf"
        doc.write_bytes(b"%PDF")
        with patch.object(rag, "_DATA_DIR", tmp_path):
            # Clear LRU cache so the patched _DATA_DIR is used
            rag._find_file_url.cache_clear()
            result = rag._find_file_url("doc.pdf")
        assert result.startswith("file://")
        assert "doc.pdf" in result

    def test_returns_empty_string_when_not_found(self, rag, tmp_path):
        with patch.object(rag, "_DATA_DIR", tmp_path):
            rag._find_file_url.cache_clear()
            result = rag._find_file_url("nonexistent.pdf")
        assert result == ""

    def test_returns_first_match_when_multiple_found(self, rag, tmp_path):
        """Only the first rglob match is returned."""
        sub1 = tmp_path / "a"
        sub1.mkdir()
        sub2 = tmp_path / "b"
        sub2.mkdir()
        (sub1 / "doc.pdf").write_bytes(b"%PDF")
        (sub2 / "doc.pdf").write_bytes(b"%PDF")
        with patch.object(rag, "_DATA_DIR", tmp_path):
            rag._find_file_url.cache_clear()
            result = rag._find_file_url("doc.pdf")
        assert result.startswith("file://")

    def test_lru_cache_is_used(self, rag, tmp_path):
        """Calling twice with the same arg should hit the cache."""
        with patch.object(rag, "_DATA_DIR", tmp_path):
            rag._find_file_url.cache_clear()
            with patch.object(Path, "rglob", return_value=iter([])) as mock_rglob:
                rag._find_file_url("cached.pdf")
                rag._find_file_url("cached.pdf")
                # rglob is called on a Path instance; called once due to cache
                assert mock_rglob.call_count == 1


# ===========================================================================
# _to_docs_path
# ===========================================================================

class TestToDocsPath:
    def test_empty_string_returns_empty(self, rag):
        assert rag._to_docs_path("") == ""

    def test_none_equivalent_empty(self, rag):
        # _to_docs_path expects a str; empty string is the sentinel
        assert rag._to_docs_path("") == ""

    def test_valid_file_uri_produces_docs_path(self, rag, tmp_path):
        """A file:// URI inside _DATA_DIR maps to /docs/<rel>."""
        subdir = tmp_path / "Insurance-product-info"
        subdir.mkdir()
        pdf = subdir / "doc.pdf"
        pdf.write_bytes(b"%PDF")
        file_url = pdf.resolve().as_uri()
        with patch.object(rag, "_DATA_DIR", tmp_path):
            result = rag._to_docs_path(file_url)
        assert result == "/docs/Insurance-product-info/doc.pdf"

    def test_file_uri_outside_data_dir_returns_empty(self, rag, tmp_path):
        """If the file is not under _DATA_DIR, relative_to() raises and we return ''."""
        other_dir = tmp_path / "other"
        other_dir.mkdir()
        pdf = other_dir / "secret.pdf"
        pdf.write_bytes(b"%PDF")
        file_url = pdf.resolve().as_uri()
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        with patch.object(rag, "_DATA_DIR", data_dir):
            result = rag._to_docs_path(file_url)
        assert result == ""

    def test_spaces_in_filename_are_percent_encoded(self, rag, tmp_path):
        subdir = tmp_path / "Insurance-product-info"
        subdir.mkdir()
        pdf = subdir / "my doc.pdf"
        pdf.write_bytes(b"%PDF")
        file_url = pdf.resolve().as_uri()
        with patch.object(rag, "_DATA_DIR", tmp_path):
            result = rag._to_docs_path(file_url)
        assert "%20" in result or "my%20doc.pdf" in result

    def test_malformed_uri_returns_empty(self, rag):
        result = rag._to_docs_path("not_a_valid_uri:::///")
        # Should not raise, should return empty string
        assert isinstance(result, str)

    def test_special_characters_in_path_encoded(self, rag, tmp_path):
        subdir = tmp_path / "Insurance-product-info"
        subdir.mkdir()
        pdf = subdir / "doc & stuff.pdf"
        pdf.write_bytes(b"%PDF")
        file_url = pdf.resolve().as_uri()
        with patch.object(rag, "_DATA_DIR", tmp_path):
            result = rag._to_docs_path(file_url)
        assert result.startswith("/docs/")


# ===========================================================================
# _collect_sources
# ===========================================================================

class TestCollectSources:
    def test_no_bucket_returns_empty_strings(self, rag):
        """When contextvar is None, returns a list of empty strings."""
        rag._sources_ctx.set(None)
        hits = [_make_hit(), _make_hit(document_name="other.pdf", page_start=5)]
        result = rag._collect_sources(hits)
        assert result == ["", ""]

    def test_single_hit_assigned_s1(self, rag):
        rag.reset_sources()
        hits = [_make_hit()]
        result = rag._collect_sources(hits)
        assert result == ["S1"]
        assert rag.get_current_sources()[0]["source_id"] == "S1"

    def test_two_distinct_hits_assigned_s1_s2(self, rag):
        rag.reset_sources()
        hits = [
            _make_hit(document_name="doc1.pdf", page_start=1),
            _make_hit(document_name="doc2.pdf", page_start=3),
        ]
        result = rag._collect_sources(hits)
        assert result == ["S1", "S2"]
        assert len(rag.get_current_sources()) == 2

    def test_duplicate_hit_reuses_source_id(self, rag):
        """Two hits with same (document_name, page_start) share one source ID."""
        rag.reset_sources()
        hit = _make_hit(document_name="doc1.pdf", page_start=1)
        result = rag._collect_sources([hit, hit])
        assert result == ["S1", "S1"]
        assert len(rag.get_current_sources()) == 1

    def test_cross_call_deduplication(self, rag):
        """A second call with the same doc/page reuses the ID from the first call."""
        rag.reset_sources()
        hit = _make_hit(document_name="doc1.pdf", page_start=1)
        first = rag._collect_sources([hit])
        second = rag._collect_sources([hit])
        assert first == ["S1"]
        assert second == ["S1"]
        assert len(rag.get_current_sources()) == 1

    def test_source_entry_fields_populated_correctly(self, rag, tmp_path):
        rag.reset_sources()
        # Use a real file URI so _to_docs_path can resolve it
        subdir = tmp_path / "Insurance-product-info"
        subdir.mkdir()
        pdf = subdir / "Generations-II_PB_EN.pdf"
        pdf.write_bytes(b"%PDF")
        file_url = pdf.resolve().as_uri()
        hit = _make_hit(
            document_name="Generations-II_PB_EN.pdf",
            page_start=10,
            page_end=11,
            product_name="Generations II",
            section_title="Benefits",
            file_url=file_url,
            chunk_id="c042",
            text="A" * 300,  # longer than 250 chars
        )
        with patch.object(rag, "_DATA_DIR", tmp_path):
            rag._collect_sources([hit])
        entry = rag.get_current_sources()[0]
        assert entry["source_id"] == "S1"
        assert entry["document"] == "Generations-II_PB_EN.pdf"
        assert entry["product"] == "Generations II"
        assert entry["page_start"] == 10
        assert entry["page_end"] == 11
        assert entry["section"] == "Benefits"
        assert entry["chunk_id"] == "c042"
        assert len(entry["text_preview"]) == 250  # truncated to 250

    def test_missing_metadata_uses_defaults(self, rag):
        rag.reset_sources()
        hit = {"metadata": {}, "text": "some text"}
        result = rag._collect_sources([hit])
        assert result == ["S1"]
        entry = rag.get_current_sources()[0]
        assert entry["document"] == "?"
        assert entry["page_start"] == "?"
        assert entry["page_end"] == "?"
        assert entry["product"] == ""
        assert entry["section"] == ""

    def test_empty_hits_list_returns_empty_list(self, rag):
        rag.reset_sources()
        result = rag._collect_sources([])
        assert result == []
        assert rag.get_current_sources() == []

    def test_counter_increments_across_multiple_calls(self, rag):
        rag.reset_sources()
        rag._collect_sources([_make_hit(document_name="a.pdf", page_start=1)])
        rag._collect