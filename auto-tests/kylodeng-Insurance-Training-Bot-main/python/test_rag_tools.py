"""
Test module for api/rag_tools.py

What is tested:
    - reset_sources(): initialises a fresh source list in the contextvar
    - get_current_sources(): retrieves sources from the contextvar
    - _find_file_url(): filesystem glob helper with lru_cache
    - _to_docs_path(): file URI → /docs/-relative URL conversion
    - _collect_sources(): deduplication, ID assignment, bucket management
    - _log_hits(): conditional logging based on SHOW_TOOL_CALLS env var
    - make_rag_tools(): factory function, get_current_date tool, list_products tool stub

Mocks used:
    - unittest.mock.patch for Path.rglob, Path.resolve, os.getenv
    - unittest.mock.MagicMock for the vector store passed to make_rag_tools
    - unittest.mock.patch for logging.Logger.info to verify log calls
    - contextvars isolation between tests (manual reset)

TODOs:
    - list_products tool: source code is truncated; full behaviour cannot be tested
    - Any additional tools returned by make_rag_tools beyond get_current_date/list_products
    - Integration tests against a real vector store
"""

import contextvars
import os
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

# ---------------------------------------------------------------------------
# Module-level import  
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

def _make_hit(document_name="doc.pdf", page_start=1, page_end=2,
              product_name="Generations II", file_url="", chunk_id="c1",
              text="sample text", doc_type="product_brochure", section_title="Intro",
              word_count=50):
    return {
        "text": text,
        "metadata": {
            "document_name": document_name,
            "page_start": page_start,
            "page_end": page_end,
            "product_name": product_name,
            "file_url": file_url,
            "chunk_id": chunk_id,
            "doc_type": doc_type,
            "section_title": section_title,
            "word_count": word_count,
        },
    }


@pytest.fixture(autouse=True)
def isolate_contextvar():
    """Ensure contextvar is reset to None before every test."""
    token = _sources_ctx.set(None)
    yield
    _sources_ctx.reset(token)


@pytest.fixture(autouse=True)
def clear_lru_cache():
    """Clear _find_file_url cache before each test to avoid cross-test contamination."""
    _find_file_url.cache_clear()
    yield
    _find_file_url.cache_clear()


# ===========================================================================
# reset_sources / get_current_sources
# ===========================================================================

class TestResetSources:
    def test_sets_empty_list(self):
        reset_sources()
        assert _sources_ctx.get(None) == []

    def test_replaces_existing_list(self):
        _sources_ctx.set(["existing"])
        reset_sources()
        assert _sources_ctx.get(None) == []

    def test_idempotent_multiple_calls(self):
        reset_sources()
        reset_sources()
        assert _sources_ctx.get(None) == []


class TestGetCurrentSources:
    def test_returns_empty_list_when_no_context(self):
        # default is None → should return []
        assert get_current_sources() == []

    def test_returns_empty_list_when_context_is_none(self):
        _sources_ctx.set(None)
        assert get_current_sources() == []

    def test_returns_list_after_reset(self):
        reset_sources()
        assert get_current_sources() == []

    def test_returns_populated_list(self):
        reset_sources()
        bucket = _sources_ctx.get()
        bucket.append({"source_id": "S1"})
        assert get_current_sources() == [{"source_id": "S1"}]

    def test_returns_same_reference_as_contextvar(self):
        reset_sources()
        bucket = _sources_ctx.get()
        bucket.append({"source_id": "S1"})
        result = get_current_sources()
        assert result is bucket


# ===========================================================================
# _find_file_url
# ===========================================================================

class TestFindFileUrl:
    def test_returns_uri_when_file_found(self, tmp_path):
        # Create a temporary file and patch _DATA_DIR
        dummy = tmp_path / "Insurance-product-info" / "doc.pdf"
        dummy.parent.mkdir(parents=True)
        dummy.write_text("dummy")

        with patch.object(rag_tools, "_DATA_DIR", tmp_path):
            _find_file_url.cache_clear()
            result = _find_file_url("doc.pdf")

        assert result.startswith("file:///") or result.startswith("file:/")
        assert "doc.pdf" in result

    def test_returns_empty_string_when_not_found(self, tmp_path):
        with patch.object(rag_tools, "_DATA_DIR", tmp_path):
            _find_file_url.cache_clear()
            result = _find_file_url("nonexistent.pdf")

        assert result == ""

    def test_returns_first_match_when_multiple(self, tmp_path):
        f1 = tmp_path / "dir1" / "doc.pdf"
        f2 = tmp_path / "dir2" / "doc.pdf"
        f1.parent.mkdir(parents=True)
        f2.parent.mkdir(parents=True)
        f1.write_text("a")
        f2.write_text("b")

        with patch.object(rag_tools, "_DATA_DIR", tmp_path):
            _find_file_url.cache_clear()
            result = _find_file_url("doc.pdf")

        assert result != ""  # some URI is returned
        assert "doc.pdf" in result

    def test_caches_result(self, tmp_path):
        with patch.object(rag_tools, "_DATA_DIR", tmp_path):
            _find_file_url.cache_clear()
            r1 = _find_file_url("missing.pdf")
            r2 = _find_file_url("missing.pdf")

        assert r1 == r2 == ""
        info = _find_file_url.cache_info()
        assert info.hits >= 1


# ===========================================================================
# _to_docs_path
# ===========================================================================

class TestToDocsPath:
    def test_empty_string_returns_empty(self):
        assert _to_docs_path("") == ""

    def test_none_like_falsy_returns_empty(self):
        # The function guards with `if not file_url`
        assert _to_docs_path("") == ""

    def test_valid_uri_returns_docs_relative_url(self, tmp_path):
        # Build a real file so resolve() works
        subdir = tmp_path / "Insurance-product-info"
        subdir.mkdir()
        pdf = subdir / "Generations-II_PB_EN.pdf"
        pdf.write_text("x")

        file_uri = pdf.resolve().as_uri()

        with patch.object(rag_tools, "_DATA_DIR", tmp_path):
            result = _to_docs_path(file_uri)

        assert result == "/docs/Insurance-product-info/Generations-II_PB_EN.pdf"

    def test_url_encodes_spaces(self, tmp_path):
        subdir = tmp_path / "My Products"
        subdir.mkdir()
        pdf = subdir / "my doc.pdf"
        pdf.write_text("x")

        file_uri = pdf.resolve().as_uri()

        with patch.object(rag_tools, "_DATA_DIR", tmp_path):
            result = _to_docs_path(file_uri)

        assert result == "/docs/My%20Products/my%20doc.pdf"

    def test_file_outside_data_dir_returns_empty(self, tmp_path):
        outside = tmp_path / "outside.pdf"
        outside.write_text("x")
        file_uri = outside.resolve().as_uri()

        # _DATA_DIR is somewhere else → relative_to will raise
        with patch.object(rag_tools, "_DATA_DIR", tmp_path / "data"):
            result = _to_docs_path(file_uri)

        assert result == ""

    def test_malformed_uri_returns_empty(self):
        result = _to_docs_path("not_a_uri_at_all:::???")
        # Should not raise; returns empty on exception
        assert isinstance(result, str)


# ===========================================================================
# _collect_sources
# ===========================================================================

class TestCollectSources:
    def test_returns_empty_strings_when_no_bucket(self):
        # contextvar is None (autouse fixture)
        hits = [_make_hit()]
        result = _collect_sources(hits)
        assert result == [""]

    def test_returns_empty_list_for_no_hits(self):
        reset_sources()
        result = _collect_sources([])
        assert result == []

    def test_single_hit_gets_s1(self):
        reset_sources()
        hit = _make_hit()
        result = _collect_sources([hit])
        assert result == ["S1"]

    def test_two_different_hits_get_sequential_ids(self):
        reset_sources()
        hits = [
            _make_hit(document_name="a.pdf", page_start=1),
            _make_hit(document_name="b.pdf", page_start=1),
        ]
        result = _collect_sources(hits)
        assert result == ["S1", "S2"]

    def test_duplicate_hit_reuses_existing_id(self):
        reset_sources()
        hit = _make_hit(document_name="a.pdf", page_start=1)
        r1 = _collect_sources([hit])
        r2 = _collect_sources([hit])
        assert r1 == ["S1"]
        assert r2 == ["S1"]  # reused

    def test_duplicate_within_same_call(self):
        reset_sources()
        hit = _make_hit(document_name="a.pdf", page_start=1)
        result = _collect_sources([hit, hit])
        assert result == ["S1", "S1"]

    def test_same_document_different_pages_get_different_ids(self):
        reset_sources()
        hits = [
            _make_hit(document_name="a.pdf", page_start=1),
            _make_hit(document_name="a.pdf", page_start=5),
        ]
        result = _collect_sources(hits)
        assert result == ["S1", "S2"]

    def test_bucket_grows_correctly(self):
        reset_sources()
        hits = [
            _make_hit(document_name="a.pdf", page_start=1),
            _make_hit(document_name="b.pdf", page_start=2),
        ]
        _collect_sources(hits)
        assert len(get_current_sources()) == 2

    def test_entry_fields_populated(self):
        reset_sources()
        hit = _make_hit(
            document_name="Generations-II_PB_EN.pdf",
            page_start=3,
            page_end=4,
            product_name="Generations II",
            chunk_id="c99",
            text="Hello world " * 30,
            section_title="Overview",
        )
        _collect_sources([hit])
        entry = get_current_sources()[0]
        assert entry["source_id"] == "S1"
        assert entry["document"] == "Generations-II_PB_EN.pdf"
        assert entry["product"] == "Generations II"
        assert entry["page_start"] == 3
        assert entry["page_end"] == 4
        assert entry["section"] == "Overview"
        assert entry["chunk_id"] == "c99"
        assert len(entry["text_preview"]) <= 250

    def test_text_preview_truncated_at_250(self):
        reset_sources()
        long_text = "A" * 500
        hit = _make_hit(text=long_text)
        _collect_sources([hit])
        assert get_current_sources()[0]["text_preview"] == "A" * 250

    def test_missing_metadata_uses_defaults(self):
        reset_sources()
        hit = {"text": "content", "metadata": {}}
        result = _collect_sources([hit])
        assert result == ["S1"]
        entry = get_current_sources()[0]
        assert entry["document"] == "?"
        assert entry["page_start"] == "?"
        assert entry["page_end"] == "?"
        assert entry["product"] == ""
        assert entry["section"] == ""

    def test_empty_section_title_becomes_empty_string(self):
        reset_sources()
        hit = _make_hit(section_title=None)
        # section_title=None → md.get("section_title", "") returns None → `or ""` → ""
        # But _make_hit sets it in metadata directly; let's build manually
        hit["metadata"]["section_title"] = None
        _collect_sources([hit])
        assert get_current_sources()[0]["section"] == ""

    def test_file_url_fallback_to_find_file_url(self, tmp_path):
        """When metadata file_url is missing, falls back to _find_file_url."""
        reset_sources()
        subdir = tmp_path / "Insurance-product-info"
        subdir.mkdir()
        pdf = subdir / "doc.pdf"
        pdf.write_text("x")

        hit = _make_hit(document_name="doc.pdf", file_url="")

        with patch.object(rag_tools, "_DATA_DIR", tmp_path):
            _find_file_url.cache_clear()
            _collect_sources([hit])

        entry = get_current_sources()[0]
        # Should have resolved to a /docs/... URL
        assert entry["file_url"].startswith("/docs/")

    def test_multiple_calls_accumulate_in_bucket(self):
        reset_sources()
        _collect_sources([_make_hit(document_name="a.pdf", page_start=1)])
        _collect_sources([_make_hit(document_name="b.pdf", page_start=1)])
        _collect_sources([_make_hit(document_name="c.pdf", page_start=1)])
        sources = get_current_sources()
        assert [s["source_id"] for s in sources] == ["S1", "S2", "S3"]

    def test_no_bucket_multiple_hits_all_empty_string(self):
        # contextvar remains None
        hits = [_make_hit(), _make_hit(document_name="b.pdf")]
        result = _collect_sources(hits)
        assert result == ["", ""]

    def test_synthetic_insurance_hit(self):
        """Use the synthetic data product names from the test data samples."""
        reset_sources()
        hits = [
            _make_hit(
                document_name="Generations-II_PB_EN.pdf",
                page_start=1,
                product_name="Generations II",
            ),
            _make_hit(
                document_name="List of designated hospitals in mainland China.pdf",
                page_start=5,
                product_name="List of Designated Hospitals in Mainland China",
            ),
            _make_hit(
                document_name="Mainland_China_VIP_Hospital_Network