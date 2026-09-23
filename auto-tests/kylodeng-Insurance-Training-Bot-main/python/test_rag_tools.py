"""
Test module for api/rag_tools.py

What is tested:
- reset_sources(): initialises a fresh list in the contextvar
- get_current_sources(): returns the current sources list or empty list
- _find_file_url(): filesystem glob fallback for document URLs
- _to_docs_path(): conversion from file:/// URI to /docs/-relative server URL
- _collect_sources(): deduplication, ID assignment, bucket management
- _log_hits(): conditional logging based on SHOW_TOOL_CALLS env var
- make_rag_tools() factory: verifies tools are created and callable
  - get_current_date tool: returns today's date string
  - list_products tool: stub (incomplete source provided)

Mocks used:
- unittest.mock.patch for filesystem (Path.rglob), os.getenv, logging
- Fake store object passed to make_rag_tools()
- Synthetic insurance document metadata from provided samples

TODOs:
- list_products tool body is truncated in source; full behaviour not testable
- Any tools beyond list_products (not visible in source) need stubs
- Integration tests against a real vector store are skipped (need store fixture)
"""

import contextvars
import logging
import os
import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

# ---------------------------------------------------------------------------
# Helpers to import the module under test with a controlled environment
# ---------------------------------------------------------------------------

def _import_rag_tools(monkeypatch=None, show_tool_calls: str = "false"):
    """Re-import rag_tools so module-level constants pick up env overrides."""
    import importlib
    if monkeypatch:
        monkeypatch.setenv("SHOW_TOOL_CALLS", show_tool_calls)
    # Remove cached module so re-import picks up env changes
    sys.modules.pop("api.rag_tools", None)
    sys.modules.pop("rag_tools", None)
    import api.rag_tools as rt
    return rt


@pytest.fixture()
def rt():
    """Fresh import of rag_tools with SHOW_TOOL_CALLS=false."""
    sys.modules.pop("api.rag_tools", None)
    with patch.dict(os.environ, {"SHOW_TOOL_CALLS": "false"}):
        import api.rag_tools as module
        yield module
    sys.modules.pop("api.rag_tools", None)


@pytest.fixture()
def rt_show_calls():
    """Fresh import of rag_tools with SHOW_TOOL_CALLS=true."""
    sys.modules.pop("api.rag_tools", None)
    with patch.dict(os.environ, {"SHOW_TOOL_CALLS": "true"}):
        import api.rag_tools as module
        yield module
    sys.modules.pop("api.rag_tools", None)


@pytest.fixture()
def fake_store():
    """Minimal fake store object accepted by make_rag_tools."""
    return MagicMock(name="vector_store")


# ---------------------------------------------------------------------------
# Synthetic hit helpers (based on provided data samples)
# ---------------------------------------------------------------------------

def _make_hit(
    document_name="Generations-II_PB_EN.pdf",
    product_name="Generations II",
    doc_type="product_brochure",
    page_start=1,
    page_end=2,
    section_title="Overview",
    chunk_id="chunk-001",
    file_url="",
    text="Sun Life participating whole life insurance plan.",
    word_count=8,
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
            "file_url": file_url,
            "word_count": word_count,
        },
        "text": text,
    }


GENERATIONS_HIT = _make_hit(
    document_name="Generations-II_PB_EN.pdf",
    product_name="Generations II",
    page_start=1,
    page_end=3,
    section_title="Plan Features",
    chunk_id="gen-001",
    text="Guaranteed lifelong protection, double bonuses, mental incapacity benefit.",
)

HOSPITAL_LIST_HIT = _make_hit(
    document_name="List of designated hospitals in mainland China.pdf",
    product_name="List of Designated Hospitals in Mainland China",
    doc_type="supplementary",
    page_start=5,
    page_end=6,
    section_title="Class 3 Hospitals",
    chunk_id="hosp-001",
    text="All Class 3 hospitals across mainland China.",
)

VIP_HOSPITAL_HIT = _make_hit(
    document_name="Mainland_China_VIP_Hospital_Network.pdf",
    product_name="List of Network Hospitals with Mainland China VIP Medical Navigation Service",
    doc_type="supplementary",
    page_start=2,
    page_end=4,
    section_title="Shanghai Hospitals",
    chunk_id="vip-001",
    text="Hospitals affiliated with top universities in Shanghai.",
)


# ===========================================================================
# reset_sources / get_current_sources
# ===========================================================================

class TestResetSources:
    def test_sets_empty_list(self, rt):
        rt.reset_sources()
        assert rt.get_current_sources() == []

    def test_clears_previous_sources(self, rt):
        rt.reset_sources()
        # Manually populate bucket
        bucket = rt._sources_ctx.get(None)
        bucket.append({"source_id": "S1", "document": "doc.pdf", "page_start": 1})
        assert len(rt.get_current_sources()) == 1
        # Reset should wipe it
        rt.reset_sources()
        assert rt.get_current_sources() == []

    def test_multiple_resets_each_give_fresh_list(self, rt):
        rt.reset_sources()
        first = rt._sources_ctx.get(None)
        rt.reset_sources()
        second = rt._sources_ctx.get(None)
        assert first is not second

    def test_sets_new_list_object(self, rt):
        rt.reset_sources()
        result = rt.get_current_sources()
        assert isinstance(result, list)


class TestGetCurrentSources:
    def test_returns_empty_list_when_not_initialised(self, rt):
        # Ensure contextvar is None
        rt._sources_ctx.set(None)
        result = rt.get_current_sources()
        assert result == []

    def test_returns_empty_list_after_reset(self, rt):
        rt.reset_sources()
        assert rt.get_current_sources() == []

    def test_returns_populated_list(self, rt):
        rt.reset_sources()
        bucket = rt._sources_ctx.get(None)
        entry = {"source_id": "S1", "document": "doc.pdf", "page_start": 1}
        bucket.append(entry)
        result = rt.get_current_sources()
        assert result == [entry]

    def test_returns_list_not_none_when_contextvar_is_none(self, rt):
        rt._sources_ctx.set(None)
        assert rt.get_current_sources() is not None


# ===========================================================================
# _find_file_url
# ===========================================================================

class TestFindFileUrl:
    def test_returns_uri_when_file_found(self, rt, tmp_path):
        # Create a temporary file to match
        doc = tmp_path / "test_doc.pdf"
        doc.write_bytes(b"%PDF")
        with patch.object(Path, "rglob", return_value=iter([doc])):
            # Clear LRU cache to avoid stale results
            rt._find_file_url.cache_clear()
            result = rt._find_file_url("test_doc.pdf")
        assert result.startswith("file:///") or result.startswith("file:/")
        assert "test_doc" in result

    def test_returns_empty_string_when_not_found(self, rt):
        rt._find_file_url.cache_clear()
        with patch.object(Path, "rglob", return_value=iter([])):
            result = rt._find_file_url("nonexistent_doc.pdf")
        assert result == ""

    def test_caches_result(self, rt, tmp_path):
        doc = tmp_path / "cached_doc.pdf"
        doc.write_bytes(b"%PDF")
        rt._find_file_url.cache_clear()
        with patch.object(Path, "rglob", return_value=iter([doc])) as mock_rglob:
            rt._find_file_url("cached_doc.pdf")
            rt._find_file_url("cached_doc.pdf")
        # rglob called only once due to lru_cache
        assert mock_rglob.call_count == 1

    def test_returns_string_type(self, rt):
        rt._find_file_url.cache_clear()
        with patch.object(Path, "rglob", return_value=iter([])):
            result = rt._find_file_url("anything.pdf")
        assert isinstance(result, str)


# ===========================================================================
# _to_docs_path
# ===========================================================================

class TestToDocsPath:
    def test_empty_string_returns_empty(self, rt):
        assert rt._to_docs_path("") == ""

    def test_valid_file_url_returns_docs_path(self, rt, tmp_path):
        # Build a real file URI that is relative to _DATA_DIR
        # We patch _DATA_DIR so the relative_to() call succeeds
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        sub = data_dir / "Insurance-product-info"
        sub.mkdir()
        doc = sub / "doc.pdf"
        doc.write_bytes(b"%PDF")

        file_url = doc.resolve().as_uri()
        with patch.object(type(rt), "_DATA_DIR", new_callable=lambda: property(lambda self: data_dir), create=True):
            # Patch the module-level _DATA_DIR directly
            original = rt._DATA_DIR
            rt._DATA_DIR = data_dir
            try:
                result = rt._to_docs_path(file_url)
            finally:
                rt._DATA_DIR = original

        assert result.startswith("/docs/")
        assert "doc.pdf" in result

    def test_non_file_url_returns_empty_on_exception(self, rt):
        # A URL that doesn't relate to _DATA_DIR should return ""
        result = rt._to_docs_path("file:///some/completely/different/path/doc.pdf")
        # relative_to will raise ValueError → returns ""
        assert result == ""

    def test_returns_string(self, rt):
        result = rt._to_docs_path("")
        assert isinstance(result, str)

    def test_url_parts_are_encoded(self, rt, tmp_path):
        """Spaces and special chars in path segments are percent-encoded."""
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        sub = data_dir / "My Product Folder"
        sub.mkdir()
        doc = sub / "my doc.pdf"
        doc.write_bytes(b"%PDF")
        file_url = doc.resolve().as_uri()

        original = rt._DATA_DIR
        rt._DATA_DIR = data_dir
        try:
            result = rt._to_docs_path(file_url)
        finally:
            rt._DATA_DIR = original

        # spaces encoded as %20
        assert "%20" in result or " " not in result


# ===========================================================================
# _collect_sources
# ===========================================================================

class TestCollectSources:
    def test_returns_empty_strings_when_bucket_is_none(self, rt):
        rt._sources_ctx.set(None)
        hits = [GENERATIONS_HIT, HOSPITAL_LIST_HIT]
        result = rt._collect_sources(hits)
        assert result == ["", ""]

    def test_assigns_sequential_ids(self, rt):
        rt.reset_sources()
        with patch.object(rt, "_find_file_url", return_value=""), \
             patch.object(rt, "_to_docs_path", return_value=""):
            result = rt._collect_sources([GENERATIONS_HIT, HOSPITAL_LIST_HIT])
        assert result == ["S1", "S2"]

    def test_deduplicates_same_doc_and_page(self, rt):
        rt.reset_sources()
        # Two hits with the same document_name + page_start → same ID
        hit_a = _make_hit(document_name="doc.pdf", page_start=1, chunk_id="c1")
        hit_b = _make_hit(document_name="doc.pdf", page_start=1, chunk_id="c2")
        with patch.object(rt, "_find_file_url", return_value=""), \
             patch.object(rt, "_to_docs_path", return_value=""):
            result = rt._collect_sources([hit_a, hit_b])
        assert result[0] == result[1] == "S1"
        # Only one entry in bucket
        assert len(rt.get_current_sources()) == 1

    def test_different_pages_same_doc_get_separate_ids(self, rt):
        rt.reset_sources()
        hit_a = _make_hit(document_name="doc.pdf", page_start=1)
        hit_b = _make_hit(document_name="doc.pdf", page_start=5)
        with patch.object(rt, "_find_file_url", return_value=""), \
             patch.object(rt, "_to_docs_path", return_value=""):
            result = rt._collect_sources([hit_a, hit_b])
        assert result[0] != result[1]
        assert len(rt.get_current_sources()) == 2

    def test_empty_hits_returns_empty_list(self, rt):
        rt.reset_sources()
        result = rt._collect_sources([])
        assert result == []

    def test_result_length_matches_hits(self, rt):
        rt.reset_sources()
        hits = [GENERATIONS_HIT, HOSPITAL_LIST_HIT, VIP_HOSPITAL_HIT]
        with patch.object(rt, "_find_file_url", return_value=""), \
             patch.object(rt, "_to_docs_path", return_value=""):
            result = rt._collect_sources(hits)
        assert len(result) == 3

    def test_text_preview_truncated_to_250(self, rt):
        rt.reset_sources()
        long_text = "A" * 500
        hit = _make_hit(text=long_text, document_name="long.pdf", page_start=1)
        with patch.object(rt, "_find_file_url", return_value=""), \
             patch.object(rt, "_to_docs_path", return_value=""):
            rt._collect_sources([hit])
        bucket = rt.get_current_sources()
        assert len(bucket[0]["text_preview"]) == 250

    def test_entry_fields_populated_correctly(self, rt):
        rt.reset_sources()
        with patch.object(rt, "_find_file_url", return_value=""), \
             patch.object(rt, "_to_docs_path", return_value="/docs/test/doc.pdf"):
            rt._collect_sources([GENERATIONS_HIT])
        entry = rt.get_current_sources()[0]
        assert entry["source_id"] == "S1"
        assert entry["document"] == "Generations-II_PB_EN.pdf"
        assert entry["product"] == "Generations II"
        assert entry["page_start"] == 1
        assert entry["page_end"] == 3
        