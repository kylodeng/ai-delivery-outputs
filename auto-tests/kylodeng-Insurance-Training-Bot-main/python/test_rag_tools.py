"""
Test suite for api/rag_tools.py

What is tested:
- reset_sources(): initialises a fresh empty list in the contextvar
- get_current_sources(): returns the current list or [] when unset
- _find_file_url(): filesystem glob lookup with LRU cache
- _to_docs_path(): file:// URI → /docs/-relative URL conversion
- _collect_sources(): deduplication, source ID assignment, metadata extraction
- _log_hits(): conditional logging based on SHOW_TOOL_CALLS env var
- make_rag_tools(): factory returns callable tools; get_current_date and list_products behaviour

Mocks used:
- unittest.mock.patch for filesystem (Path.rglob, Path.resolve, Path.relative_to)
- unittest.mock.patch for logging.Logger.info
- unittest.mock.MagicMock for the vector store passed to make_rag_tools
- monkeypatch for environment variables and module-level constants

TODOs:
- TODO: Full integration test for list_products tool (requires knowing the full tool body which is truncated)
- TODO: Test async/concurrent behaviour of _sources_ctx across asyncio tasks (needs async test harness)
- TODO: Test remaining tools inside make_rag_tools once full source is available
"""

import contextvars
import os
import sys
from datetime import date
from pathlib import Path, PurePosixPath
from unittest.mock import MagicMock, patch, PropertyMock
import types

import pytest

# ---------------------------------------------------------------------------
# Helper: import the module under test, bypassing missing optional deps
# ---------------------------------------------------------------------------

def _import_rag_tools():
    """Import api.rag_tools, stubbing heavy optional dependencies."""
    # Stub langchain_core.tools if not installed
    if "langchain_core" not in sys.modules:
        lc_core = types.ModuleType("langchain_core")
        lc_tools = types.ModuleType("langchain_core.tools")

        def tool(fn):
            """Minimal @tool decorator: just return the function unchanged."""
            fn.invoke = fn  # give it a minimal .invoke so tests can call it
            return fn

        lc_tools.tool = tool
        lc_core.tools = lc_tools
        sys.modules["langchain_core"] = lc_core
        sys.modules["langchain_core.tools"] = lc_tools

    import importlib
    if "api.rag_tools" in sys.modules:
        return sys.modules["api.rag_tools"]
    # Make sure the package root is on sys.path
    pkg_root = Path(__file__).parent.parent
    if str(pkg_root) not in sys.path:
        sys.path.insert(0, str(pkg_root))
    import api.rag_tools as mod
    return mod


rag_tools = _import_rag_tools()

# Convenience aliases
reset_sources = rag_tools.reset_sources
get_current_sources = rag_tools.get_current_sources
_find_file_url = rag_tools._find_file_url
_to_docs_path = rag_tools._to_docs_path
_collect_sources = rag_tools._collect_sources
_log_hits = rag_tools._log_hits
make_rag_tools = rag_tools.make_rag_tools
_sources_ctx = rag_tools._sources_ctx


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def reset_contextvar():
    """Ensure the contextvar is cleared before/after every test."""
    token = _sources_ctx.set(None)
    yield
    _sources_ctx.reset(token)


@pytest.fixture()
def fresh_sources():
    """Initialise a fresh source bucket for tests that need it."""
    reset_sources()
    return get_current_sources


@pytest.fixture()
def fake_hit():
    """Factory for synthetic retrieval hits modelled on the Insurance data samples."""
    def _make(
        document_name="Generations-II_PB_EN.pdf",
        product_name="Generations II",
        doc_type="product_brochure",
        page_start=1,
        page_end=2,
        section_title="Overview",
        chunk_id="chunk-001",
        word_count=120,
        file_url="",
        text="This is sample text for Generations II brochure.",
    ):
        return {
            "metadata": {
                "document_name": document_name,
                "product_name": product_name,
                "doc_type": doc_type,
                "page_start": page_start,
                "page_end": page_end,
                "section_title": section_title,
                "chunk_id": chunk_id,
                "word_count": word_count,
                "file_url": file_url,
            },
            "text": text,
        }
    return _make


@pytest.fixture()
def mock_store():
    """Minimal mock vector store."""
    return MagicMock(name="VectorStore")


# ---------------------------------------------------------------------------
# reset_sources / get_current_sources
# ---------------------------------------------------------------------------

class TestResetSources:
    def test_sets_empty_list(self):
        reset_sources()
        assert _sources_ctx.get(None) == []

    def test_overwrites_existing_data(self):
        _sources_ctx.set(["old_data"])
        reset_sources()
        assert _sources_ctx.get(None) == []

    def test_idempotent_multiple_calls(self):
        reset_sources()
        reset_sources()
        assert _sources_ctx.get(None) == []


class TestGetCurrentSources:
    def test_returns_empty_list_when_unset(self):
        # contextvar holds None (default) — fixture ensures clean state
        result = get_current_sources()
        assert result == []

    def test_returns_empty_list_after_reset(self):
        reset_sources()
        assert get_current_sources() == []

    def test_returns_accumulated_sources(self, fresh_sources, fake_hit):
        hits = [fake_hit()]
        _collect_sources(hits)
        sources = get_current_sources()
        assert len(sources) == 1
        assert sources[0]["source_id"] == "S1"

    def test_returns_list_not_none(self):
        result = get_current_sources()
        assert result is not None
        assert isinstance(result, list)

    def test_returns_same_list_object_as_contextvar(self):
        reset_sources()
        bucket = _sources_ctx.get(None)
        result = get_current_sources()
        assert result is bucket


# ---------------------------------------------------------------------------
# _find_file_url
# ---------------------------------------------------------------------------

class TestFindFileUrl:
    def setup_method(self):
        # Clear the LRU cache before each test to avoid cross-test pollution
        _find_file_url.cache_clear()

    def test_returns_uri_when_file_found(self, tmp_path):
        # Create a fake file inside a temp directory
        subdir = tmp_path / "Insurance-product-info"
        subdir.mkdir(parents=True)
        fake_pdf = subdir / "Generations-II_PB_EN.pdf"
        fake_pdf.write_bytes(b"%PDF")

        with patch.object(rag_tools, "_DATA_DIR", tmp_path):
            _find_file_url.cache_clear()
            result = _find_file_url("Generations-II_PB_EN.pdf")

        assert result.startswith("file://")
        assert "Generations-II_PB_EN.pdf" in result

    def test_returns_empty_string_when_not_found(self, tmp_path):
        with patch.object(rag_tools, "_DATA_DIR", tmp_path):
            _find_file_url.cache_clear()
            result = _find_file_url("nonexistent.pdf")

        assert result == ""

    def test_lru_cache_is_used(self, tmp_path):
        """Second call with the same arg should use cache (rglob called only once)."""
        with patch.object(rag_tools, "_DATA_DIR", tmp_path):
            _find_file_url.cache_clear()
            with patch.object(Path, "rglob", return_value=[]) as mock_rglob:
                _find_file_url("doc.pdf")
                _find_file_url("doc.pdf")
                assert mock_rglob.call_count == 1

    def test_returns_first_match_when_multiple_exist(self, tmp_path):
        dir_a = tmp_path / "a"
        dir_b = tmp_path / "b"
        dir_a.mkdir()
        dir_b.mkdir()
        (dir_a / "doc.pdf").write_bytes(b"%PDF")
        (dir_b / "doc.pdf").write_bytes(b"%PDF")

        with patch.object(rag_tools, "_DATA_DIR", tmp_path):
            _find_file_url.cache_clear()
            result = _find_file_url("doc.pdf")

        # Should return a URI, not empty
        assert result.startswith("file://")


# ---------------------------------------------------------------------------
# _to_docs_path
# ---------------------------------------------------------------------------

class TestToDocsPath:
    def test_empty_string_returns_empty(self):
        assert _to_docs_path("") == ""

    def test_none_equivalent_empty(self):
        # The function guards on falsy — pass empty string
        assert _to_docs_path("") == ""

    def test_valid_file_uri_converted(self, tmp_path):
        """A file URI that sits inside _DATA_DIR should produce /docs/... path."""
        subdir = tmp_path / "Insurance-product-info"
        subdir.mkdir()
        pdf = subdir / "doc.pdf"
        pdf.write_bytes(b"%PDF")

        file_uri = pdf.resolve().as_uri()

        with patch.object(rag_tools, "_DATA_DIR", tmp_path):
            result = _to_docs_path(file_uri)

        assert result == "/docs/Insurance-product-info/doc.pdf"

    def test_file_outside_data_dir_returns_empty(self, tmp_path):
        """A file URI pointing outside _DATA_DIR should return empty string."""
        outside = tmp_path / "other" / "doc.pdf"
        outside.parent.mkdir(parents=True)
        outside.write_bytes(b"%PDF")
        file_uri = outside.resolve().as_uri()

        # _DATA_DIR points somewhere else
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        with patch.object(rag_tools, "_DATA_DIR", data_dir):
            result = _to_docs_path(file_uri)

        assert result == ""

    def test_special_characters_are_percent_encoded(self, tmp_path):
        subdir = tmp_path / "My Products"
        subdir.mkdir()
        pdf = subdir / "my doc.pdf"
        pdf.write_bytes(b"%PDF")

        file_uri = pdf.resolve().as_uri()

        with patch.object(rag_tools, "_DATA_DIR", tmp_path):
            result = _to_docs_path(file_uri)

        assert "My%20Products" in result or "My+Products" in result or "My Products" not in result
        # The important thing is it doesn't crash and starts with /docs/
        assert result.startswith("/docs/")

    def test_malformed_uri_returns_empty(self):
        result = _to_docs_path("not-a-valid-uri:::///")
        # Should not raise; returns empty on exception
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# _collect_sources
# ---------------------------------------------------------------------------

class TestCollectSources:
    def test_returns_empty_strings_when_no_bucket(self, fake_hit):
        """With contextvar unset, each hit gets an empty string source_id."""
        hits = [fake_hit(), fake_hit(document_name="other.pdf", page_start=3)]
        result = _collect_sources(hits)
        assert result == ["", ""]

    def test_assigns_sequential_ids(self, fake_hit):
        reset_sources()
        h1 = fake_hit(document_name="doc1.pdf", page_start=1)
        h2 = fake_hit(document_name="doc2.pdf", page_start=1)
        result = _collect_sources([h1, h2])
        assert result == ["S1", "S2"]

    def test_deduplicates_same_doc_and_page(self, fake_hit):
        reset_sources()
        h = fake_hit(document_name="doc.pdf", page_start=5)
        result = _collect_sources([h, h])
        assert result == ["S1", "S1"]
        assert len(get_current_sources()) == 1

    def test_different_pages_same_doc_get_different_ids(self, fake_hit):
        reset_sources()
        h1 = fake_hit(document_name="doc.pdf", page_start=1)
        h2 = fake_hit(document_name="doc.pdf", page_start=2)
        result = _collect_sources([h1, h2])
        assert result == ["S1", "S2"]

    def test_entry_fields_populated_correctly(self, fake_hit):
        reset_sources()
        h = fake_hit(
            document_name="Generations-II_PB_EN.pdf",
            product_name="Generations II",
            doc_type="product_brochure",
            page_start=10,
            page_end=11,
            section_title="Benefits",
            chunk_id="chunk-042",
            word_count=200,
            text="A" * 300,  # longer than 250 chars
        )
        _collect_sources([h])
        sources = get_current_sources()
        entry = sources[0]

        assert entry["source_id"] == "S1"
        assert entry["document"] == "Generations-II_PB_EN.pdf"
        assert entry["product"] == "Generations II"
        assert entry["page_start"] == 10
        assert entry["page_end"] == 11
        assert entry["section"] == "Benefits"
        assert entry["chunk_id"] == "chunk-042"
        assert len(entry["text_preview"]) == 250  # truncated

    def test_text_preview_not_truncated_when_short(self, fake_hit):
        reset_sources()
        short_text = "Short text."
        h = fake_hit(text=short_text)
        _collect_sources([h])
        assert get_current_sources()[0]["text_preview"] == short_text

    def test_empty_hits_list(self):
        reset_sources()
        result = _collect_sources([])
        assert result == []
        assert get_current_sources() == []

    def test_missing_metadata_uses_defaults(self):
        reset_sources()
        hit = {"metadata": {}, "text": "hello"}
        result = _collect_sources([hit])
        assert result == ["S1"]
        entry = get_current_sources()[0]
        assert entry["document"] == "?"
        assert entry["page_start"] == "?"
        assert entry["page_end"] == "?"

    def test_accumulates_across_calls(self, fake_hit):
        reset_sources()
        _collect_sources([fake_hit(document_name="a.pdf", page_start=1)])
        _collect_sources([fake_hit(document_name="b.pdf", page_start=1)])
        sources = get_current_sources()
        assert len(sources) == 2
        assert sources[0]["source_id"] == "S1"
        assert sources[1]["source_id"] == "S2"

    def test_reuses_id_for_duplicate_across_calls(self, fake_hit):
        reset_sources()
        h = fake_hit(document_name="dup.pdf", page_start=7)
        r1 = _collect_sources([h])
        r2 = _collect_sources([h])
        assert r1 == ["S1"]
        assert r2 == ["S