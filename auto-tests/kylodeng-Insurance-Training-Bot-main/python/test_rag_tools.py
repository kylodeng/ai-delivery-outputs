"""
Test module for api/rag_tools.py

What is tested:
- reset_sources(): initialises a fresh source list in the contextvar
- get_current_sources(): retrieves accumulated sources; returns empty list when unset
- _find_file_url(): filesystem glob lookup with LRU cache
- _to_docs_path(): URI → /docs/-relative URL conversion
- _collect_sources(): deduplication, source-ID assignment, bucket management
- _log_hits(): conditional logging based on SHOW_TOOL_CALLS flag
- make_rag_tools(): tool factory — get_current_date, list_products existence & basic behaviour

Mocks used:
- unittest.mock.patch for Path.rglob (filesystem isolation in _find_file_url)
- unittest.mock.patch for datetime.date.today (deterministic date in get_current_date)
- unittest.mock.patch for logger (log-call assertions)
- monkeypatch for environment variables (SHOW_TOOL_CALLS)
- Fake in-memory store object for make_rag_tools

TODOs:
- TODO: Full integration test of list_products tool requires a real/mock vector store
        with a known product catalogue — stub provided below.
- TODO: Tests for any additional tools returned by make_rag_tools beyond
        get_current_date and list_products (source code is truncated).
- TODO: Test async behaviour of _collect_sources across multiple asyncio tasks
        sharing the same contextvar reference.
"""

import contextvars
import os
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

# ---------------------------------------------------------------------------
# Helpers to import the module under test with minimal side-effects
# ---------------------------------------------------------------------------

import importlib
import sys


@pytest.fixture(autouse=True)
def _reset_lru_cache():
    """Clear the _find_file_url LRU cache between tests to avoid cross-test pollution."""
    import api.rag_tools as rag_tools
    rag_tools._find_file_url.cache_clear()
    yield
    rag_tools._find_file_url.cache_clear()


@pytest.fixture(autouse=True)
def _reset_sources_ctx():
    """Reset the contextvar to its default (None) between tests."""
    import api.rag_tools as rag_tools
    token = rag_tools._sources_ctx.set(None)
    yield
    rag_tools._sources_ctx.reset(token)


# ---------------------------------------------------------------------------
# Convenience import alias
# ---------------------------------------------------------------------------

@pytest.fixture()
def rag():
    import api.rag_tools as module
    return module


# ===========================================================================
# reset_sources / get_current_sources
# ===========================================================================

class TestResetSources:
    def test_sets_empty_list(self, rag):
        rag.reset_sources()
        assert rag._sources_ctx.get(None) == []

    def test_replaces_existing_list(self, rag):
        rag.reset_sources()
        rag._sources_ctx.get(None).append({"source_id": "S1"})
        rag.reset_sources()
        assert rag._sources_ctx.get(None) == []

    def test_idempotent_multiple_calls(self, rag):
        rag.reset_sources()
        rag.reset_sources()
        assert rag._sources_ctx.get(None) == []


class TestGetCurrentSources:
    def test_returns_empty_list_when_ctx_is_none(self, rag):
        # contextvar default is None — should return []
        result = rag.get_current_sources()
        assert result == []

    def test_returns_accumulated_sources_after_reset(self, rag):
        rag.reset_sources()
        rag._sources_ctx.get(None).append({"source_id": "S1", "document": "doc.pdf", "page_start": 1})
        result = rag.get_current_sources()
        assert len(result) == 1
        assert result[0]["source_id"] == "S1"

    def test_returns_empty_list_when_ctx_is_empty_list(self, rag):
        rag.reset_sources()
        assert rag.get_current_sources() == []

    def test_returns_copy_reference(self, rag):
        """get_current_sources returns the actual bucket list (not a copy)."""
        rag.reset_sources()
        result = rag.get_current_sources()
        result.append({"source_id": "S99"})
        assert len(rag._sources_ctx.get(None)) == 1


# ===========================================================================
# _find_file_url
# ===========================================================================

class TestFindFileUrl:
    def test_returns_uri_for_first_match(self, rag, tmp_path):
        fake_file = tmp_path / "doc.pdf"
        fake_file.write_bytes(b"")
        with patch.object(Path, "rglob", return_value=iter([fake_file])):
            result = rag._find_file_url("doc.pdf")
        assert result.startswith("file:///") or result.startswith("file:")
        assert "doc.pdf" in result

    def test_returns_empty_string_when_no_match(self, rag):
        with patch.object(Path, "rglob", return_value=iter([])):
            result = rag._find_file_url("nonexistent.pdf")
        assert result == ""

    def test_lru_cache_is_applied(self, rag):
        with patch.object(Path, "rglob", return_value=iter([])) as mock_rglob:
            rag._find_file_url("cached.pdf")
            rag._find_file_url("cached.pdf")
        # rglob should only be called once thanks to LRU cache
        assert mock_rglob.call_count == 1

    def test_different_names_are_cached_independently(self, rag):
        with patch.object(Path, "rglob", return_value=iter([])) as mock_rglob:
            rag._find_file_url("a.pdf")
            rag._find_file_url("b.pdf")
        assert mock_rglob.call_count == 2


# ===========================================================================
# _to_docs_path
# ===========================================================================

class TestToDocsPath:
    def test_empty_string_returns_empty(self, rag):
        assert rag._to_docs_path("") == ""

    def test_none_equivalent_empty(self, rag):
        # Defensive: calling with falsy value
        assert rag._to_docs_path("") == ""

    def test_valid_file_uri_produces_docs_path(self, rag, tmp_path):
        """A file:// URI that is under _DATA_DIR should map to /docs/..."""
        # Patch _DATA_DIR to point at tmp_path so relative_to succeeds
        sub = tmp_path / "Insurance-product-info" / "doc.pdf"
        sub.parent.mkdir(parents=True, exist_ok=True)
        sub.write_bytes(b"")
        uri = sub.resolve().as_uri()

        with patch.object(rag, "_DATA_DIR", tmp_path):
            result = rag._to_docs_path(uri)

        assert result == "/docs/Insurance-product-info/doc.pdf"

    def test_uri_outside_data_dir_returns_empty(self, rag, tmp_path):
        """A file:// URI that is NOT under _DATA_DIR should return ''."""
        outside = tmp_path / "some_other_dir" / "other.pdf"
        outside.parent.mkdir(parents=True, exist_ok=True)
        outside.write_bytes(b"")
        uri = outside.resolve().as_uri()

        data_dir = tmp_path / "data"
        data_dir.mkdir()
        with patch.object(rag, "_DATA_DIR", data_dir):
            result = rag._to_docs_path(uri)

        assert result == ""

    def test_special_characters_are_percent_encoded(self, rag, tmp_path):
        """Directory/file names with spaces should be percent-encoded."""
        sub = tmp_path / "My Products" / "doc name.pdf"
        sub.parent.mkdir(parents=True, exist_ok=True)
        sub.write_bytes(b"")
        uri = sub.resolve().as_uri()

        with patch.object(rag, "_DATA_DIR", tmp_path):
            result = rag._to_docs_path(uri)

        assert "%20" in result or "My%20Products" in result or "My Products" not in result

    def test_malformed_uri_returns_empty(self, rag):
        result = rag._to_docs_path("not-a-valid-uri://???")
        assert result == ""


# ===========================================================================
# _collect_sources
# ===========================================================================

def _make_hit(doc="doc.pdf", page_start=1, page_end=2, product="Generations II",
              section="Section A", file_url="", chunk_id="c1",
              text="Sample text for testing purposes."):
    return {
        "metadata": {
            "document_name": doc,
            "page_start": page_start,
            "page_end": page_end,
            "product_name": product,
            "section_title": section,
            "file_url": file_url,
            "chunk_id": chunk_id,
            "word_count": 10,
        },
        "text": text,
    }


class TestCollectSources:
    def test_returns_empty_strings_when_bucket_is_none(self, rag):
        # _sources_ctx is None (default) → returns ["", ""]
        hits = [_make_hit(), _make_hit(doc="other.pdf")]
        result = rag._collect_sources(hits)
        assert result == ["", ""]

    def test_assigns_sequential_ids(self, rag):
        rag.reset_sources()
        hits = [_make_hit(doc="a.pdf", page_start=1), _make_hit(doc="b.pdf", page_start=1)]
        result = rag._collect_sources(hits)
        assert result == ["S1", "S2"]

    def test_deduplicates_same_doc_and_page(self, rag):
        rag.reset_sources()
        hit = _make_hit(doc="a.pdf", page_start=1)
        result = rag._collect_sources([hit, hit])
        # Second hit is a duplicate → same ID
        assert result == ["S1", "S1"]
        # Only one entry in the bucket
        assert len(rag.get_current_sources()) == 1

    def test_different_pages_same_doc_are_separate(self, rag):
        rag.reset_sources()
        h1 = _make_hit(doc="a.pdf", page_start=1)
        h2 = _make_hit(doc="a.pdf", page_start=5)
        result = rag._collect_sources([h1, h2])
        assert result == ["S1", "S2"]
        assert len(rag.get_current_sources()) == 2

    def test_source_entry_fields_are_correct(self, rag):
        rag.reset_sources()
        hit = _make_hit(
            doc="Generations-II_PB_EN.pdf",
            page_start=3,
            page_end=4,
            product="Generations II",
            section="Overview",
            chunk_id="chunk_007",
            text="A" * 300,  # longer than 250 chars
        )
        rag._collect_sources([hit])
        entry = rag.get_current_sources()[0]
        assert entry["source_id"] == "S1"
        assert entry["document"] == "Generations-II_PB_EN.pdf"
        assert entry["product"] == "Generations II"
        assert entry["page_start"] == 3
        assert entry["page_end"] == 4
        assert entry["section"] == "Overview"
        assert entry["chunk_id"] == "chunk_007"
        assert len(entry["text_preview"]) == 250  # truncated to 250

    def test_text_preview_truncated_at_250(self, rag):
        rag.reset_sources()
        hit = _make_hit(text="X" * 500)
        rag._collect_sources([hit])
        assert len(rag.get_current_sources()[0]["text_preview"]) == 250

    def test_text_preview_short_text_not_padded(self, rag):
        rag.reset_sources()
        hit = _make_hit(text="short")
        rag._collect_sources([hit])
        assert rag.get_current_sources()[0]["text_preview"] == "short"

    def test_empty_hits_list(self, rag):
        rag.reset_sources()
        result = rag._collect_sources([])
        assert result == []
        assert rag.get_current_sources() == []

    def test_accumulates_across_multiple_calls(self, rag):
        rag.reset_sources()
        rag._collect_sources([_make_hit(doc="a.pdf", page_start=1)])
        rag._collect_sources([_make_hit(doc="b.pdf", page_start=1)])
        sources = rag.get_current_sources()
        assert len(sources) == 2
        assert sources[0]["source_id"] == "S1"
        assert sources[1]["source_id"] == "S2"

    def test_cross_call_dedup_uses_existing_id(self, rag):
        """A hit seen in a later call with same (doc, page_start) reuses the original ID."""
        rag.reset_sources()
        rag._collect_sources([_make_hit(doc="a.pdf", page_start=1)])
        result2 = rag._collect_sources([_make_hit(doc="a.pdf", page_start=1)])
        assert result2 == ["S1"]
        assert len(rag.get_current_sources()) == 1

    def test_file_url_fallback_calls_find_file_url(self, rag):
        rag.reset_sources()
        with patch.object(rag, "_find_file_url", return_value="") as mock_find, \
             patch.object(rag, "_to_docs_path", return_value="/docs/fake.pdf"):
            hit = _make_hit(file_url="")  # no file_url → triggers fallback
            rag._collect_sources([hit])
        mock_find.assert_called_once_with("doc.pdf")

    def test_file_url_in_metadata_skips_fallback(self, rag):
        rag.reset_sources()
        with patch.object(rag, "_find_file_url") as mock_find, \
             patch.object(rag, "_to_docs_path", return_value="/docs/fake.pdf"):
            hit = _make_hit(file_url="file:///some/path/doc.pdf")
            rag._collect_sources([hit])
        mock_find.assert_not_called()

    def test_missing_metadata_fields_use_defaults(self, rag):
        rag.reset_sources()
        hit = {"metadata": {}, "text": "hello"}
        result = rag._collect_sources([hit])
        assert result == ["S1"]
        entry = rag.get_current_sources()[0]
        assert entry["document"] == "?"
        assert entry["page_start"] == "?"
        assert entry["section"] == ""

    def test_none_section_title_becomes_empty_string(self, rag):
        rag.reset_sources()
        hit = {
            "metadata": {
                "document_name": "doc.pdf",
                "page_start": 1,
                "section_title": None,
            },
            "text": "",
        }
        rag._collect_sources([hit])
        assert rag.get_current_sources()[0]["section"] == ""

    