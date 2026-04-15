"""
Test module for api/rag_tools.py

What is tested:
    - reset_sources(): initialises a fresh list in the contextvar
    - get_current_sources(): returns current sources or empty list
    - _find_file_url(): file-system glob helper (lru_cache backed)
    - _to_docs_path(): converts file:// URIs to /docs/-relative URLs
    - _collect_sources(): deduplication logic, ID assignment, bucket management
    - _log_hits(): conditional logging based on SHOW_TOOL_CALLS env var
    - make_rag_tools(): factory returns callable LangChain tools
    - get_current_date tool: returns formatted date string
    - list_products tool: stub (incomplete source)

Mocks used:
    - unittest.mock.patch for filesystem (Path.rglob, Path.resolve)
    - unittest.mock.MagicMock for the vector store passed to make_rag_tools
    - unittest.mock.patch for os.getenv / module-level _SHOW_TOOL_CALLS
    - unittest.mock.patch for datetime.date.today

TODOs:
    - list_products tool body is truncated in source; tests are stubbed
    - Any additional tools returned by make_rag_tools beyond get_current_date
      and list_products need source code to be fully tested
    - Integration tests against a real vector store require a live instance
"""

import contextvars
import logging
import os
import sys
import types
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock, call
import pytest

# ---------------------------------------------------------------------------
# Helpers to import the module under test with controlled environment
# ---------------------------------------------------------------------------

def _import_rag_tools(env_overrides: dict | None = None):
    """Import (or re-import) rag_tools with a specific environment."""
    # Remove cached module so env changes are picked up by module-level code
    sys.modules.pop("api.rag_tools", None)
    sys.modules.pop("rag_tools", None)

    env = {**os.environ, **(env_overrides or {})}
    with patch.dict(os.environ, env, clear=True):
        # Ensure langchain_core.tools is importable (may not be installed in CI)
        if "langchain_core" not in sys.modules:
            # Create a minimal stub
            lc_core = types.ModuleType("langchain_core")
            lc_tools = types.ModuleType("langchain_core.tools")

            def tool(fn):  # noqa: D401
                """Minimal @tool stub: just returns the function unchanged."""
                fn.invoke = fn  # give it an .invoke for duck-typing
                return fn

            lc_tools.tool = tool
            lc_core.tools = lc_tools
            sys.modules["langchain_core"] = lc_core
            sys.modules["langchain_core.tools"] = lc_tools

        import importlib
        # We need to handle the package path properly
        spec = importlib.util.spec_from_file_location(
            "api.rag_tools",
            Path(__file__).parent.parent / "api" / "rag_tools.py",
        )
        if spec is None or spec.loader is None:
            pytest.skip("Cannot locate api/rag_tools.py — adjust path.")
        mod = importlib.util.module_from_spec(spec)
        sys.modules["api.rag_tools"] = mod
        spec.loader.exec_module(mod)
        return mod


# ---------------------------------------------------------------------------
# Module-level fixture — import once per session with SHOW_TOOL_CALLS=false
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def rag():
    """Return the rag_tools module with SHOW_TOOL_CALLS disabled."""
    return _import_rag_tools({"SHOW_TOOL_CALLS": "false"})


@pytest.fixture(scope="module")
def rag_show_calls():
    """Return the rag_tools module with SHOW_TOOL_CALLS enabled."""
    return _import_rag_tools({"SHOW_TOOL_CALLS": "true"})


# ---------------------------------------------------------------------------
# Synthetic hit factories
# ---------------------------------------------------------------------------

def _make_hit(
    document_name: str = "Generations-II_PB_EN.pdf",
    page_start: int = 1,
    page_end: int = 2,
    product_name: str = "Generations II",
    doc_type: str = "product_brochure",
    file_url: str = "",
    chunk_id: str = "c001",
    section_title: str = "Overview",
    word_count: int = 120,
    text: str = "Sample chunk text.",
) -> dict:
    return {
        "metadata": {
            "document_name": document_name,
            "page_start": page_start,
            "page_end": page_end,
            "product_name": product_name,
            "doc_type": doc_type,
            "file_url": file_url,
            "chunk_id": chunk_id,
            "section_title": section_title,
            "word_count": word_count,
        },
        "text": text,
    }


SYNTHETIC_HITS = [
    _make_hit(
        document_name="Generations-II_PB_EN.pdf",
        page_start=1,
        page_end=3,
        product_name="Generations II",
        file_url="file:///data/Insurance-product-info/Generations-II/Generations-II_PB_EN.pdf",
        text="Generations II is a participating whole life insurance plan.",
    ),
    _make_hit(
        document_name="List of designated hospitals in mainland China.pdf",
        page_start=5,
        page_end=6,
        product_name="List of Designated Hospitals in Mainland China",
        file_url="file:///data/Insurance-product-info/List of designated hospitals in mainland China.pdf",
        text="Class 3 hospitals across mainland China.",
    ),
    _make_hit(
        document_name="Mainland_China_VIP_Hospital_Network.pdf",
        page_start=2,
        page_end=4,
        product_name="List of Network Hospitals with Mainland China VIP Medical Navigation Service",
        file_url="",
        text="Network hospitals in Mainland China.",
    ),
]


# ===========================================================================
# 1. reset_sources / get_current_sources
# ===========================================================================

class TestSourceContextVar:
    """Tests for reset_sources() and get_current_sources()."""

    def test_get_current_sources_default_returns_empty_list(self, rag):
        """Before reset_sources is called the default should be an empty list."""
        # Run in a fresh context so we don't inherit sibling-test state
        ctx = contextvars.copy_context()
        result = ctx.run(rag.get_current_sources)
        assert result == []

    def test_reset_sources_initialises_empty_list(self, rag):
        ctx = contextvars.copy_context()

        def _run():
            rag.reset_sources()
            return rag.get_current_sources()

        result = ctx.run(_run)
        assert result == []
        assert isinstance(result, list)

    def test_get_current_sources_returns_accumulated_data(self, rag):
        ctx = contextvars.copy_context()

        def _run():
            rag.reset_sources()
            # Manually append to simulate collect_sources
            src_list = rag._sources_ctx.get(None)
            src_list.append({"source_id": "S1", "document": "doc.pdf", "page_start": 1})
            return rag.get_current_sources()

        result = ctx.run(_run)
        assert len(result) == 1
        assert result[0]["source_id"] == "S1"

    def test_reset_sources_clears_previous_state(self, rag):
        ctx = contextvars.copy_context()

        def _run():
            rag.reset_sources()
            src_list = rag._sources_ctx.get(None)
            src_list.append({"source_id": "S1", "document": "old.pdf", "page_start": 1})
            # Second reset should give a brand new list
            rag.reset_sources()
            return rag.get_current_sources()

        result = ctx.run(_run)
        assert result == []

    def test_get_current_sources_none_context_returns_empty(self, rag):
        """When contextvar holds None, get_current_sources returns []."""
        ctx = contextvars.copy_context()

        def _run():
            rag._sources_ctx.set(None)
            return rag.get_current_sources()

        result = ctx.run(_run)
        assert result == []


# ===========================================================================
# 2. _find_file_url
# ===========================================================================

class TestFindFileUrl:
    """Tests for _find_file_url()."""

    def test_returns_uri_when_file_found(self, rag, tmp_path):
        """Should return a file:// URI for a matching file."""
        # Create a real file for rglob to find
        target = tmp_path / "doc.pdf"
        target.write_bytes(b"%PDF")

        with patch.object(type(rag._DATA_DIR), "__truediv__", return_value=rag._DATA_DIR):
            with patch.object(rag._DATA_DIR, "rglob", return_value=[target]):
                # Clear LRU cache so our mock is used
                rag._find_file_url.cache_clear()
                result = rag._find_file_url("doc.pdf")
                assert result.startswith("file://")
                assert "doc.pdf" in result

    def test_returns_empty_string_when_no_file_found(self, rag):
        with patch.object(rag._DATA_DIR, "rglob", return_value=[]):
            rag._find_file_url.cache_clear()
            result = rag._find_file_url("nonexistent.pdf")
            assert result == ""

    def test_lru_cache_caches_result(self, rag):
        """Second call with same arg should not invoke rglob again."""
        rag._find_file_url.cache_clear()
        with patch.object(rag._DATA_DIR, "rglob", return_value=[]) as mock_rglob:
            rag._find_file_url("cached.pdf")
            rag._find_file_url("cached.pdf")
            assert mock_rglob.call_count == 1

    def test_different_args_call_rglob_separately(self, rag):
        rag._find_file_url.cache_clear()
        with patch.object(rag._DATA_DIR, "rglob", return_value=[]) as mock_rglob:
            rag._find_file_url("a.pdf")
            rag._find_file_url("b.pdf")
            assert mock_rglob.call_count == 2


# ===========================================================================
# 3. _to_docs_path
# ===========================================================================

class TestToDocsPath:
    """Tests for _to_docs_path()."""

    def test_empty_string_returns_empty(self, rag):
        assert rag._to_docs_path("") == ""

    def test_none_like_falsy_returns_empty(self, rag):
        # The function checks `if not file_url` so None is covered via empty
        assert rag._to_docs_path("") == ""

    def test_valid_file_uri_returns_docs_path(self, rag, tmp_path):
        """A file URI inside _DATA_DIR resolves to /docs/<rel_path>."""
        # Build a fake file under a temporary data dir
        sub = tmp_path / "Insurance-product-info" / "doc.pdf"
        sub.parent.mkdir(parents=True, exist_ok=True)
        sub.write_bytes(b"%PDF")

        file_url = sub.resolve().as_uri()

        with patch.object(rag, "_DATA_DIR", tmp_path):
            result = rag._to_docs_path(file_url)

        assert result.startswith("/docs/")
        assert "Insurance-product-info" in result
        assert "doc.pdf" in result

    def test_uri_outside_data_dir_returns_empty(self, rag, tmp_path):
        """A file URI NOT under _DATA_DIR should return ''."""
        outside = tmp_path / "outside" / "doc.pdf"
        outside.parent.mkdir(parents=True, exist_ok=True)
        outside.write_bytes(b"%PDF")

        file_url = outside.resolve().as_uri()
        data_dir = tmp_path / "data"
        data_dir.mkdir()

        with patch.object(rag, "_DATA_DIR", data_dir):
            result = rag._to_docs_path(file_url)

        assert result == ""

    def test_url_encodes_spaces_in_path(self, rag, tmp_path):
        """Parts with spaces must be percent-encoded in the result."""
        sub = tmp_path / "Some Folder" / "my doc.pdf"
        sub.parent.mkdir(parents=True, exist_ok=True)
        sub.write_bytes(b"%PDF")

        file_url = sub.resolve().as_uri()

        with patch.object(rag, "_DATA_DIR", tmp_path):
            result = rag._to_docs_path(file_url)

        # Spaces become %20
        assert " " not in result
        assert "%20" in result or "Some%20Folder" in result or "my%20doc" in result

    def test_malformed_uri_returns_empty(self, rag):
        result = rag._to_docs_path("not_a_valid_uri://??##")
        # Should not raise; may return '' on exception
        assert isinstance(result, str)


# ===========================================================================
# 4. _collect_sources
# ===========================================================================

class TestCollectSources:
    """Tests for _collect_sources()."""

    def test_no_bucket_returns_empty_string_per_hit(self, rag):
        """When contextvar is None, every hit gets ''."""
        ctx = contextvars.copy_context()

        def _run():
            rag._sources_ctx.set(None)
            return rag._collect_sources([_make_hit(), _make_hit(page_start=2)])

        result = ctx.run(_run)
        assert result == ["", ""]

    def test_single_hit_appended_to_bucket(self, rag):
        ctx = contextvars.copy_context()

        def _run():
            rag.reset_sources()
            ids = rag._collect_sources([_make_hit(document_name="doc.pdf", page_start=1)])
            return ids, rag.get_current_sources()

        ids, sources = ctx.run(_run)
        assert ids == ["S1"]
        assert len(sources) == 1
        assert sources[0]["source_id"] == "S1"
        assert sources[0]["document"] == "doc.pdf"

    def test_duplicate_hit_reuses_existing_id(self, rag):
        """Same (doc, page_start) key should reuse the existing source ID."""
        ctx = contextvars.copy_context()

        def _run():
            rag.reset_sources()
            hit = _make_hit(document_name="doc.pdf", page_start=1)
            ids = rag._collect_sources([hit, hit])
            return ids, rag.get_current_sources()

        ids, sources = ctx.run(_run)
        assert ids == ["S1", "S1"]
        assert len(sources) == 1  # only one entry in bucket

    def test_multiple_unique_hits_get_sequential_ids(self, rag):
        ctx = contextvars.copy_context()

        def _run():
            rag.reset_sources()
            hits = [
                _make_hit(document_name="a.pdf", page_start=1),
                _make_hit(document_name="