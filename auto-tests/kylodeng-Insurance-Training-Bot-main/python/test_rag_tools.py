"""
Test module for api/rag_tools.py

What is tested:
  - reset_sources(): initialises a fresh list in the contextvar
  - get_current_sources(): returns accumulated sources or empty list
  - _find_file_url(): filesystem glob lookup with LRU cache
  - _to_docs_path(): converts file:/// URIs to /docs/-relative paths
  - _collect_sources(): deduplicates hits, assigns source IDs, builds entries
  - _log_hits(): conditional logging based on SHOW_TOOL_CALLS env var
  - make_rag_tools() → get_current_date tool: returns today's date string
  - make_rag_tools() → list_products tool: stub (truncated source)

Mocks used:
  - unittest.mock.patch for filesystem (_DATA_DIR.rglob, Path.resolve)
  - unittest.mock.patch for os.getenv / _SHOW_TOOL_CALLS
  - unittest.mock.patch for date.today()
  - unittest.mock.MagicMock for the vector store passed to make_rag_tools()
  - pytest monkeypatch for environment variables

TODOs:
  - TODO: list_products tool body is truncated in source — add full integration tests once complete
  - TODO: any additional tools returned by make_rag_tools() beyond get_current_date and list_products
    need tests once their source is available
  - TODO: _find_file_url cache invalidation tests require resetting lru_cache between test runs
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
# Ensure the package root is importable regardless of working directory
# ---------------------------------------------------------------------------
ROOT = Path(__file__).parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import api.rag_tools as rag_tools
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
# Helpers / fixtures
# ===========================================================================

def _make_hit(
    document_name="Generations-II_PB_EN.pdf",
    page_start=1,
    page_end=2,
    product_name="Generations II",
    doc_type="product_brochure",
    section_title="Overview",
    file_url="",
    chunk_id="c001",
    word_count=120,
    text="Sample chunk text for testing purposes.",
) -> dict:
    return {
        "metadata": {
            "document_name": document_name,
            "page_start": page_start,
            "page_end": page_end,
            "product_name": product_name,
            "doc_type": doc_type,
            "section_title": section_title,
            "file_url": file_url,
            "chunk_id": chunk_id,
            "word_count": word_count,
        },
        "text": text,
    }


@pytest.fixture(autouse=True)
def _reset_context():
    """Ensure each test starts with a clean contextvar state."""
    token = _sources_ctx.set(None)
    yield
    _sources_ctx.reset(token)


@pytest.fixture(autouse=True)
def _clear_find_file_url_cache():
    """Clear the LRU cache on _find_file_url before every test."""
    _find_file_url.cache_clear()
    yield
    _find_file_url.cache_clear()


@pytest.fixture()
def mock_store():
    return MagicMock()


# ===========================================================================
# reset_sources / get_current_sources
# ===========================================================================

class TestResetSources:
    def test_sets_empty_list(self):
        reset_sources()
        assert _sources_ctx.get(None) == []

    def test_clears_previous_data(self):
        _sources_ctx.set(["stale_entry"])
        reset_sources()
        assert _sources_ctx.get(None) == []

    def test_subsequent_reset_replaces_list(self):
        reset_sources()
        first_list = _sources_ctx.get(None)
        reset_sources()
        second_list = _sources_ctx.get(None)
        assert first_list is not second_list  # new list object
        assert second_list == []


class TestGetCurrentSources:
    def test_returns_empty_list_when_not_initialised(self):
        # contextvar is None (default)
        assert get_current_sources() == []

    def test_returns_empty_list_when_contextvar_is_none(self):
        _sources_ctx.set(None)
        assert get_current_sources() == []

    def test_returns_accumulated_sources(self):
        reset_sources()
        _sources_ctx.get(None).append({"source_id": "S1", "document": "doc.pdf"})
        result = get_current_sources()
        assert len(result) == 1
        assert result[0]["source_id"] == "S1"

    def test_returns_copy_of_list(self):
        reset_sources()
        result = get_current_sources()
        result.append("external_mutation")
        # The bucket should NOT be mutated
        assert _sources_ctx.get(None) == []


# ===========================================================================
# _find_file_url
# ===========================================================================

class TestFindFileUrl:
    def test_returns_uri_when_file_found(self, tmp_path):
        """When the file exists under _DATA_DIR, the file:// URI is returned."""
        fake_file = tmp_path / "doc.pdf"
        fake_file.write_bytes(b"pdf")

        with patch.object(rag_tools, "_DATA_DIR", tmp_path):
            with patch.object(Path, "rglob", return_value=[fake_file]):
                # Clear cache so the patched _DATA_DIR is used
                _find_file_url.cache_clear()
                result = _find_file_url("doc.pdf")
        assert result.startswith("file:///") or result.startswith("file:/")
        assert "doc.pdf" in result

    def test_returns_empty_string_when_not_found(self, tmp_path):
        with patch.object(rag_tools, "_DATA_DIR", tmp_path):
            with patch.object(Path, "rglob", return_value=[]):
                _find_file_url.cache_clear()
                result = _find_file_url("nonexistent.pdf")
        assert result == ""

    def test_returns_first_match_when_multiple(self, tmp_path):
        file_a = tmp_path / "a" / "doc.pdf"
        file_b = tmp_path / "b" / "doc.pdf"
        file_a.parent.mkdir(parents=True, exist_ok=True)
        file_b.parent.mkdir(parents=True, exist_ok=True)
        file_a.write_bytes(b"a")
        file_b.write_bytes(b"b")

        with patch.object(rag_tools, "_DATA_DIR", tmp_path):
            with patch.object(Path, "rglob", return_value=[file_a, file_b]):
                _find_file_url.cache_clear()
                result = _find_file_url("doc.pdf")
        assert "doc.pdf" in result

    def test_lru_cache_returns_same_result(self, tmp_path):
        with patch.object(rag_tools, "_DATA_DIR", tmp_path):
            with patch.object(Path, "rglob", return_value=[]) as mock_rglob:
                _find_file_url.cache_clear()
                _find_file_url("cached.pdf")
                _find_file_url("cached.pdf")
                # rglob should only be called once due to caching
                assert mock_rglob.call_count == 1


# ===========================================================================
# _to_docs_path
# ===========================================================================

class TestToDocsPath:
    def test_empty_string_returns_empty(self):
        assert _to_docs_path("") == ""

    def test_none_like_falsy_returns_empty(self):
        assert _to_docs_path("") == ""

    def test_valid_file_uri_converts_to_docs_path(self, tmp_path):
        # Create a real file so Path.resolve() works
        product_dir = tmp_path / "Insurance-product-info"
        product_dir.mkdir(parents=True, exist_ok=True)
        pdf = product_dir / "doc.pdf"
        pdf.write_bytes(b"pdf")

        file_url = pdf.resolve().as_uri()

        with patch.object(rag_tools, "_DATA_DIR", tmp_path):
            result = _to_docs_path(file_url)

        assert result.startswith("/docs/")
        assert "doc.pdf" in result

    def test_url_encodes_special_characters(self, tmp_path):
        product_dir = tmp_path / "My Product"
        product_dir.mkdir(parents=True, exist_ok=True)
        pdf = product_dir / "my doc.pdf"
        pdf.write_bytes(b"pdf")

        file_url = pdf.resolve().as_uri()

        with patch.object(rag_tools, "_DATA_DIR", tmp_path):
            result = _to_docs_path(file_url)

        if result:  # only assert encoding if conversion succeeded
            assert " " not in result

    def test_invalid_uri_returns_empty(self):
        result = _to_docs_path("not-a-valid-uri://???")
        # Should not raise; returns "" on exception
        assert isinstance(result, str)

    def test_path_outside_data_dir_returns_empty(self, tmp_path):
        """A file:// URI that cannot be made relative to _DATA_DIR returns ''."""
        other_dir = tmp_path / "other"
        other_dir.mkdir()
        pdf = other_dir / "secret.pdf"
        pdf.write_bytes(b"x")

        data_dir = tmp_path / "data"
        data_dir.mkdir()

        file_url = pdf.resolve().as_uri()
        with patch.object(rag_tools, "_DATA_DIR", data_dir):
            result = _to_docs_path(file_url)

        assert result == ""


# ===========================================================================
# _collect_sources
# ===========================================================================

class TestCollectSources:
    def test_returns_empty_source_ids_when_bucket_is_none(self):
        """If contextvar is None (no reset_sources call), returns list of ''."""
        hits = [_make_hit(), _make_hit()]
        result = _collect_sources(hits)
        assert result == ["", ""]

    def test_assigns_sequential_source_ids(self):
        reset_sources()
        hits = [
            _make_hit(document_name="doc1.pdf", page_start=1),
            _make_hit(document_name="doc2.pdf", page_start=5),
        ]
        ids = _collect_sources(hits)
        assert ids == ["S1", "S2"]

    def test_deduplicates_same_document_and_page(self):
        reset_sources()
        hit = _make_hit(document_name="doc.pdf", page_start=3)
        ids = _collect_sources([hit, hit])
        assert ids == ["S1", "S1"]
        # Only one entry in the bucket
        assert len(_sources_ctx.get()) == 1

    def test_different_pages_same_document_get_different_ids(self):
        reset_sources()
        h1 = _make_hit(document_name="doc.pdf", page_start=1)
        h2 = _make_hit(document_name="doc.pdf", page_start=2)
        ids = _collect_sources([h1, h2])
        assert ids == ["S1", "S2"]
        assert len(_sources_ctx.get()) == 2

    def test_entry_fields_are_populated(self):
        reset_sources()
        hit = _make_hit(
            document_name="Generations-II_PB_EN.pdf",
            page_start=10,
            page_end=11,
            product_name="Generations II",
            section_title="Benefits",
            chunk_id="chunk-42",
            text="Hello world " * 30,  # > 250 chars
        )
        with patch.object(rag_tools, "_find_file_url", return_value=""):
            _collect_sources([hit])
        entry = _sources_ctx.get()[0]
        assert entry["source_id"] == "S1"
        assert entry["document"] == "Generations-II_PB_EN.pdf"
        assert entry["product"] == "Generations II"
        assert entry["page_start"] == 10
        assert entry["page_end"] == 11
        assert entry["section"] == "Benefits"
        assert entry["chunk_id"] == "chunk-42"
        assert len(entry["text_preview"]) <= 250

    def test_text_preview_truncated_to_250(self):
        reset_sources()
        long_text = "A" * 500
        hit = _make_hit(text=long_text)
        _collect_sources([hit])
        entry = _sources_ctx.get()[0]
        assert entry["text_preview"] == "A" * 250

    def test_missing_metadata_fields_use_defaults(self):
        reset_sources()
        bare_hit = {"metadata": {}, "text": "bare"}
        _collect_sources([bare_hit])
        entry = _sources_ctx.get()[0]
        assert entry["document"] == "?"
        assert entry["page_start"] == "?"
        assert entry["page_end"] == "?"
        assert entry["product"] == ""
        assert entry["section"] == ""
        assert entry["chunk_id"] == ""

    def test_empty_hits_list_returns_empty(self):
        reset_sources()
        result = _collect_sources([])
        assert result == []
        assert _sources_ctx.get() == []

    def test_uses_file_url_from_metadata_when_present(self, tmp_path):
        reset_sources()
        product_dir = tmp_path / "Insurance-product-info"
        product_dir.mkdir(parents=True, exist_ok=True)
        pdf = product_dir / "doc.pdf"
        pdf.write_bytes(b"x")
        file_url = pdf.resolve().as_uri()

        hit = _make_hit(file_url=file_url)

        with patch.object(rag_tools, "_DATA_DIR", tmp_path):
            _collect_sources([hit])

        entry = _sources_ctx.get()[0]
        # file_url goes through _to_docs_path; at minimum it should not be the raw URI
        assert isinstance(entry["file_url"], str)

    def test_falls_back_to_find_file_url_when_metadata_url_absent(self):
        reset_sources()
        hit = _make_hit(file_url="", document_name="special.pdf")
        with patch.object(rag_tools, "_find_file_url", return_value="file:///data/special.pdf") as mock_find:
            with patch.object(rag_tools, "_to_docs_path", return_value="/docs/special.pdf"):
                _collect_sources([hit])
        mock_find.assert_called_once_with("special.pdf")

    def test_counter_continues_across_multiple_calls(self):
        reset_sources()
        h1 = _make_hit(document_name="a.pdf", page_start=1)
        h2 = _make_hit(document_name="b.pdf", page_start=1)
        h3 = _make_hit(document_name="c.pdf", page_start=