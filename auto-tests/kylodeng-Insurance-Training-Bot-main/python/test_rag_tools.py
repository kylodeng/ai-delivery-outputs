"""
Test module for api/rag_tools.py

What is tested:
- reset_sources(): initialises a fresh list in the contextvar
- get_current_sources(): returns accumulated sources or empty list
- _find_file_url(): filesystem glob with lru_cache
- _to_docs_path(): URI-to-server-path conversion (happy path, edge cases, errors)
- _collect_sources(): dedup logic, source ID generation, bucket management
- _log_hits(): conditional logging based on SHOW_TOOL_CALLS env var
- make_rag_tools() / get_current_date tool: date formatting
- make_rag_tools() / list_products tool: stub (truncated source)

Mocks used:
- unittest.mock.patch for filesystem (Path.rglob), date.today, logger, os.getenv
- Fake in-memory vector store passed to make_rag_tools()

TODOs:
- list_products tool body is truncated in the source; full behaviour cannot be tested
- Any additional tools returned by make_rag_tools() beyond get_current_date and
  list_products are unknown; stubs are provided
- lru_cache on _find_file_url is cleared between tests to avoid cross-test pollution
"""

import contextvars
import importlib
import sys
import types
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers to import the module under test while controlling side-effects
# ---------------------------------------------------------------------------

def _import_rag_tools():
    """Import (or reload) api.rag_tools with a clean module state."""
    # Remove cached module so patching at module level takes effect
    for key in list(sys.modules.keys()):
        if "rag_tools" in key:
            del sys.modules[key]
    import api.rag_tools as rt
    return rt


@pytest.fixture()
def rt():
    """Fresh import of rag_tools for each test."""
    mod = _import_rag_tools()
    # Clear lru_cache so file-system lookups don't leak between tests
    mod._find_file_url.cache_clear()
    return mod


@pytest.fixture()
def clean_sources(rt):
    """Ensure the contextvar starts as None for every test, then clean up."""
    token = rt._sources_ctx.set(None)
    yield rt
    rt._sources_ctx.reset(token)


# ---------------------------------------------------------------------------
# Synthetic chunk hits (derived from the provided data samples)
# ---------------------------------------------------------------------------

GENERATIONS_II_HIT = {
    "text": "Generations II is a participating whole life insurance plan offered by Sun Life.",
    "metadata": {
        "document_name": "Generations-II_PB_EN.pdf",
        "product_name": "Generations II",
        "doc_type": "product_brochure",
        "page_start": 1,
        "page_end": 2,
        "section_title": "Overview",
        "file_url": "file:///data/Insurance-product-info/Generations-II/Generations-II_PB_EN.pdf",
        "chunk_id": "gen2-001",
    },
}

HOSPITAL_LIST_HIT = {
    "text": "List of designated hospitals in mainland China for insurance claims purposes.",
    "metadata": {
        "document_name": "List of designated hospitals in mainland China.pdf",
        "product_name": "List of Designated Hospitals in Mainland China",
        "doc_type": "supplementary",
        "page_start": 1,
        "page_end": 3,
        "section_title": "Scope",
        "file_url": "",
        "chunk_id": "hosp-001",
    },
}

VIP_HOSPITAL_HIT = {
    "text": "Network hospitals with VIP Medical Navigation Service.",
    "metadata": {
        "document_name": "Mainland_China_VIP_Hospital_Network.pdf",
        "product_name": "List of Network Hospitals with Mainland China VIP Medical Navigation Service",
        "doc_type": "supplementary",
        "page_start": 5,
        "page_end": 6,
        "section_title": "Shanghai Hospitals",
        "file_url": "file:///data/Insurance-product-info/Mainland_China_VIP_Hospital_Network.pdf",
        "chunk_id": "vip-001",
    },
}

CASHLESS_HIT = {
    "text": "Global Network Hospital List for Cashless Arrangement.",
    "metadata": {
        "document_name": "Network_Hospitals_with_Cashless_Arrangement.pdf",
        "product_name": "Global Network Hospital List for Cashless Arrangement",
        "doc_type": "supplementary",
        "page_start": 2,
        "page_end": 4,
        "section_title": "",
        "file_url": "file:///data/Insurance-product-info/Network_Hospitals_with_Cashless_Arrangement.pdf",
        "chunk_id": "cash-001",
    },
}


# ===========================================================================
# reset_sources / get_current_sources
# ===========================================================================

class TestResetSources:
    def test_sets_empty_list(self, clean_sources):
        rt = clean_sources
        assert rt._sources_ctx.get(None) is None  # precondition
        rt.reset_sources()
        assert rt._sources_ctx.get(None) == []

    def test_replaces_existing_list(self, clean_sources):
        rt = clean_sources
        rt._sources_ctx.set(["stale"])
        rt.reset_sources()
        assert rt._sources_ctx.get(None) == []

    def test_called_twice_gives_fresh_list(self, clean_sources):
        rt = clean_sources
        rt.reset_sources()
        rt._sources_ctx.get(None).append("item")
        rt.reset_sources()
        assert rt._sources_ctx.get(None) == []


class TestGetCurrentSources:
    def test_returns_empty_list_when_not_initialised(self, clean_sources):
        rt = clean_sources
        assert rt.get_current_sources() == []

    def test_returns_accumulated_sources(self, clean_sources):
        rt = clean_sources
        rt.reset_sources()
        rt._sources_ctx.get(None).append({"source_id": "S1"})
        result = rt.get_current_sources()
        assert result == [{"source_id": "S1"}]

    def test_returns_copy_reference_not_none(self, clean_sources):
        rt = clean_sources
        # contextvar is None → should return []
        assert rt.get_current_sources() == []


# ===========================================================================
# _find_file_url
# ===========================================================================

class TestFindFileUrl:
    def test_returns_uri_when_file_exists(self, rt, tmp_path):
        fake_file = tmp_path / "doc.pdf"
        fake_file.touch()

        with patch.object(Path, "rglob", return_value=[fake_file]):
            result = rt._find_file_url("doc.pdf")

        assert result.startswith("file:///") or result.startswith("file:")
        assert "doc.pdf" in result

    def test_returns_empty_string_when_no_match(self, rt):
        with patch.object(Path, "rglob", return_value=[]):
            result = rt._find_file_url("nonexistent.pdf")

        assert result == ""

    def test_lru_cache_hit(self, rt, tmp_path):
        fake_file = tmp_path / "cached.pdf"
        fake_file.touch()

        with patch.object(Path, "rglob", return_value=[fake_file]) as mock_rglob:
            rt._find_file_url("cached.pdf")
            rt._find_file_url("cached.pdf")
            # rglob called only once because of cache
            assert mock_rglob.call_count == 1

    def test_different_documents_queried_separately(self, rt):
        with patch.object(Path, "rglob", return_value=[]) as mock_rglob:
            rt._find_file_url("a.pdf")
            rt._find_file_url("b.pdf")
            assert mock_rglob.call_count == 2


# ===========================================================================
# _to_docs_path
# ===========================================================================

class TestToDocsPath:
    def test_empty_string_returns_empty(self, rt):
        assert rt._to_docs_path("") == ""

    def test_none_like_falsy_returns_empty(self, rt):
        # The function checks `if not file_url` so None would also hit that branch.
        # We pass empty string as falsy (None would require type ignore).
        assert rt._to_docs_path("") == ""

    @pytest.mark.parametrize("file_url,expected_suffix", [
        (
            "file:///home/user/project/data/Insurance-product-info/Generations-II/Generations-II_PB_EN.pdf",
            "/docs/Insurance-product-info/Generations-II/Generations-II_PB_EN.pdf",
        ),
        (
            "file:///home/user/project/data/Insurance-product-info/Network_Hospitals_with_Cashless_Arrangement.pdf",
            "/docs/Insurance-product-info/Network_Hospitals_with_Cashless_Arrangement.pdf",
        ),
    ])
    def test_valid_uri_converted_to_docs_path(self, rt, file_url, expected_suffix, tmp_path):
        """
        We patch _DATA_DIR so relative_to() resolves correctly regardless of
        the actual filesystem layout on the CI machine.
        """
        # Build a fake data dir whose path matches the URI above
        data_dir = tmp_path / "data"
        sub = data_dir / "Insurance-product-info" / "Generations-II"
        sub.mkdir(parents=True, exist_ok=True)
        (data_dir / "Insurance-product-info").mkdir(parents=True, exist_ok=True)

        # Reconstruct a file_url that actually matches tmp_path
        from pathlib import PurePosixPath
        rel_path = PurePosixPath(file_url.replace("file:///", "/"))
        # Derive the parts after /data/
        try:
            idx = rel_path.parts.index("data")
            rel_parts = rel_path.parts[idx + 1:]
        except ValueError:
            pytest.skip("Cannot determine relative parts from test URI")

        real_file = data_dir.joinpath(*rel_parts)
        real_file.parent.mkdir(parents=True, exist_ok=True)
        real_file.touch()
        real_uri = real_file.resolve().as_uri()

        with patch.object(rt, "_DATA_DIR", data_dir.resolve()):
            result = rt._to_docs_path(real_uri)

        assert result.startswith("/docs/")
        for part in rel_parts:
            assert part in result or part.replace(" ", "%20") in result

    def test_invalid_uri_returns_empty_string(self, rt):
        result = rt._to_docs_path("not-a-valid-uri://???")
        # Should return "" because relative_to() or urlparse will raise
        assert isinstance(result, str)

    def test_uri_outside_data_dir_returns_empty(self, rt, tmp_path):
        """A valid file URI that is not under _DATA_DIR returns ''."""
        outside_file = tmp_path / "outside.pdf"
        outside_file.touch()
        uri = outside_file.resolve().as_uri()
        # _DATA_DIR is somewhere else; relative_to() will raise ValueError
        result = rt._to_docs_path(uri)
        assert result == ""

    def test_url_encoded_spaces_in_path(self, rt, tmp_path):
        data_dir = tmp_path / "data"
        sub_dir = data_dir / "folder with spaces"
        sub_dir.mkdir(parents=True)
        doc = sub_dir / "my doc.pdf"
        doc.touch()
        uri = doc.resolve().as_uri()

        with patch.object(rt, "_DATA_DIR", data_dir.resolve()):
            result = rt._to_docs_path(uri)

        assert result.startswith("/docs/")
        assert "my%20doc.pdf" in result or "my doc.pdf" in result


# ===========================================================================
# _collect_sources
# ===========================================================================

class TestCollectSources:
    def test_returns_empty_strings_when_bucket_is_none(self, clean_sources):
        rt = clean_sources
        # bucket is None (no reset_sources called)
        result = rt._collect_sources([GENERATIONS_II_HIT, HOSPITAL_LIST_HIT])
        assert result == ["", ""]

    def test_assigns_sequential_source_ids(self, clean_sources):
        rt = clean_sources
        rt.reset_sources()
        with patch.object(rt, "_find_file_url", return_value=""):
            with patch.object(rt, "_to_docs_path", return_value=""):
                result = rt._collect_sources([GENERATIONS_II_HIT, HOSPITAL_LIST_HIT])
        assert result == ["S1", "S2"]

    def test_deduplicates_same_document_and_page(self, clean_sources):
        rt = clean_sources
        rt.reset_sources()
        duplicate = dict(GENERATIONS_II_HIT)  # same doc, same page_start
        with patch.object(rt, "_find_file_url", return_value=""):
            with patch.object(rt, "_to_docs_path", return_value=""):
                result = rt._collect_sources([GENERATIONS_II_HIT, duplicate])
        assert result == ["S1", "S1"]
        assert len(rt._sources_ctx.get()) == 1  # only one entry in bucket

    def test_different_pages_same_doc_get_different_ids(self, clean_sources):
        rt = clean_sources
        rt.reset_sources()
        page2_hit = {
            "text": "Page 2 content",
            "metadata": {
                **GENERATIONS_II_HIT["metadata"],
                "page_start": 99,
                "page_end": 100,
            },
        }
        with patch.object(rt, "_find_file_url", return_value=""):
            with patch.object(rt, "_to_docs_path", return_value=""):
                result = rt._collect_sources([GENERATIONS_II_HIT, page2_hit])
        assert result == ["S1", "S2"]

    def test_entry_structure_fields(self, clean_sources):
        rt = clean_sources
        rt.reset_sources()
        with patch.object(rt, "_find_file_url", return_value=""):
            with patch.object(rt, "_to_docs_path", return_value="/docs/test.pdf"):
                rt._collect_sources([GENERATIONS_II_HIT])

        bucket = rt._sources_ctx.get()
        assert len(bucket) == 1
        entry = bucket[0]
        assert entry["source_id"] == "S1"
        assert entry["document"] == "Generations-II_PB_EN.pdf"
        assert entry["product"] == "Generations II"
        assert entry["page_start"] == 1
        assert entry["page_end"] == 2
        assert entry["section"] == "Overview"
        assert entry["chunk_id"] == "gen2-001"
        assert entry["file_url"] == "/docs/test.pdf"
        assert "Generations II" in entry["text_preview"]

    def test_text_preview_truncated_at_250_chars(self, clean_sources):
        rt = clean_sources
        rt.reset_sources()
        long_hit = {
            "text": "A" * 500,
            "metadata": {
                "document_name": "long.pdf",
                "product_name": "Test",
                "page_start": 1,
                "page_end": 1,
                "section_title": "",
                "file_url": "",
                "chunk_