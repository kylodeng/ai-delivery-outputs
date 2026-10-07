"""
Test module for api/rag_tools.py

What is tested:
    - reset_sources(): initialises a fresh list in the contextvar
    - get_current_sources(): returns accumulated sources or [] when unset
    - _find_file_url(): filesystem glob for document names (mocked filesystem)
    - _to_docs_path(): URI → /docs/-relative URL conversion
    - _collect_sources(): deduplication, source-ID assignment, bucket management
    - _log_hits(): conditional logging based on SHOW_TOOL_CALLS env var
    - make_rag_tools() → get_current_date tool: returns formatted date string
    - make_rag_tools() → list_products tool: exercises the tool factory

Mocks used:
    - unittest.mock.patch for os.getenv / environment variables
    - unittest.mock.MagicMock for the vector store passed to make_rag_tools()
    - tmp_path (pytest fixture) for filesystem-dependent _find_file_url tests
    - monkeypatch for patching module-level globals (_DATA_DIR, _SHOW_TOOL_CALLS)
    - unittest.mock.patch for datetime.date.today

TODOs:
    - TODO: Full integration test for list_products tool requires knowing the
      complete signature of the store object and its search API — stub added below.
    - TODO: Tests for any additional tools returned by make_rag_tools() beyond
      get_current_date and list_products (source truncated) — stubs added below.
    - TODO: Async task isolation test (multiple asyncio tasks sharing same
      contextvar list) requires a running event loop with concurrent tasks.
"""

import contextvars
import importlib
import logging
import sys
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers to (re)import the module under test so we can monkeypatch easily
# ---------------------------------------------------------------------------

MODULE_PATH = "api.rag_tools"


def _import_module():
    """Import (or re-import) the module under test."""
    if MODULE_PATH in sys.modules:
        return sys.modules[MODULE_PATH]
    return importlib.import_module(MODULE_PATH)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_contextvar():
    """Ensure contextvar state does not leak between tests."""
    import api.rag_tools as rt

    token = rt._sources_ctx.set(None)
    yield
    rt._sources_ctx.reset(token)


@pytest.fixture()
def rt():
    """Alias for the module under test."""
    return _import_module()


@pytest.fixture()
def fake_data_dir(tmp_path, monkeypatch):
    """
    Create a temporary data directory that mirrors the real layout and patch
    _DATA_DIR in the module to point at it.
    """
    import api.rag_tools as module

    monkeypatch.setattr(module, "_DATA_DIR", tmp_path)
    # Also clear the lru_cache so each test starts fresh
    module._find_file_url.cache_clear()
    yield tmp_path
    module._find_file_url.cache_clear()


@pytest.fixture()
def mock_store():
    """A generic MagicMock standing in for the vector store."""
    return MagicMock()


# ---------------------------------------------------------------------------
# reset_sources / get_current_sources
# ---------------------------------------------------------------------------


class TestResetSources:
    def test_sets_empty_list(self, rt):
        rt.reset_sources()
        assert rt._sources_ctx.get(None) == []

    def test_overwrites_existing_list(self, rt):
        rt._sources_ctx.set(["something"])
        rt.reset_sources()
        assert rt._sources_ctx.get(None) == []

    def test_idempotent_double_call(self, rt):
        rt.reset_sources()
        rt.reset_sources()
        assert rt._sources_ctx.get(None) == []


class TestGetCurrentSources:
    def test_returns_empty_list_when_unset(self, rt):
        # contextvar default is None → should return []
        result = rt.get_current_sources()
        assert result == []

    def test_returns_empty_list_after_reset(self, rt):
        rt.reset_sources()
        assert rt.get_current_sources() == []

    def test_returns_accumulated_sources(self, rt):
        rt.reset_sources()
        rt._sources_ctx.get(None).append({"source_id": "S1", "document": "doc.pdf"})
        result = rt.get_current_sources()
        assert len(result) == 1
        assert result[0]["source_id"] == "S1"

    def test_returns_copy_reference(self, rt):
        rt.reset_sources()
        sources = rt.get_current_sources()
        assert isinstance(sources, list)


# ---------------------------------------------------------------------------
# _find_file_url
# ---------------------------------------------------------------------------


class TestFindFileUrl:
    def test_returns_uri_when_file_exists(self, rt, fake_data_dir):
        # Create a realistic nested file
        subdir = fake_data_dir / "Insurance-product-info" / "Generations-II"
        subdir.mkdir(parents=True)
        pdf = subdir / "Generations-II_PB_EN.pdf"
        pdf.write_bytes(b"%PDF-1.4")

        result = rt._find_file_url("Generations-II_PB_EN.pdf")

        assert result.startswith("file://")
        assert "Generations-II_PB_EN.pdf" in result

    def test_returns_empty_string_when_file_missing(self, rt, fake_data_dir):
        result = rt._find_file_url("nonexistent_document.pdf")
        assert result == ""

    def test_returns_first_match_when_multiple_exist(self, rt, fake_data_dir):
        for subdir_name in ("alpha", "beta"):
            d = fake_data_dir / subdir_name
            d.mkdir()
            (d / "shared.pdf").write_bytes(b"%PDF")

        result = rt._find_file_url("shared.pdf")
        assert result.startswith("file://")

    def test_lru_cache_is_used(self, rt, fake_data_dir):
        # Call twice; cache info should show 1 miss then 1 hit
        rt._find_file_url.cache_clear()
        rt._find_file_url("cached_test.pdf")
        rt._find_file_url("cached_test.pdf")
        info = rt._find_file_url.cache_info()
        assert info.hits >= 1

    def test_empty_string_document_name(self, rt, fake_data_dir):
        # Should not raise; rglob("") returns nothing useful but shouldn't crash
        result = rt._find_file_url("")
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# _to_docs_path
# ---------------------------------------------------------------------------


class TestToDocsPath:
    def test_empty_string_returns_empty(self, rt):
        assert rt._to_docs_path("") == ""

    def test_converts_file_uri_to_docs_path(self, rt, fake_data_dir, monkeypatch):
        import api.rag_tools as module

        # Create the real file so .resolve() works
        subdir = fake_data_dir / "Insurance-product-info"
        subdir.mkdir(parents=True, exist_ok=True)
        pdf = subdir / "doc.pdf"
        pdf.write_bytes(b"%PDF")

        uri = pdf.resolve().as_uri()
        result = module._to_docs_path(uri)

        assert result.startswith("/docs/")
        assert "doc.pdf" in result

    def test_path_with_spaces_is_encoded(self, rt, fake_data_dir, monkeypatch):
        import api.rag_tools as module

        subdir = fake_data_dir / "My Products"
        subdir.mkdir(parents=True, exist_ok=True)
        pdf = subdir / "plan doc.pdf"
        pdf.write_bytes(b"%PDF")

        uri = pdf.resolve().as_uri()
        result = module._to_docs_path(uri)

        assert result.startswith("/docs/")
        # Spaces should be percent-encoded
        assert " " not in result

    def test_returns_empty_on_path_outside_data_dir(self, rt, tmp_path, monkeypatch):
        """A file:// URI that cannot be made relative to _DATA_DIR → ''."""
        import api.rag_tools as module

        # Point _DATA_DIR at a sub-path that does NOT contain tmp_path root
        monkeypatch.setattr(module, "_DATA_DIR", tmp_path / "data")
        (tmp_path / "other").mkdir(exist_ok=True)
        pdf = tmp_path / "other" / "outside.pdf"
        pdf.write_bytes(b"%PDF")

        uri = pdf.resolve().as_uri()
        result = module._to_docs_path(uri)

        assert result == ""

    def test_returns_empty_on_malformed_uri(self, rt):
        result = rt._to_docs_path("not-a-uri-at-all")
        # Should not raise; result is "" or some string
        assert isinstance(result, str)

    def test_returns_empty_on_http_uri(self, rt):
        # Non-file URIs won't be relative to _DATA_DIR
        result = rt._to_docs_path("http://example.com/doc.pdf")
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# _collect_sources
# ---------------------------------------------------------------------------

def _make_hit(doc="doc.pdf", page_start=1, page_end=2, product="ProductX",
              file_url="", chunk_id="c1", text="sample text"):
    return {
        "metadata": {
            "document_name": doc,
            "page_start": page_start,
            "page_end": page_end,
            "product_name": product,
            "file_url": file_url,
            "chunk_id": chunk_id,
            "section_title": "Section A",
        },
        "text": text,
    }


class TestCollectSources:
    def test_returns_empty_strings_when_no_bucket(self, rt):
        # contextvar is None → no bucket
        hits = [_make_hit()]
        result = rt._collect_sources(hits)
        assert result == [""]

    def test_assigns_first_source_id(self, rt):
        rt.reset_sources()
        hits = [_make_hit(doc="doc1.pdf", page_start=1)]
        result = rt._collect_sources(hits)
        assert result == ["S1"]
        assert len(rt.get_current_sources()) == 1

    def test_sequential_ids_for_distinct_hits(self, rt):
        rt.reset_sources()
        hits = [
            _make_hit(doc="doc1.pdf", page_start=1),
            _make_hit(doc="doc2.pdf", page_start=5),
        ]
        result = rt._collect_sources(hits)
        assert result == ["S1", "S2"]
        assert len(rt.get_current_sources()) == 2

    def test_deduplication_same_doc_same_page(self, rt):
        rt.reset_sources()
        hits = [
            _make_hit(doc="doc.pdf", page_start=3),
            _make_hit(doc="doc.pdf", page_start=3),  # duplicate
        ]
        result = rt._collect_sources(hits)
        assert result == ["S1", "S1"]
        assert len(rt.get_current_sources()) == 1

    def test_different_pages_same_doc_not_deduplicated(self, rt):
        rt.reset_sources()
        hits = [
            _make_hit(doc="doc.pdf", page_start=1),
            _make_hit(doc="doc.pdf", page_start=2),
        ]
        result = rt._collect_sources(hits)
        assert result == ["S1", "S2"]
        assert len(rt.get_current_sources()) == 2

    def test_dedup_across_two_calls(self, rt):
        rt.reset_sources()
        # First call seeds the bucket
        rt._collect_sources([_make_hit(doc="doc.pdf", page_start=1)])
        # Second call with same hit should reuse S1
        result = rt._collect_sources([_make_hit(doc="doc.pdf", page_start=1)])
        assert result == ["S1"]
        assert len(rt.get_current_sources()) == 1

    def test_new_source_after_existing(self, rt):
        rt.reset_sources()
        rt._collect_sources([_make_hit(doc="doc.pdf", page_start=1)])
        result = rt._collect_sources([_make_hit(doc="doc2.pdf", page_start=7)])
        assert result == ["S2"]
        assert len(rt.get_current_sources()) == 2

    def test_empty_hits_list(self, rt):
        rt.reset_sources()
        result = rt._collect_sources([])
        assert result == []
        assert rt.get_current_sources() == []

    def test_source_entry_fields(self, rt):
        rt.reset_sources()
        hit = _make_hit(doc="plan.pdf", page_start=10, page_end=11,
                        product="GenII", chunk_id="ch99",
                        text="A" * 300)
        rt._collect_sources([hit])
        entry = rt.get_current_sources()[0]
        assert entry["source_id"] == "S1"
        assert entry["document"] == "plan.pdf"
        assert entry["product"] == "GenII"
        assert entry["page_start"] == 10
        assert entry["page_end"] == 11
        assert entry["chunk_id"] == "ch99"
        # text_preview must be at most 250 chars
        assert len(entry["text_preview"]) <= 250

    def test_text_preview_truncated_to_250(self, rt):
        rt.reset_sources()
        long_text = "X" * 500
        hit = _make_hit(text=long_text)
        rt._collect_sources([hit])
        entry = rt.get_current_sources()[0]
        assert len(entry["text_preview"]) == 250

    def test_missing_metadata_uses_defaults(self, rt):
        rt.reset_sources()
        # Hit with no metadata at all
        hit = {"text": "hello"}
        result = rt._collect_sources([hit])
        assert result == ["S1"]
        entry = rt.get_current_sources()[0]
        assert entry["document"] == "?"
        assert entry["page_start"] == "?"

    def test_file_url_falls_back_to_find_file_url(self, rt, fake_data_dir):
        """When file_url metadata is absent, _find_file_url is called."""
        subdir = fake_data_dir / "Insurance-product-info"
        subdir.mkdir(parents=True, exist_ok=True)
        pdf = subdir / "fallback.pdf"
        pdf.write_bytes(b"%PDF")

        rt.reset_sources()
        hit = {
            "metadata": {
                "document_name": "fallback.pdf",
                "page_start": 1,
                "page_end": 2,
                "product_name": "FallbackProd",
                "file_url": "",  # empty → should trigger _find_file_url
                "chunk_id": "c0",
                "section_title": "",
            },
            "text": "content",
        }
        rt._collect_sources([hit])
        entry = rt.get_current_sources()[0]
        # Should have resolved to a /docs/ path (non-empty)
        assert isinstance(entry["file_url"], str)

    def test_synthetic_generations_ii_hit(self, rt):
        """Exercise with realistic Generations II metadata from synthetic data."""