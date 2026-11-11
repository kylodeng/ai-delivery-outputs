"""
Test module for api/rag_tools.py

What is tested:
- reset_sources(): initialises a fresh list in the contextvar
- get_current_sources(): returns current sources list or empty list
- _find_file_url(): filesystem glob to find a document by name
- _to_docs_path(): converts file:/// URIs to /docs/-relative server URLs
- _collect_sources(): appends unique source dicts, deduplicates by (doc, page_start)
- _log_hits(): logs chunk metadata when SHOW_TOOL_CALLS=true
- make_rag_tools(): factory returns a list of LangChain tools
- get_current_date tool: returns today's date string
- list_products tool: truncated in source, stub provided

Mocks used:
- unittest.mock.patch for filesystem (_DATA_DIR, Path.rglob)
- unittest.mock.patch for os.getenv / _SHOW_TOOL_CALLS
- unittest.mock.MagicMock for the vector store passed to make_rag_tools
- unittest.mock.patch for date.today in get_current_date tool
- Temporary directories (tmp_path) for _find_file_url filesystem tests

TODOs:
- list_products tool body is truncated in source; full behaviour cannot be tested
- Any tools beyond get_current_date and list_products (source truncated)
- Integration tests against a real vector store
"""

import contextvars
import os
import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

# ---------------------------------------------------------------------------
# Helpers to import module under test with controllable env
# ---------------------------------------------------------------------------

def _import_rag_tools():
    """Re-import the module so module-level constants pick up monkeypatched env."""
    import importlib
    import api.rag_tools as mod
    importlib.reload(mod)
    return mod


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def reset_contextvar():
    """Ensure the contextvar is clean before every test."""
    import api.rag_tools as mod
    mod._sources_ctx.set(None)
    yield
    mod._sources_ctx.set(None)


@pytest.fixture()
def mod():
    import api.rag_tools as m
    return m


@pytest.fixture()
def mock_store():
    return MagicMock()


@pytest.fixture()
def sample_hits():
    """Minimal hit dicts that match the expected schema."""
    return [
        {
            "text": "Generations II is a whole life plan." * 10,  # >250 chars
            "metadata": {
                "document_name": "Generations-II_PB_EN.pdf",
                "product_name": "Generations II",
                "doc_type": "product_brochure",
                "page_start": 1,
                "page_end": 2,
                "section_title": "Overview",
                "file_url": "file:///data/Insurance-product-info/Generations-II/Generations-II_PB_EN.pdf",
                "chunk_id": "chunk-001",
                "word_count": 120,
            },
        },
        {
            "text": "Hospital list content.",
            "metadata": {
                "document_name": "List of designated hospitals in mainland China.pdf",
                "product_name": "List of Designated Hospitals in Mainland China",
                "doc_type": "supplementary",
                "page_start": 3,
                "page_end": 4,
                "section_title": "",
                "file_url": "",
                "chunk_id": "chunk-002",
                "word_count": 50,
            },
        },
    ]


@pytest.fixture()
def duplicate_hit(sample_hits):
    """A hit that shares (document_name, page_start) with the first sample hit."""
    return {
        "text": "Duplicate chunk from same page.",
        "metadata": {
            "document_name": "Generations-II_PB_EN.pdf",
            "product_name": "Generations II",
            "doc_type": "product_brochure",
            "page_start": 1,
            "page_end": 1,
            "section_title": "Overview",
            "file_url": "file:///data/Insurance-product-info/Generations-II/Generations-II_PB_EN.pdf",
            "chunk_id": "chunk-001b",
            "word_count": 10,
        },
    }


# ===========================================================================
# reset_sources / get_current_sources
# ===========================================================================

class TestResetSources:
    def test_sets_empty_list(self, mod):
        mod.reset_sources()
        assert mod._sources_ctx.get(None) == []

    def test_clears_existing_data(self, mod):
        mod._sources_ctx.set([{"source_id": "S1"}])
        mod.reset_sources()
        assert mod._sources_ctx.get(None) == []

    def test_idempotent_called_twice(self, mod):
        mod.reset_sources()
        mod.reset_sources()
        assert mod._sources_ctx.get(None) == []


class TestGetCurrentSources:
    def test_returns_empty_list_when_unset(self, mod):
        assert mod.get_current_sources() == []

    def test_returns_empty_list_when_none(self, mod):
        mod._sources_ctx.set(None)
        assert mod.get_current_sources() == []

    def test_returns_populated_list(self, mod):
        entries = [{"source_id": "S1", "document": "doc.pdf"}]
        mod._sources_ctx.set(entries)
        assert mod.get_current_sources() == entries

    def test_returns_same_reference(self, mod):
        entries = [{"source_id": "S1"}]
        mod._sources_ctx.set(entries)
        result = mod.get_current_sources()
        assert result is entries


# ===========================================================================
# _find_file_url
# ===========================================================================

class TestFindFileUrl:
    def test_finds_existing_file(self, tmp_path, mod):
        # Create a fake file inside a structure
        subdir = tmp_path / "Insurance-product-info" / "Generations-II"
        subdir.mkdir(parents=True)
        pdf = subdir / "Generations-II_PB_EN.pdf"
        pdf.write_bytes(b"%PDF")

        with patch.object(mod, "_DATA_DIR", tmp_path):
            # Clear lru_cache so the patch takes effect
            mod._find_file_url.cache_clear()
            result = mod._find_file_url("Generations-II_PB_EN.pdf")

        assert result.startswith("file:///") or result.startswith("file://")
        assert "Generations-II_PB_EN.pdf" in result
        mod._find_file_url.cache_clear()

    def test_returns_empty_string_when_not_found(self, tmp_path, mod):
        with patch.object(mod, "_DATA_DIR", tmp_path):
            mod._find_file_url.cache_clear()
            result = mod._find_file_url("nonexistent_document.pdf")

        assert result == ""
        mod._find_file_url.cache_clear()

    def test_returns_first_match_when_multiple(self, tmp_path, mod):
        for sub in ("a", "b"):
            d = tmp_path / sub
            d.mkdir()
            (d / "shared.pdf").write_bytes(b"%PDF")

        with patch.object(mod, "_DATA_DIR", tmp_path):
            mod._find_file_url.cache_clear()
            result = mod._find_file_url("shared.pdf")

        assert "shared.pdf" in result
        mod._find_file_url.cache_clear()

    def test_lru_cache_is_applied(self, tmp_path, mod):
        """The function must have lru_cache (cache_info exists)."""
        assert hasattr(mod._find_file_url, "cache_info")

    def test_cache_clear_resets_state(self, tmp_path, mod):
        mod._find_file_url.cache_clear()
        info_before = mod._find_file_url.cache_info()
        assert info_before.currsize == 0


# ===========================================================================
# _to_docs_path
# ===========================================================================

class TestToDocsPath:
    def test_empty_string_returns_empty(self, mod):
        assert mod._to_docs_path("") == ""

    def test_none_equivalent_empty(self, mod):
        # The guard is `if not file_url` — test falsy string
        assert mod._to_docs_path("") == ""

    def test_converts_posix_uri(self, tmp_path, mod):
        # Build a real file so resolve() can work
        subdir = tmp_path / "Insurance-product-info" / "Generations-II"
        subdir.mkdir(parents=True)
        pdf = subdir / "Generations-II_PB_EN.pdf"
        pdf.write_bytes(b"%PDF")

        file_url = pdf.resolve().as_uri()
        with patch.object(mod, "_DATA_DIR", tmp_path):
            result = mod._to_docs_path(file_url)

        assert result.startswith("/docs/")
        assert "Generations-II_PB_EN.pdf" in result

    def test_path_parts_are_url_encoded(self, tmp_path, mod):
        subdir = tmp_path / "My Folder"
        subdir.mkdir(parents=True)
        pdf = subdir / "My Doc.pdf"
        pdf.write_bytes(b"%PDF")

        file_url = pdf.resolve().as_uri()
        with patch.object(mod, "_DATA_DIR", tmp_path):
            result = mod._to_docs_path(file_url)

        # Spaces should be percent-encoded in the output
        assert " " not in result
        if result:  # only if relative_to succeeded
            assert "%20" in result or "My" in result

    def test_returns_empty_on_invalid_uri(self, mod):
        # A URI that cannot be made relative to _DATA_DIR should return ""
        result = mod._to_docs_path("file:///totally/unrelated/path/doc.pdf")
        assert result == ""

    def test_returns_empty_on_malformed_uri(self, mod):
        result = mod._to_docs_path("not_a_uri_at_all")
        # Either returns "" (exception caught) or a path — must not raise
        assert isinstance(result, str)

    def test_deep_nested_path(self, tmp_path, mod):
        subdir = tmp_path / "a" / "b" / "c"
        subdir.mkdir(parents=True)
        pdf = subdir / "deep.pdf"
        pdf.write_bytes(b"%PDF")

        file_url = pdf.resolve().as_uri()
        with patch.object(mod, "_DATA_DIR", tmp_path):
            result = mod._to_docs_path(file_url)

        assert result == "/docs/a/b/c/deep.pdf"


# ===========================================================================
# _collect_sources
# ===========================================================================

class TestCollectSources:
    def test_returns_empty_strings_when_bucket_is_none(self, mod, sample_hits):
        mod._sources_ctx.set(None)
        result = mod._collect_sources(sample_hits)
        assert result == [""] * len(sample_hits)

    def test_returns_empty_list_for_empty_hits(self, mod):
        mod.reset_sources()
        result = mod._collect_sources([])
        assert result == []

    def test_assigns_sequential_source_ids(self, mod, sample_hits):
        mod.reset_sources()
        with patch.object(mod, "_to_docs_path", return_value="/docs/fake.pdf"), \
             patch.object(mod, "_find_file_url", return_value="file:///fake.pdf"):
            result = mod._collect_sources(sample_hits)

        assert result == ["S1", "S2"]

    def test_duplicate_hit_reuses_existing_id(self, mod, sample_hits, duplicate_hit):
        mod.reset_sources()
        with patch.object(mod, "_to_docs_path", return_value="/docs/fake.pdf"), \
             patch.object(mod, "_find_file_url", return_value=""):
            # First ingest sample_hits so S1 / S2 are registered
            mod._collect_sources(sample_hits)
            # Now add a duplicate of sample_hits[0]
            result = mod._collect_sources([duplicate_hit])

        assert result == ["S1"]

    def test_bucket_grows_with_new_unique_hits(self, mod, sample_hits):
        mod.reset_sources()
        with patch.object(mod, "_to_docs_path", return_value="/docs/fake.pdf"), \
             patch.object(mod, "_find_file_url", return_value=""):
            mod._collect_sources(sample_hits)

        bucket = mod._sources_ctx.get(None)
        assert len(bucket) == 2

    def test_source_entry_fields(self, mod, sample_hits):
        mod.reset_sources()
        with patch.object(mod, "_to_docs_path", return_value="/docs/gen2.pdf"), \
             patch.object(mod, "_find_file_url", return_value=""):
            mod._collect_sources([sample_hits[0]])

        bucket = mod._sources_ctx.get(None)
        entry = bucket[0]
        assert entry["source_id"] == "S1"
        assert entry["document"] == "Generations-II_PB_EN.pdf"
        assert entry["product"] == "Generations II"
        assert entry["page_start"] == 1
        assert entry["page_end"] == 2
        assert entry["section"] == "Overview"
        assert entry["file_url"] == "/docs/gen2.pdf"
        assert entry["chunk_id"] == "chunk-001"
        # text_preview truncated at 250 chars
        assert len(entry["text_preview"]) <= 250

    def test_text_preview_truncated_at_250_chars(self, mod):
        mod.reset_sources()
        long_text = "x" * 500
        hit = {
            "text": long_text,
            "metadata": {
                "document_name": "doc.pdf",
                "page_start": 1,
            },
        }
        with patch.object(mod, "_to_docs_path", return_value=""), \
             patch.object(mod, "_find_file_url", return_value=""):
            mod._collect_sources([hit])

        bucket = mod._sources_ctx.get(None)
        assert len(bucket[0]["text_preview"]) == 250

    def test_missing_metadata_fields_use_defaults(self, mod):
        """Hits with minimal metadata should not raise and use '?' defaults."""
        mod.reset_sources()
        minimal_hit = {"text": "minimal", "metadata": {}}
        with patch.object(mod, "_to_docs_path", return_value=""), \
             patch.object(mod, "_find_file_url", return_value=""):
            result = mod._collect_sources([minimal_hit])

        assert result == ["S1"]
        bucket = mod._sources_ctx.get(None)
        assert bucket[0]["document"] == "?"
        assert bucket[0]["page_start"] == "?"

    def test_fallback_to_find_file_url_when_no_file_url(self, mod):
        mod.reset_sources()
        hit = {
            "text": "content",
            "metadata": {
                "document_name": "Network_Hospitals_with_Cashless_Arrangement.pdf",
                "page_start": 5,
                "file_url": "",  # empty → fallback
            },
        }
        with patch.object(mod, "_find_file_url", return_value="file:///fake/Network.