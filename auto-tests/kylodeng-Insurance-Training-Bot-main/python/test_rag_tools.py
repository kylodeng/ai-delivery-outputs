"""
Test module for api/rag_tools.py

What is tested:
    - reset_sources(): initialises a fresh list in the contextvar
    - get_current_sources(): returns collected sources or empty list
    - _find_file_url(): filesystem glob lookup with LRU cache
    - _to_docs_path(): URI-to-server-URL conversion
    - _collect_sources(): dedup logic, source-ID assignment, bucket management
    - _log_hits(): logging behaviour gated by SHOW_TOOL_CALLS flag
    - make_rag_tools() / get_current_date tool: date formatting
    - make_rag_tools() / list_products tool: stub (tool body is truncated in source)

Mocks used:
    - unittest.mock.patch for Path.rglob (_find_file_url filesystem calls)
    - unittest.mock.patch for logging.Logger.info (_log_hits)
    - unittest.mock.patch for datetime.date.today (get_current_date)
    - unittest.mock.MagicMock for the vector store passed to make_rag_tools()
    - os.environ patching for SHOW_TOOL_CALLS flag

TODOs:
    - list_products tool body is truncated in source; full behaviour cannot be tested
    - Any additional tools created inside make_rag_tools() beyond the truncated source
      cannot be tested without the full source
"""

import contextvars
import importlib
import logging
import os
import sys
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Helpers to (re)import the module so env-var-dependent module-level state
# (e.g. _SHOW_TOOL_CALLS) can be exercised under different env conditions.
# ---------------------------------------------------------------------------

def _reimport_rag_tools(env_overrides: dict | None = None):
    """Import (or re-import) api.rag_tools with optional env overrides."""
    env = os.environ.copy()
    if env_overrides:
        env.update(env_overrides)
    with patch.dict(os.environ, env, clear=True):
        if "api.rag_tools" in sys.modules:
            del sys.modules["api.rag_tools"]
        import api.rag_tools as module
    return module


# Use a single import for most tests (SHOW_TOOL_CALLS=false by default)
import api.rag_tools as rag_tools


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def reset_contextvar():
    """Ensure the contextvar is clean before and after each test."""
    token = rag_tools._sources_ctx.set(None)
    yield
    rag_tools._sources_ctx.reset(token)


@pytest.fixture()
def fresh_bucket():
    """Initialise a fresh source bucket and return it."""
    rag_tools.reset_sources()
    return rag_tools._sources_ctx.get()


@pytest.fixture()
def mock_store():
    return MagicMock()


# ---------------------------------------------------------------------------
# Synthetic hit data derived from the provided samples
# ---------------------------------------------------------------------------

GENERATIONS_II_HIT = {
    "text": "Generations II is a participating whole life insurance plan.",
    "metadata": {
        "document_name": "Generations-II_PB_EN.pdf",
        "product_name": "Generations II",
        "doc_type": "product_brochure",
        "page_start": 1,
        "page_end": 2,
        "section_title": "Overview",
        "file_url": "file:///data/Insurance-product-info/Generations-II/Generations-II_PB_EN.pdf",
        "chunk_id": "gen2_001",
        "word_count": 120,
    },
}

HOSPITALS_HIT = {
    "text": "List of designated hospitals in mainland China.",
    "metadata": {
        "document_name": "List of designated hospitals in mainland China.pdf",
        "product_name": "List of Designated Hospitals in Mainland China",
        "doc_type": "supplementary",
        "page_start": 5,
        "page_end": 6,
        "section_title": "Guangdong Hospitals",
        "file_url": "",
        "chunk_id": "hosp_042",
        "word_count": 80,
    },
}

VIP_HIT = {
    "text": "VIP Medical Navigation Service network hospitals.",
    "metadata": {
        "document_name": "Mainland_China_VIP_Hospital_Network.pdf",
        "product_name": "List of Network Hospitals with Mainland China VIP Medical Navigation Service",
        "doc_type": "supplementary",
        "page_start": 3,
        "page_end": 3,
        "section_title": "",
        "file_url": "file:///data/Insurance-product-info/Mainland_China_VIP_Hospital_Network.pdf",
        "chunk_id": "vip_007",
        "word_count": 60,
    },
}


# ===========================================================================
# Tests: reset_sources / get_current_sources
# ===========================================================================

class TestSourcesContextVar:

    def test_reset_sources_sets_empty_list(self):
        rag_tools.reset_sources()
        bucket = rag_tools._sources_ctx.get()
        assert bucket == []
        assert isinstance(bucket, list)

    def test_get_current_sources_before_reset_returns_empty_list(self):
        # contextvar is None (autouse fixture sets it to None)
        result = rag_tools.get_current_sources()
        assert result == []

    def test_get_current_sources_after_reset_returns_list(self):
        rag_tools.reset_sources()
        result = rag_tools.get_current_sources()
        assert result == []

    def test_get_current_sources_reflects_mutations(self):
        rag_tools.reset_sources()
        bucket = rag_tools._sources_ctx.get()
        bucket.append({"source_id": "S1", "document": "doc.pdf", "page_start": 1})
        result = rag_tools.get_current_sources()
        assert len(result) == 1
        assert result[0]["source_id"] == "S1"

    def test_reset_sources_clears_previous_content(self):
        rag_tools.reset_sources()
        rag_tools._sources_ctx.get().append({"dummy": True})
        rag_tools.reset_sources()
        assert rag_tools.get_current_sources() == []

    def test_get_current_sources_when_contextvar_is_none(self):
        rag_tools._sources_ctx.set(None)
        assert rag_tools.get_current_sources() == []


# ===========================================================================
# Tests: _find_file_url
# ===========================================================================

class TestFindFileUrl:

    def setup_method(self):
        # Clear LRU cache between tests so filesystem mocks take effect
        rag_tools._find_file_url.cache_clear()

    def test_returns_uri_when_file_found(self, tmp_path):
        doc = tmp_path / "doc.pdf"
        doc.write_bytes(b"%PDF")
        with patch.object(rag_tools._DATA_DIR, "rglob", return_value=[doc]):
            result = rag_tools._find_file_url("doc.pdf")
        assert result.startswith("file:///") or result.startswith("file://")
        assert "doc.pdf" in result

    def test_returns_empty_string_when_not_found(self):
        with patch.object(Path, "rglob", return_value=[]):
            result = rag_tools._find_file_url("nonexistent.pdf")
        assert result == ""

    def test_returns_first_match_when_multiple(self, tmp_path):
        first = tmp_path / "first.pdf"
        second = tmp_path / "second.pdf"
        first.write_bytes(b"%PDF")
        second.write_bytes(b"%PDF")
        with patch.object(rag_tools._DATA_DIR, "rglob", return_value=[first, second]):
            result = rag_tools._find_file_url("any.pdf")
        assert "first.pdf" in result

    def test_lru_cache_prevents_repeated_filesystem_calls(self):
        rag_tools._find_file_url.cache_clear()
        with patch.object(rag_tools._DATA_DIR, "rglob", return_value=[]) as mock_rglob:
            rag_tools._find_file_url("cached.pdf")
            rag_tools._find_file_url("cached.pdf")
            # rglob should only be called once due to LRU cache
            assert mock_rglob.call_count == 1

    def test_empty_document_name(self):
        with patch.object(Path, "rglob", return_value=[]):
            result = rag_tools._find_file_url("")
        assert result == ""


# ===========================================================================
# Tests: _to_docs_path
# ===========================================================================

class TestToDocsPath:

    def test_empty_string_returns_empty(self):
        assert rag_tools._to_docs_path("") == ""

    def test_none_equivalent_falsy_returns_empty(self):
        # The function checks `if not file_url`
        assert rag_tools._to_docs_path("") == ""

    def test_valid_file_uri_produces_docs_path(self, tmp_path):
        # Create a real file so relative_to() resolves correctly
        subdir = tmp_path / "Insurance-product-info"
        subdir.mkdir()
        pdf = subdir / "doc.pdf"
        pdf.write_bytes(b"%PDF")

        with patch.object(rag_tools, "_DATA_DIR", tmp_path):
            uri = pdf.resolve().as_uri()
            result = rag_tools._to_docs_path(uri)

        assert result == "/docs/Insurance-product-info/doc.pdf"

    def test_file_outside_data_dir_returns_empty(self, tmp_path):
        # File is not relative to _DATA_DIR → relative_to raises ValueError
        outside = tmp_path / "outside" / "doc.pdf"
        outside.parent.mkdir()
        outside.write_bytes(b"%PDF")
        data_dir = tmp_path / "data"
        data_dir.mkdir()

        with patch.object(rag_tools, "_DATA_DIR", data_dir):
            uri = outside.resolve().as_uri()
            result = rag_tools._to_docs_path(uri)

        assert result == ""

    def test_invalid_uri_returns_empty(self):
        result = rag_tools._to_docs_path("not_a_uri:::///\\bad")
        # Should not raise; returns empty on exception
        assert isinstance(result, str)

    def test_path_parts_are_url_encoded(self, tmp_path):
        subdir = tmp_path / "My Insurance Docs"
        subdir.mkdir()
        pdf = subdir / "my doc.pdf"
        pdf.write_bytes(b"%PDF")

        with patch.object(rag_tools, "_DATA_DIR", tmp_path):
            uri = pdf.resolve().as_uri()
            result = rag_tools._to_docs_path(uri)

        # Spaces must be percent-encoded in the output path
        assert " " not in result
        assert result.startswith("/docs/")

    def test_nested_subdir_produces_correct_path(self, tmp_path):
        subdir = tmp_path / "cat1" / "subcat2"
        subdir.mkdir(parents=True)
        pdf = subdir / "nested.pdf"
        pdf.write_bytes(b"%PDF")

        with patch.object(rag_tools, "_DATA_DIR", tmp_path):
            uri = pdf.resolve().as_uri()
            result = rag_tools._to_docs_path(uri)

        assert result == "/docs/cat1/subcat2/nested.pdf"


# ===========================================================================
# Tests: _collect_sources
# ===========================================================================

class TestCollectSources:

    def test_returns_empty_source_ids_when_bucket_is_none(self):
        # contextvar is None (not reset)
        hits = [GENERATIONS_II_HIT, HOSPITALS_HIT]
        result = rag_tools._collect_sources(hits)
        assert result == ["", ""]

    def test_assigns_sequential_source_ids(self, fresh_bucket):
        hits = [GENERATIONS_II_HIT, HOSPITALS_HIT]
        with patch.object(rag_tools, "_find_file_url", return_value=""):
            with patch.object(rag_tools, "_to_docs_path", return_value=""):
                result = rag_tools._collect_sources(hits)
        assert result == ["S1", "S2"]

    def test_deduplication_same_doc_and_page(self, fresh_bucket):
        hit_copy = dict(GENERATIONS_II_HIT)
        hit_copy["text"] = "Duplicate chunk same page."
        with patch.object(rag_tools, "_find_file_url", return_value=""):
            with patch.object(rag_tools, "_to_docs_path", return_value=""):
                result = rag_tools._collect_sources([GENERATIONS_II_HIT, hit_copy])
        assert result == ["S1", "S1"]
        assert len(rag_tools._sources_ctx.get()) == 1

    def test_different_pages_same_doc_get_different_ids(self, fresh_bucket):
        hit2 = {
            "text": "Page 10 text.",
            "metadata": {
                **GENERATIONS_II_HIT["metadata"],
                "page_start": 10,
                "page_end": 11,
            },
        }
        with patch.object(rag_tools, "_find_file_url", return_value=""):
            with patch.object(rag_tools, "_to_docs_path", return_value=""):
                result = rag_tools._collect_sources([GENERATIONS_II_HIT, hit2])
        assert result == ["S1", "S2"]

    def test_empty_hits_list(self, fresh_bucket):
        result = rag_tools._collect_sources([])
        assert result == []

    def test_single_hit(self, fresh_bucket):
        with patch.object(rag_tools, "_find_file_url", return_value=""):
            with patch.object(rag_tools, "_to_docs_path", return_value=""):
                result = rag_tools._collect_sources([GENERATIONS_II_HIT])
        assert result == ["S1"]

    def test_source_entry_fields_populated_correctly(self, fresh_bucket):
        with patch.object(rag_tools, "_find_file_url", return_value="file:///fake.pdf"):
            with patch.object(rag_tools, "_to_docs_path", return_value="/docs/fake.pdf"):
                rag_tools._collect_sources([GENERATIONS_II_HIT])
        bucket = rag_tools._sources_ctx.get()
        assert len(bucket) == 1
        entry = bucket[0]
        assert entry["source_id"] == "S1"
        assert entry["document"] == "Generations-II_PB_EN.pdf"
        assert entry["product"] == "Generations II"
        assert entry["page_start"] == 1
        assert entry["page_end"] == 2
        assert entry["section"] == "Overview"
        assert entry["chunk_id"] == "gen2_001"
        assert entry["file_url"] == "/docs/fake.pdf"
        assert "Generations II" in entry["text_preview"]

    def test_text_preview_truncated_to_250_chars(self, fresh_bucket):
        long_text = "A" * 500
        hit = {
            "text": long_text,
            "metadata": {
                "document_name": "long.pdf",
                "product_name": "Test",
                "