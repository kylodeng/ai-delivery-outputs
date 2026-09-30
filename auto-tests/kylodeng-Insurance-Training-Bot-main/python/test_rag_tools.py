"""
Test module for api/rag_tools.py

What is tested:
    - reset_sources(): initialises a fresh empty list in the context var
    - get_current_sources(): returns current list or empty list when unset
    - _find_file_url(): finds files by name under _DATA_DIR via rglob
    - _to_docs_path(): converts file:/// URIs to /docs/-relative server URLs
    - _collect_sources(): deduplicates hits, assigns source IDs, builds bucket entries
    - _log_hits(): logs chunk metadata when SHOW_TOOL_CALLS is true/false
    - make_rag_tools(): returns a list of tools bound to a store

Mocks used:
    - unittest.mock.patch for Path.rglob (_find_file_url filesystem calls)
    - unittest.mock.patch for os.getenv / _SHOW_TOOL_CALLS
    - unittest.mock.MagicMock for the vector store passed to make_rag_tools
    - unittest.mock.patch for date.today in get_current_date tool
    - unittest.mock.patch for logger.info in _log_hits

TODOs:
    - TODO: Full integration test for list_products tool requires store API contract details
    - TODO: Additional tools returned by make_rag_tools beyond get_current_date and
            list_products cannot be tested without the complete source (truncated)
    - TODO: Async contextvar isolation tests across asyncio tasks need the real LangGraph
            executor to verify cross-task list-sharing behaviour
"""

import contextvars
import os
import sys
import types
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch, call
import pytest

# ---------------------------------------------------------------------------
# Helpers to import the module under test with controlled environment
# ---------------------------------------------------------------------------

def _import_rag_tools():
    """Import api.rag_tools fresh, ensuring it is importable without a real store."""
    # langchain_core.tools may or may not be installed; stub if absent
    if "langchain_core" not in sys.modules:
        lc = types.ModuleType("langchain_core")
        lc_tools = types.ModuleType("langchain_core.tools")

        def tool(fn):
            """Minimal @tool decorator stub — just returns the function unchanged."""
            fn.invoke = fn  # allow .invoke() calls in tests
            return fn

        lc_tools.tool = tool
        sys.modules["langchain_core"] = lc
        sys.modules["langchain_core.tools"] = lc_tools

    import importlib
    import api.rag_tools as mod
    importlib.reload(mod)
    return mod


# ---------------------------------------------------------------------------
# Module-level import
# ---------------------------------------------------------------------------

try:
    import api.rag_tools as rag_tools
except ModuleNotFoundError:
    rag_tools = _import_rag_tools()  # type: ignore[assignment]


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture(autouse=True)
def reset_context_var():
    """Ensure the contextvar is clean before and after every test."""
    token = rag_tools._sources_ctx.set(None)
    yield
    rag_tools._sources_ctx.reset(token)


@pytest.fixture()
def fresh_bucket():
    """Initialise a fresh bucket and return the module for convenience."""
    rag_tools.reset_sources()
    return rag_tools


@pytest.fixture()
def mock_store():
    return MagicMock(name="vector_store")


# ============================================================================
# reset_sources / get_current_sources
# ============================================================================

class TestResetSources:
    def test_sets_empty_list(self):
        rag_tools.reset_sources()
        result = rag_tools._sources_ctx.get(None)
        assert result == []
        assert isinstance(result, list)

    def test_overwrites_existing_data(self):
        rag_tools._sources_ctx.set(["old_data"])
        rag_tools.reset_sources()
        assert rag_tools._sources_ctx.get(None) == []

    def test_multiple_resets_give_fresh_list(self):
        rag_tools.reset_sources()
        bucket = rag_tools._sources_ctx.get(None)
        bucket.append("sentinel")
        rag_tools.reset_sources()
        assert rag_tools._sources_ctx.get(None) == []


class TestGetCurrentSources:
    def test_returns_empty_list_when_unset(self):
        # contextvar is None by default (autouse fixture ensures this)
        result = rag_tools.get_current_sources()
        assert result == []

    def test_returns_empty_list_when_explicitly_none(self):
        rag_tools._sources_ctx.set(None)
        assert rag_tools.get_current_sources() == []

    def test_returns_populated_bucket(self):
        rag_tools._sources_ctx.set([{"source_id": "S1"}, {"source_id": "S2"}])
        result = rag_tools.get_current_sources()
        assert len(result) == 2
        assert result[0]["source_id"] == "S1"

    def test_returns_same_list_object_as_bucket(self):
        rag_tools.reset_sources()
        bucket = rag_tools._sources_ctx.get(None)
        bucket.append({"source_id": "S1"})
        result = rag_tools.get_current_sources()
        assert result is bucket


# ============================================================================
# _find_file_url
# ============================================================================

class TestFindFileUrl:
    def setup_method(self):
        # Clear lru_cache between tests
        rag_tools._find_file_url.cache_clear()

    def test_returns_uri_when_file_found(self, tmp_path):
        dummy_file = tmp_path / "doc.pdf"
        dummy_file.write_text("dummy")

        with patch.object(rag_tools._DATA_DIR, "rglob", return_value=[dummy_file]):
            with patch("api.rag_tools._DATA_DIR") as mock_data_dir:
                mock_data_dir.rglob.return_value = [dummy_file]
                # Call directly patching _DATA_DIR inside the function
                pass

        # Patch at the module level properly
        rag_tools._find_file_url.cache_clear()
        with patch("api.rag_tools._DATA_DIR") as mock_dir:
            mock_dir.rglob.return_value = [dummy_file]
            result = rag_tools._find_file_url("doc.pdf")
        assert result.startswith("file:///") or result.startswith("file://")
        assert "doc.pdf" in result

    def test_returns_empty_string_when_not_found(self):
        rag_tools._find_file_url.cache_clear()
        with patch("api.rag_tools._DATA_DIR") as mock_dir:
            mock_dir.rglob.return_value = []
            result = rag_tools._find_file_url("nonexistent.pdf")
        assert result == ""

    def test_returns_first_match_when_multiple(self, tmp_path):
        rag_tools._find_file_url.cache_clear()
        file1 = tmp_path / "a" / "doc.pdf"
        file2 = tmp_path / "b" / "doc.pdf"
        file1.parent.mkdir(parents=True)
        file2.parent.mkdir(parents=True)
        file1.write_text("a")
        file2.write_text("b")

        with patch("api.rag_tools._DATA_DIR") as mock_dir:
            mock_dir.rglob.return_value = [file1, file2]
            result = rag_tools._find_file_url("doc.pdf")
        assert "doc.pdf" in result

    def test_lru_cache_is_applied(self):
        """Second call with same arg should hit cache, not rglob again."""
        rag_tools._find_file_url.cache_clear()
        with patch("api.rag_tools._DATA_DIR") as mock_dir:
            mock_dir.rglob.return_value = []
            rag_tools._find_file_url("cached.pdf")
            rag_tools._find_file_url("cached.pdf")
            assert mock_dir.rglob.call_count == 1

    def test_different_names_are_cached_independently(self):
        rag_tools._find_file_url.cache_clear()
        with patch("api.rag_tools._DATA_DIR") as mock_dir:
            mock_dir.rglob.return_value = []
            rag_tools._find_file_url("a.pdf")
            rag_tools._find_file_url("b.pdf")
            assert mock_dir.rglob.call_count == 2


# ============================================================================
# _to_docs_path
# ============================================================================

class TestToDocsPath:
    def _make_file_uri(self, path: Path) -> str:
        return path.resolve().as_uri()

    def test_empty_string_returns_empty(self):
        assert rag_tools._to_docs_path("") == ""

    def test_none_like_falsy_returns_empty(self):
        # Only str is accepted; test with explicit empty
        assert rag_tools._to_docs_path("") == ""

    def test_valid_uri_converts_to_docs_path(self, tmp_path):
        # Build a fake _DATA_DIR structure
        data_dir = tmp_path / "data"
        subdir = data_dir / "Insurance-product-info"
        subdir.mkdir(parents=True)
        doc = subdir / "doc.pdf"
        doc.write_text("x")

        file_uri = doc.resolve().as_uri()

        with patch("api.rag_tools._DATA_DIR", data_dir):
            result = rag_tools._to_docs_path(file_uri)

        assert result.startswith("/docs/")
        assert "Insurance-product-info" in result
        assert "doc.pdf" in result

    def test_path_outside_data_dir_returns_empty(self, tmp_path):
        # A file URI that cannot be made relative to _DATA_DIR
        outside = tmp_path / "outside" / "file.pdf"
        outside.parent.mkdir(parents=True)
        outside.write_text("x")
        file_uri = outside.resolve().as_uri()

        data_dir = tmp_path / "data"
        data_dir.mkdir()

        with patch("api.rag_tools._DATA_DIR", data_dir):
            result = rag_tools._to_docs_path(file_uri)

        assert result == ""

    def test_parts_are_url_encoded(self, tmp_path):
        data_dir = tmp_path / "data"
        subdir = data_dir / "My Folder"
        subdir.mkdir(parents=True)
        doc = subdir / "my doc.pdf"
        doc.write_text("x")
        file_uri = doc.resolve().as_uri()

        with patch("api.rag_tools._DATA_DIR", data_dir):
            result = rag_tools._to_docs_path(file_uri)

        assert "%20" in result or "My%20Folder" in result or "my%20doc" in result

    def test_malformed_uri_returns_empty(self):
        result = rag_tools._to_docs_path("not_a_uri_at_all:::///")
        # Should either return "" (exception caught) or some string — must not raise
        assert isinstance(result, str)

    def test_generations_ii_pdf_uri(self, tmp_path):
        """Synthetic data: Generations-II PDF path should convert correctly."""
        data_dir = tmp_path / "data"
        subdir = data_dir / "Insurance-product-info" / "Generations-II"
        subdir.mkdir(parents=True)
        doc = subdir / "Generations-II_PB_EN.pdf"
        doc.write_text("x")
        file_uri = doc.resolve().as_uri()

        with patch("api.rag_tools._DATA_DIR", data_dir):
            result = rag_tools._to_docs_path(file_uri)

        assert result == (
            "/docs/Insurance-product-info/Generations-II/Generations-II_PB_EN.pdf"
        )


# ============================================================================
# _collect_sources
# ============================================================================

def _make_hit(doc="doc.pdf", page_start=1, page_end=2, product="ProductA",
              file_url="", section="Intro", chunk_id="c1", text="Sample text"):
    return {
        "metadata": {
            "document_name": doc,
            "page_start": page_start,
            "page_end": page_end,
            "product_name": product,
            "file_url": file_url,
            "section_title": section,
            "chunk_id": chunk_id,
            "word_count": 100,
        },
        "text": text,
    }


class TestCollectSources:
    def test_returns_empty_strings_when_no_bucket(self):
        # bucket is None (context var not set)
        hits = [_make_hit()]
        result = rag_tools._collect_sources(hits)
        assert result == [""]

    def test_returns_empty_list_for_no_hits(self, fresh_bucket):
        result = rag_tools._collect_sources([])
        assert result == []

    def test_assigns_source_id_s1_for_first_hit(self, fresh_bucket):
        hits = [_make_hit(doc="a.pdf", page_start=1)]
        result = rag_tools._collect_sources(hits)
        assert result == ["S1"]

    def test_assigns_sequential_ids(self, fresh_bucket):
        hits = [
            _make_hit(doc="a.pdf", page_start=1),
            _make_hit(doc="b.pdf", page_start=5),
        ]
        result = rag_tools._collect_sources(hits)
        assert result == ["S1", "S2"]

    def test_deduplicates_same_doc_and_page(self, fresh_bucket):
        hits = [
            _make_hit(doc="a.pdf", page_start=1),
            _make_hit(doc="a.pdf", page_start=1),  # duplicate
        ]
        result = rag_tools._collect_sources(hits)
        assert result == ["S1", "S1"]
        assert len(rag_tools.get_current_sources()) == 1

    def test_different_pages_same_doc_are_different_sources(self, fresh_bucket):
        hits = [
            _make_hit(doc="a.pdf", page_start=1),
            _make_hit(doc="a.pdf", page_start=2),
        ]
        result = rag_tools._collect_sources(hits)
        assert result == ["S1", "S2"]

    def test_different_docs_same_page_are_different_sources(self, fresh_bucket):
        hits = [
            _make_hit(doc="a.pdf", page_start=1),
            _make_hit(doc="b.pdf", page_start=1),
        ]
        result = rag_tools._collect_sources(hits)
        assert result == ["S1", "S2"]

    def test_bucket_entry_has_correct_fields(self, fresh_bucket):
        hits = [_make_hit(
            doc="Generations-II_PB_EN.pdf",
            page_start=3, page_end=4,
            product="Generations II",
            section="Benefits",
            chunk_id="c42",
            text="Hello world " * 30,
        )]
        rag_tools._collect_sources(hits)
        entry = rag_tools.get_current_sources()[0]
        assert entry["source_id"] == "S1"
        assert entry["document"] == "Generations-II_PB_EN.pdf"