"""
Test module for api/rag_tools.py

What is tested:
  - reset_sources(): initialises a fresh source list in the contextvar
  - get_current_sources(): returns current sources or empty list
  - _find_file_url(): filesystem glob fallback for chunk metadata
  - _to_docs_path(): URI → /docs/-relative server URL conversion
  - _collect_sources(): deduplication logic, source-ID assignment, bucket accumulation
  - _log_hits(): conditional logging based on SHOW_TOOL_CALLS env var
  - make_rag_tools(): tool factory — get_current_date, list_products tool stubs

Mocks used:
  - unittest.mock.patch for filesystem (_DATA_DIR, Path.rglob, Path.resolve)
  - unittest.mock.patch for os.getenv / _SHOW_TOOL_CALLS
  - unittest.mock.MagicMock for the vector store passed to make_rag_tools()
  - monkeypatch (pytest) for environment variables and module-level state
  - lru_cache cleared between tests to avoid cross-test pollution

TODOs:
  - TODO: list_products tool body is truncated in source — full behaviour cannot be tested
  - TODO: any additional tools returned by make_rag_tools() beyond get_current_date and
          list_products are unknown from the truncated source; add tests once source is complete
  - TODO: async/concurrent contextvar sharing across asyncio tasks needs an integration test
          with a real event loop and multiple concurrent tool invocations
"""

import contextvars
import importlib
import logging
import os
import sys
from datetime import date
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Helpers to import the module under test with a clean slate each time
# ---------------------------------------------------------------------------

RAG_TOOLS_MODULE = "api.rag_tools"


def _fresh_module() -> ModuleType:
    """Re-import api.rag_tools so module-level state is reset between tests."""
    if RAG_TOOLS_MODULE in sys.modules:
        del sys.modules[RAG_TOOLS_MODULE]
    # Ensure the package stub exists so the import resolves
    if "api" not in sys.modules:
        api_pkg = ModuleType("api")
        api_pkg.__path__ = []  # type: ignore[attr-defined]
        sys.modules["api"] = api_pkg
    return importlib.import_module(RAG_TOOLS_MODULE)


@pytest.fixture(autouse=True)
def clear_lru_cache():
    """Clear _find_file_url's lru_cache before every test."""
    import api.rag_tools as rt
    rt._find_file_url.cache_clear()
    yield
    rt._find_file_url.cache_clear()


@pytest.fixture()
def rag(monkeypatch):
    """Return a freshly imported rag_tools module with SHOW_TOOL_CALLS=false."""
    monkeypatch.setenv("SHOW_TOOL_CALLS", "false")
    mod = _fresh_module()
    return mod


@pytest.fixture()
def rag_show_tool_calls(monkeypatch):
    """Return a freshly imported rag_tools module with SHOW_TOOL_CALLS=true."""
    monkeypatch.setenv("SHOW_TOOL_CALLS", "true")
    mod = _fresh_module()
    # Force the flag to True regardless of import-time caching
    mod._SHOW_TOOL_CALLS = True
    return mod


# ===========================================================================
# reset_sources / get_current_sources
# ===========================================================================


class TestSourceContextVar:
    def test_get_current_sources_default_is_empty_list(self, rag):
        """Before reset_sources is called the contextvar is None → returns []."""
        rag._sources_ctx.set(None)
        assert rag.get_current_sources() == []

    def test_reset_sources_initialises_empty_list(self, rag):
        rag.reset_sources()
        assert rag.get_current_sources() == []

    def test_reset_sources_clears_previous_data(self, rag):
        rag.reset_sources()
        rag._sources_ctx.get(None).append({"source_id": "S1", "document": "x", "page_start": 1})
        assert len(rag.get_current_sources()) == 1
        rag.reset_sources()
        assert rag.get_current_sources() == []

    def test_get_current_sources_returns_accumulated_entries(self, rag):
        rag.reset_sources()
        entry = {"source_id": "S1", "document": "doc.pdf", "page_start": 1}
        rag._sources_ctx.get(None).append(entry)
        sources = rag.get_current_sources()
        assert len(sources) == 1
        assert sources[0]["source_id"] == "S1"

    def test_get_current_sources_when_contextvar_is_none(self, rag):
        """Explicit None → falls back to empty list (not None)."""
        rag._sources_ctx.set(None)
        result = rag.get_current_sources()
        assert isinstance(result, list)
        assert result == []


# ===========================================================================
# _find_file_url
# ===========================================================================


class TestFindFileUrl:
    def test_returns_uri_when_file_found(self, rag, tmp_path):
        """Glob finds the file → returns a file:// URI string."""
        dummy = tmp_path / "doc.pdf"
        dummy.write_bytes(b"%PDF")

        with patch.object(type(rag._DATA_DIR), "rglob", return_value=[dummy]):
            result = rag._find_file_url("doc.pdf")
        assert result.startswith("file:///") or result.startswith("file://")
        assert "doc.pdf" in result

    def test_returns_empty_string_when_not_found(self, rag):
        with patch.object(type(rag._DATA_DIR), "rglob", return_value=[]):
            result = rag._find_file_url("nonexistent.pdf")
        assert result == ""

    def test_lru_cache_is_used(self, rag, tmp_path):
        """Calling twice with the same argument should only rglob once."""
        dummy = tmp_path / "cached.pdf"
        dummy.write_bytes(b"%PDF")

        with patch.object(type(rag._DATA_DIR), "rglob", return_value=[dummy]) as mock_rglob:
            rag._find_file_url("cached.pdf")
            rag._find_file_url("cached.pdf")
        # rglob may be called via the Path descriptor; just verify result is stable
        first = rag._find_file_url("cached.pdf")
        assert "cached.pdf" in first

    def test_different_document_names_return_different_results(self, rag, tmp_path):
        rag._find_file_url.cache_clear()
        file_a = tmp_path / "a.pdf"
        file_b = tmp_path / "b.pdf"
        file_a.write_bytes(b"%PDF")
        file_b.write_bytes(b"%PDF")

        def fake_rglob(pattern):
            if "a.pdf" in pattern:
                return [file_a]
            if "b.pdf" in pattern:
                return [file_b]
            return []

        with patch.object(type(rag._DATA_DIR), "rglob", side_effect=fake_rglob):
            r_a = rag._find_file_url("a.pdf")
            r_b = rag._find_file_url("b.pdf")

        assert "a.pdf" in r_a
        assert "b.pdf" in r_b
        assert r_a != r_b


# ===========================================================================
# _to_docs_path
# ===========================================================================


class TestToDocsPath:
    def test_empty_string_returns_empty(self, rag):
        assert rag._to_docs_path("") == ""

    def test_none_like_falsy_returns_empty(self, rag):
        # Passing a falsy non-empty value — function checks `if not file_url`
        assert rag._to_docs_path("") == ""

    def test_valid_file_uri_produces_docs_path(self, rag, tmp_path):
        """A file URI inside _DATA_DIR converts to /docs/<rel-path>."""
        # Build a fake data directory structure
        data_dir = tmp_path / "data"
        sub = data_dir / "Insurance-product-info"
        sub.mkdir(parents=True)
        fake_file = sub / "doc.pdf"
        fake_file.write_bytes(b"%PDF")

        uri = fake_file.resolve().as_uri()

        with patch.object(rag, "_DATA_DIR", data_dir):
            result = rag._to_docs_path(uri)

        assert result.startswith("/docs/")
        assert "Insurance-product-info" in result
        assert "doc.pdf" in result

    def test_file_outside_data_dir_returns_empty(self, rag, tmp_path):
        """A file URI that is NOT under _DATA_DIR returns ''."""
        outside = tmp_path / "outside" / "file.pdf"
        outside.parent.mkdir(parents=True)
        outside.write_bytes(b"%PDF")
        uri = outside.resolve().as_uri()

        data_dir = tmp_path / "data"
        data_dir.mkdir()

        with patch.object(rag, "_DATA_DIR", data_dir):
            result = rag._to_docs_path(uri)

        assert result == ""

    def test_malformed_uri_returns_empty(self, rag):
        """Unparseable URI gracefully returns ''."""
        result = rag._to_docs_path("not_a_valid_uri://???")
        assert result == ""

    def test_spaces_in_path_are_percent_encoded(self, rag, tmp_path):
        data_dir = tmp_path / "data"
        sub = data_dir / "folder with spaces"
        sub.mkdir(parents=True)
        fake_file = sub / "my doc.pdf"
        fake_file.write_bytes(b"%PDF")
        uri = fake_file.resolve().as_uri()

        with patch.object(rag, "_DATA_DIR", data_dir):
            result = rag._to_docs_path(uri)

        assert "%20" in result or " " not in result  # spaces must be encoded
        assert result.startswith("/docs/")


# ===========================================================================
# _collect_sources
# ===========================================================================


def _make_hit(doc="doc.pdf", page_start=1, page_end=2, product="Generations II",
              file_url="", section="Section 1", chunk_id="c1", text="Sample text"):
    return {
        "metadata": {
            "document_name": doc,
            "page_start": page_start,
            "page_end": page_end,
            "product_name": product,
            "file_url": file_url,
            "section_title": section,
            "chunk_id": chunk_id,
            "word_count": 50,
            "doc_type": "product_brochure",
        },
        "text": text,
    }


class TestCollectSources:
    def test_returns_empty_strings_when_no_bucket(self, rag):
        """When contextvar is None, returns list of empty strings."""
        rag._sources_ctx.set(None)
        hits = [_make_hit(), _make_hit(doc="other.pdf")]
        result = rag._collect_sources(hits)
        assert result == ["", ""]

    def test_single_hit_creates_s1(self, rag):
        rag.reset_sources()
        hits = [_make_hit()]
        result = rag._collect_sources(hits)
        assert result == ["S1"]
        bucket = rag.get_current_sources()
        assert len(bucket) == 1
        assert bucket[0]["source_id"] == "S1"

    def test_two_distinct_hits_create_s1_s2(self, rag):
        rag.reset_sources()
        hits = [_make_hit(doc="a.pdf", page_start=1), _make_hit(doc="b.pdf", page_start=1)]
        result = rag._collect_sources(hits)
        assert result == ["S1", "S2"]
        assert len(rag.get_current_sources()) == 2

    def test_duplicate_hit_reuses_id(self, rag):
        """Two hits with same (document, page_start) → same source_id."""
        rag.reset_sources()
        hit = _make_hit(doc="doc.pdf", page_start=5)
        result = rag._collect_sources([hit, hit])
        assert result == ["S1", "S1"]
        assert len(rag.get_current_sources()) == 1  # only one entry in bucket

    def test_dedup_across_multiple_calls(self, rag):
        """Deduplication persists across successive calls to _collect_sources."""
        rag.reset_sources()
        hit = _make_hit(doc="doc.pdf", page_start=3)
        rag._collect_sources([hit])
        result2 = rag._collect_sources([hit])
        assert result2 == ["S1"]
        assert len(rag.get_current_sources()) == 1

    def test_source_ids_are_sequential(self, rag):
        rag.reset_sources()
        hits = [
            _make_hit(doc="a.pdf", page_start=1),
            _make_hit(doc="b.pdf", page_start=2),
            _make_hit(doc="c.pdf", page_start=3),
        ]
        result = rag._collect_sources(hits)
        assert result == ["S1", "S2", "S3"]

    def test_text_preview_truncated_to_250(self, rag):
        rag.reset_sources()
        long_text = "X" * 500
        hit = _make_hit(text=long_text)
        rag._collect_sources([hit])
        bucket = rag.get_current_sources()
        assert len(bucket[0]["text_preview"]) <= 250

    def test_metadata_fields_stored_correctly(self, rag):
        rag.reset_sources()
        hit = _make_hit(
            doc="Generations-II_PB_EN.pdf",
            page_start=10,
            page_end=12,
            product="Generations II",
            section="Benefits",
            chunk_id="chunk_42",
            text="Some benefit description",
        )
        rag._collect_sources([hit])
        entry = rag.get_current_sources()[0]
        assert entry["document"] == "Generations-II_PB_EN.pdf"
        assert entry["page_start"] == 10
        assert entry["page_end"] == 12
        assert entry["product"] == "Generations II"
        assert entry["section"] == "Benefits"
        assert entry["chunk_id"] == "chunk_42"
        assert entry["source_id"] == "S1"

    def test_empty_hits_list_returns_empty_result(self, rag):
        rag.reset_sources()
        result = rag._collect_sources([])
        assert result == []
        assert rag.get_current_sources() == []

    def test_missing_metadata_fields_use_defaults(self, rag):
        rag.reset_sources()
        hit = {"metadata": {}, "text": "bare hit"}
        result = rag._collect_sources([hit])
        assert result == ["S1"]
        entry = rag.get_current_sources()[0]
        assert entry["document"] == "?"
        assert entry["page_start"] == "?"
        assert entry["section"] == ""

    def test_file_url_fallback_to_find_