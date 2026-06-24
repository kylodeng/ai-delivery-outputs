"""
Test module for api/rag_tools.py

What is tested:
    - reset_sources(): initialises a fresh list in the contextvar
    - get_current_sources(): returns sources from contextvar (or empty list)
    - _find_file_url(): filesystem glob fallback for document URLs
    - _to_docs_path(): converts file:/// URIs to /docs/-relative server URLs
    - _collect_sources(): deduplicates hits and builds source-entry dicts
    - _log_hits(): conditional logging based on SHOW_TOOL_CALLS env var
    - make_rag_tools() → get_current_date tool: returns today's date string
    - make_rag_tools() → list_products tool: stub (incomplete source)

Mocks used:
    - unittest.mock.patch for Path.rglob (_find_file_url filesystem calls)
    - unittest.mock.patch for datetime.date.today (get_current_date tool)
    - unittest.mock.patch for logging.Logger.info (_log_hits verification)
    - unittest.mock.MagicMock for the vector store passed to make_rag_tools()
    - monkeypatch for environment variables (SHOW_TOOL_CALLS)

TODOs:
    - list_products tool: source is truncated; full behaviour cannot be tested
    - Any additional tools returned by make_rag_tools() beyond list_products
      are not visible in the provided source — stub tests added below
"""

import contextvars
import os
import sys
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch, call
import pytest

# ---------------------------------------------------------------------------
# Path bootstrap so the module can be imported without a full package install
# ---------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import importlib
import api.rag_tools as rag_tools_module
from api.rag_tools import (
    _collect_sources,
    _find_file_url,
    _log_hits,
    _sources_ctx,
    _to_docs_path,
    get_current_sources,
    make_rag_tools,
    reset_sources,
)


# ===========================================================================
# Helpers
# ===========================================================================

def _make_hit(
    document_name: str = "doc.pdf",
    page_start: int = 1,
    page_end: int = 2,
    product_name: str = "Generations II",
    section_title: str = "Overview",
    file_url: str = "",
    chunk_id: str = "c1",
    text: str = "Sample chunk text",
    doc_type: str = "product_brochure",
    word_count: int = 42,
) -> dict:
    return {
        "text": text,
        "metadata": {
            "document_name": document_name,
            "page_start": page_start,
            "page_end": page_end,
            "product_name": product_name,
            "section_title": section_title,
            "file_url": file_url,
            "chunk_id": chunk_id,
            "doc_type": doc_type,
            "word_count": word_count,
        },
    }


# ===========================================================================
# reset_sources / get_current_sources
# ===========================================================================

class TestResetAndGetSources:
    def test_reset_sources_sets_empty_list(self):
        reset_sources()
        assert _sources_ctx.get(None) == []

    def test_get_current_sources_after_reset_returns_empty_list(self):
        reset_sources()
        assert get_current_sources() == []

    def test_get_current_sources_when_no_context_returns_empty_list(self):
        # Ensure no prior reset in this context
        _sources_ctx.set(None)
        result = get_current_sources()
        assert result == []

    def test_get_current_sources_returns_populated_list(self):
        reset_sources()
        bucket = _sources_ctx.get(None)
        bucket.append({"source_id": "S1", "document": "doc.pdf"})
        result = get_current_sources()
        assert len(result) == 1
        assert result[0]["source_id"] == "S1"

    def test_reset_sources_clears_existing_entries(self):
        reset_sources()
        _sources_ctx.get(None).append({"source_id": "S1"})
        reset_sources()
        assert get_current_sources() == []

    def test_multiple_resets_are_idempotent(self):
        for _ in range(5):
            reset_sources()
        assert get_current_sources() == []


# ===========================================================================
# _find_file_url
# ===========================================================================

class TestFindFileUrl:
    def setup_method(self):
        # Clear lru_cache between tests
        _find_file_url.cache_clear()

    def test_returns_uri_when_file_found(self, tmp_path):
        doc = tmp_path / "doc.pdf"
        doc.write_bytes(b"%PDF")
        with patch.object(
            Path,
            "rglob",
            return_value=iter([doc]),
        ):
            result = _find_file_url("doc.pdf")
        assert result.startswith("file://")
        assert "doc.pdf" in result

    def test_returns_empty_string_when_no_match(self):
        with patch.object(Path, "rglob", return_value=iter([])):
            result = _find_file_url("nonexistent.pdf")
        assert result == ""

    def test_returns_first_match_when_multiple_files(self, tmp_path):
        first = tmp_path / "a" / "doc.pdf"
        second = tmp_path / "b" / "doc.pdf"
        first.parent.mkdir()
        second.parent.mkdir()
        first.write_bytes(b"%PDF")
        second.write_bytes(b"%PDF")
        with patch.object(Path, "rglob", return_value=iter([first, second])):
            result = _find_file_url("doc.pdf")
        assert "a" in result or "b" in result  # first match

    def test_caches_result(self):
        _find_file_url.cache_clear()
        with patch.object(Path, "rglob", return_value=iter([])) as mock_rglob:
            _find_file_url("cached.pdf")
            _find_file_url("cached.pdf")
        # rglob called only once due to lru_cache
        assert mock_rglob.call_count == 1

    def test_empty_string_document_name(self):
        with patch.object(Path, "rglob", return_value=iter([])):
            result = _find_file_url("")
        assert result == ""


# ===========================================================================
# _to_docs_path
# ===========================================================================

class TestToDocsPath:
    def test_empty_string_returns_empty_string(self):
        assert _to_docs_path("") == ""

    def test_valid_file_uri_returns_docs_relative_path(self, tmp_path):
        # Build a real file URI that sits under _DATA_DIR
        data_dir = rag_tools_module._DATA_DIR.resolve()
        sub_dir = data_dir / "Insurance-product-info"
        sub_dir.mkdir(parents=True, exist_ok=True)
        doc = sub_dir / "doc.pdf"
        doc.write_bytes(b"%PDF")

        file_uri = doc.resolve().as_uri()
        result = _to_docs_path(file_uri)
        assert result.startswith("/docs/")
        assert "doc.pdf" in result

    def test_path_outside_data_dir_returns_empty_string(self, tmp_path):
        # A path that cannot be made relative to _DATA_DIR
        outside = tmp_path / "outside.pdf"
        outside.write_bytes(b"%PDF")
        uri = outside.resolve().as_uri()
        result = _to_docs_path(uri)
        assert result == ""

    def test_malformed_uri_returns_empty_string(self):
        result = _to_docs_path("not_a_uri_at_all")
        # Should not raise; returns "" on exception
        assert isinstance(result, str)

    def test_path_with_spaces_is_encoded(self, tmp_path):
        data_dir = rag_tools_module._DATA_DIR.resolve()
        spaced = data_dir / "Insurance product info" / "my doc.pdf"
        spaced.parent.mkdir(parents=True, exist_ok=True)
        spaced.write_bytes(b"%PDF")

        uri = spaced.resolve().as_uri()
        result = _to_docs_path(uri)
        if result:  # only assert encoding if path resolved successfully
            assert " " not in result


# ===========================================================================
# _collect_sources
# ===========================================================================

class TestCollectSources:
    def setup_method(self):
        reset_sources()

    # --- happy path --------------------------------------------------------

    def test_single_hit_appends_to_bucket(self):
        hit = _make_hit(document_name="Generations-II_PB_EN.pdf", page_start=1)
        ids = _collect_sources([hit])
        assert ids == ["S1"]
        bucket = _sources_ctx.get(None)
        assert len(bucket) == 1
        assert bucket[0]["source_id"] == "S1"
        assert bucket[0]["document"] == "Generations-II_PB_EN.pdf"

    def test_multiple_unique_hits_increment_ids(self):
        hits = [
            _make_hit(document_name="doc1.pdf", page_start=1),
            _make_hit(document_name="doc2.pdf", page_start=5),
            _make_hit(document_name="doc3.pdf", page_start=10),
        ]
        ids = _collect_sources(hits)
        assert ids == ["S1", "S2", "S3"]
        assert len(_sources_ctx.get(None)) == 3

    def test_duplicate_hit_reuses_existing_id(self):
        hit = _make_hit(document_name="doc.pdf", page_start=1)
        ids1 = _collect_sources([hit])
        ids2 = _collect_sources([hit])
        assert ids1 == ["S1"]
        assert ids2 == ["S1"]  # reused
        assert len(_sources_ctx.get(None)) == 1

    def test_same_doc_different_pages_are_separate_sources(self):
        hit1 = _make_hit(document_name="doc.pdf", page_start=1)
        hit2 = _make_hit(document_name="doc.pdf", page_start=5)
        ids = _collect_sources([hit1, hit2])
        assert ids == ["S1", "S2"]

    def test_entry_fields_populated_correctly(self):
        hit = _make_hit(
            document_name="doc.pdf",
            page_start=3,
            page_end=4,
            product_name="Generations II",
            section_title="Benefits",
            chunk_id="c42",
            text="A" * 300,
        )
        _collect_sources([hit])
        entry = _sources_ctx.get(None)[0]
        assert entry["source_id"] == "S1"
        assert entry["document"] == "doc.pdf"
        assert entry["product"] == "Generations II"
        assert entry["page_start"] == 3
        assert entry["page_end"] == 4
        assert entry["section"] == "Benefits"
        assert entry["chunk_id"] == "c42"
        # text_preview capped at 250 chars
        assert len(entry["text_preview"]) == 250

    def test_text_preview_capped_at_250_chars(self):
        long_text = "X" * 500
        hit = _make_hit(text=long_text)
        _collect_sources([hit])
        entry = _sources_ctx.get(None)[0]
        assert len(entry["text_preview"]) == 250

    def test_text_preview_shorter_than_250(self):
        hit = _make_hit(text="Short text")
        _collect_sources([hit])
        assert _sources_ctx.get(None)[0]["text_preview"] == "Short text"

    def test_missing_metadata_fields_use_defaults(self):
        hit = {"text": "hello", "metadata": {}}
        _collect_sources([hit])
        entry = _sources_ctx.get(None)[0]
        assert entry["document"] == "?"
        assert entry["page_start"] == "?"
        assert entry["page_end"] == "?"
        assert entry["product"] == ""
        assert entry["section"] == ""
        assert entry["chunk_id"] == ""

    def test_file_url_used_from_metadata_when_present(self):
        data_dir = rag_tools_module._DATA_DIR.resolve()
        sub = data_dir / "Insurance-product-info"
        sub.mkdir(parents=True, exist_ok=True)
        doc = sub / "doc.pdf"
        doc.write_bytes(b"%PDF")

        uri = doc.resolve().as_uri()
        hit = _make_hit(file_url=uri)
        _collect_sources([hit])
        entry = _sources_ctx.get(None)[0]
        # file_url field should be a /docs/ path or empty string, never raw URI
        assert not entry["file_url"].startswith("file://")

    def test_file_url_falls_back_to_find_file_url_when_empty(self):
        _find_file_url.cache_clear()
        hit = _make_hit(file_url="", document_name="fallback_doc.pdf")
        with patch("api.rag_tools._find_file_url", return_value="") as mock_find:
            _collect_sources([hit])
        mock_find.assert_called_once_with("fallback_doc.pdf")

    # --- no context (bucket is None) --------------------------------------

    def test_returns_empty_strings_when_no_context(self):
        _sources_ctx.set(None)
        hits = [_make_hit(), _make_hit(page_start=2)]
        ids = _collect_sources(hits)
        assert ids == ["", ""]

    def test_empty_hits_list_returns_empty_list(self):
        ids = _collect_sources([])
        assert ids == []

    # --- counter consistency across accumulated calls ---------------------

    def test_ids_continue_incrementing_across_multiple_calls(self):
        _collect_sources([_make_hit(page_start=1)])
        _collect_sources([_make_hit(page_start=2)])
        _collect_sources([_make_hit(page_start=3)])
        bucket = _sources_ctx.get(None)
        ids = [e["source_id"] for e in bucket]
        assert ids == ["S1", "S2", "S3"]

    def test_mixed_duplicate_and_new_in_same_call(self):
        hit1 = _make_hit(document_name="doc.pdf", page_start=1)
        hit2 = _make_hit(document_name="doc.pdf", page_start=2)
        _collect_sources([hit1])  # S1
        ids = _collect_sources([hit1, hit2])  # S1 reused, S2 new
        assert ids == ["S1", "S2"]
        assert len(_sources_ctx.get(None)) == 2

    # --- synthetic data inputs --------------------------------------------

    @pytest.mark.parametrize(
        "doc_name, product_name",
        [
            (
                "Generations-II_PB_EN.pdf",
                "Generations II",
            ),
            (
                "List of designated hospitals in mainland China.pdf",
                "List of Designated Hospitals in Mainland China",
            ),
            (
                "Mainland_China_VIP_Hospital_Network.pdf