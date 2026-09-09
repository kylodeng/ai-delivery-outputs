"""
Test suite for .github/scripts/shared.py

What is tested:
- call_claude(): Claude API integration returning text response
- clean_json(): Markdown code-fence stripping from JSON strings
- get_repo_files(): GitHub API tree traversal and file content fetching with extension filtering
- get_pr_diff(): GitHub PR unified diff retrieval
- write_output_file(): File creation/update in output repo (with and without existing SHA)
- post_pr_comment(): Posting review comments on PRs
- send_email(): SendGrid email delivery with success/failure handling
- email_html(): HTML email template generation
- write_audit_entry(): Audit log writing (JSON + Markdown)

Mocks used:
- unittest.mock.patch / MagicMock for:
  - requests.get, requests.post, requests.put (all external HTTP calls)
  - anthropic.Anthropic client and messages.create
  - base64 operations (verified via real calls since stdlib)
  - datetime.datetime (for deterministic timestamp assertions)
  - os.environ (patched at import time via monkeypatch)

TODOs:
- TODO: Integration test for actual Claude model response shape when API key is available
- TODO: Test write_audit_entry() fully — source code is truncated and the full implementation is unknown
- TODO: Test GH_HEADERS propagation when GH_TOKEN changes at runtime
- TODO: Verify behaviour when OUTPUT_REPO_OWNER defaults from GITHUB_REPOSITORY_OWNER
"""

import base64
import json
import sys
import types
import datetime
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Environment bootstrap — must happen BEFORE importing shared.py
# ---------------------------------------------------------------------------
ENV_DEFAULTS = {
    "ANTHROPIC_API_KEY": "test-anthropic-key",
    "GH_TOKEN": "test-gh-token",
    "SENDGRID_API_KEY": "test-sendgrid-key",
    "OUTPUT_REPO": "ai-delivery-outputs",
    "OUTPUT_REPO_OWNER": "test-owner",
    "NOTIFY_EMAIL": "notify@example.com",
    "SENDER_EMAIL": "sender@example.com",
    "GITHUB_REPOSITORY_OWNER": "fallback-owner",
}


@pytest.fixture(autouse=True, scope="session")
def _patch_env():
    """Patch environment variables before any import of shared.py."""
    with patch.dict("os.environ", ENV_DEFAULTS, clear=False):
        yield


# ---------------------------------------------------------------------------
# Lazy import of shared after env is set
# ---------------------------------------------------------------------------
@pytest.fixture(scope="session")
def shared_module(_patch_env):
    """Import shared module once with env vars in place."""
    # Remove cached module if already imported without env
    sys.modules.pop("shared", None)
    import importlib, importlib.util, pathlib

    spec = importlib.util.spec_from_file_location(
        "shared", pathlib.Path(".github/scripts/shared.py")
    )
    mod = importlib.util.module_from_spec(spec)

    # Stub anthropic before exec so the module-level client creation won't fail
    fake_anthropic = types.ModuleType("anthropic")
    fake_anthropic.Anthropic = MagicMock()
    sys.modules["anthropic"] = fake_anthropic

    spec.loader.exec_module(mod)
    sys.modules["shared"] = mod
    return mod


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _make_response(status_code=200, json_data=None, text=""):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data if json_data is not None else {}
    resp.text = text
    return resp


# ===========================================================================
# call_claude
# ===========================================================================
class TestCallClaude:
    def test_happy_path_returns_text(self, shared_module):
        fake_text = "Here is the AI response."
        mock_content = MagicMock()
        mock_content.text = fake_text

        mock_client = MagicMock()
        mock_client.messages.create.return_value = MagicMock(content=[mock_content])

        with patch("anthropic.Anthropic", return_value=mock_client):
            # Re-bind so the module picks up our patch
            with patch.object(shared_module.anthropic, "Anthropic", return_value=mock_client):
                result = shared_module.call_claude("system prompt", "user prompt")

        assert result == fake_text

    def test_passes_correct_model_and_tokens(self, shared_module):
        mock_content = MagicMock()
        mock_content.text = "ok"
        mock_client = MagicMock()
        mock_client.messages.create.return_value = MagicMock(content=[mock_content])

        with patch.object(shared_module.anthropic, "Anthropic", return_value=mock_client):
            shared_module.call_claude("sys", "usr", max_tokens=1024)

        call_kwargs = mock_client.messages.create.call_args
        assert call_kwargs.kwargs["model"] == shared_module.MODEL
        assert call_kwargs.kwargs["max_tokens"] == 1024
        assert call_kwargs.kwargs["system"] == "sys"
        assert call_kwargs.kwargs["messages"] == [{"role": "user", "content": "usr"}]

    def test_default_max_tokens(self, shared_module):
        mock_content = MagicMock()
        mock_content.text = "response"
        mock_client = MagicMock()
        mock_client.messages.create.return_value = MagicMock(content=[mock_content])

        with patch.object(shared_module.anthropic, "Anthropic", return_value=mock_client):
            shared_module.call_claude("s", "u")

        assert mock_client.messages.create.call_args.kwargs["max_tokens"] == 4096

    def test_raises_on_api_error(self, shared_module):
        mock_client = MagicMock()
        mock_client.messages.create.side_effect = RuntimeError("API error")

        with patch.object(shared_module.anthropic, "Anthropic", return_value=mock_client):
            with pytest.raises(RuntimeError, match="API error"):
                shared_module.call_claude("s", "u")

    def test_empty_system_and_user(self, shared_module):
        mock_content = MagicMock()
        mock_content.text = ""
        mock_client = MagicMock()
        mock_client.messages.create.return_value = MagicMock(content=[mock_content])

        with patch.object(shared_module.anthropic, "Anthropic", return_value=mock_client):
            result = shared_module.call_claude("", "")

        assert result == ""


# ===========================================================================
# clean_json
# ===========================================================================
class TestCleanJson:
    @pytest.mark.parametrize("raw,expected", [
        # Plain JSON — no fences
        ('{"key": "value"}', '{"key": "value"}'),
        # Triple-backtick json fence
        ('```json\n{"key": "value"}\n```', '{"key": "value"}'),
        # Triple-backtick no language tag
        ('```\n{"key": "value"}\n```', '{"key": "value"}'),
        # Leading/trailing whitespace
        ('  {"key": "value"}  ', '{"key": "value"}'),
        # Fence with leading/trailing whitespace
        ('  ```json\n{"key": "value"}\n```  ', '{"key": "value"}'),
        # Multi-line JSON with fence
        ('```json\n{\n  "a": 1,\n  "b": 2\n}\n```', '{\n  "a": 1,\n  "b": 2\n}'),
        # Empty string
        ('', ''),
        # Only fences, empty content
        ('```\n\n```', ''),
        # JSON array with fence
        ('```json\n[1, 2, 3]\n```', '[1, 2, 3]'),
        # No fence but contains backtick in value
        ('{"url": "https://example.com"}', '{"url": "https://example.com"}'),
    ])
    def test_clean_json_parametrized(self, shared_module, raw, expected):
        assert shared_module.clean_json(raw) == expected

    def test_result_is_valid_json_after_cleaning(self, shared_module):
        raw = '```json\n{"product_name": "Generations II", "doc_type": "product_brochure"}\n```'
        cleaned = shared_module.clean_json(raw)
        parsed = json.loads(cleaned)
        assert parsed["product_name"] == "Generations II"

    def test_insurance_data_sample_cleaned(self, shared_module):
        """Use synthetic data sample shape."""
        inner = json.dumps({
            "product_name": "List of Designated Hospitals in Mainland China",
            "doc_type": "supplementary",
            "linked_product": "health_products",
        })
        raw = f"```json\n{inner}\n```"
        cleaned = shared_module.clean_json(raw)
        parsed = json.loads(cleaned)
        assert parsed["doc_type"] == "supplementary"


# ===========================================================================
# get_repo_files
# ===========================================================================
class TestGetRepoFiles:
    def _make_tree_response(self, items):
        return _make_response(json_data={"tree": items})

    def _make_blob_response(self, content_str):
        encoded = base64.b64encode(content_str.encode()).decode()
        return _make_response(json_data={"content": encoded})

    def test_happy_path_single_file(self, shared_module):
        tree = [{"type": "blob", "path": "README.md", "url": "https://api.github.com/blob/abc"}]
        file_content = "# Hello World"

        with patch("requests.get") as mock_get:
            mock_get.side_effect = [
                self._make_tree_response(tree),
                self._make_blob_response(file_content),
            ]
            result = shared_module.get_repo_files("owner", "repo", [".md"])

        assert "README.md" in result
        assert result["README.md"] == file_content

    def test_filters_by_extension(self, shared_module):
        tree = [
            {"type": "blob", "path": "script.py", "url": "url1"},
            {"type": "blob", "path": "README.md", "url": "url2"},
            {"type": "blob", "path": "config.json", "url": "url3"},
        ]
        py_content = "print('hello')"

        with patch("requests.get") as mock_get:
            mock_get.side_effect = [
                self._make_tree_response(tree),
                self._make_blob_response(py_content),
            ]
            result = shared_module.get_repo_files("owner", "repo", [".py"])

        assert "script.py" in result
        assert "README.md" not in result
        assert "config.json" not in result

    def test_skips_non_blob_items(self, shared_module):
        tree = [
            {"type": "tree", "path": "src", "url": "url-tree"},
            {"type": "blob", "path": "main.py", "url": "url-blob"},
        ]

        with patch("requests.get") as mock_get:
            mock_get.side_effect = [
                self._make_tree_response(tree),
                self._make_blob_response("content"),
            ]
            result = shared_module.get_repo_files("owner", "repo", [".py"])

        assert "src" not in result
        assert "main.py" in result

    def test_respects_max_files(self, shared_module):
        tree = [
            {"type": "blob", "path": f"file{i}.py", "url": f"url{i}"}
            for i in range(10)
        ]

        blob_response = self._make_blob_response("content")
        responses = [self._make_tree_response(tree)] + [
            self._make_blob_response(f"content{i}") for i in range(3)
        ]

        with patch("requests.get") as mock_get:
            mock_get.side_effect = responses
            result = shared_module.get_repo_files("owner", "repo", [".py"], max_files=3)

        assert len(result) == 3

    def test_empty_tree_returns_empty_dict(self, shared_module):
        with patch("requests.get") as mock_get:
            mock_get.return_value = self._make_tree_response([])
            result = shared_module.get_repo_files("owner", "repo", [".py"])

        assert result == {}

    def test_handles_blob_decode_exception_gracefully(self, shared_module):
        tree = [{"type": "blob", "path": "bad.py", "url": "url-bad"}]
        bad_blob = _make_response(json_data={"content": "not-valid-base64!!!"})

        with patch("requests.get") as mock_get:
            mock_get.side_effect = [self._make_tree_response(tree), bad_blob]
            # Should not raise
            result = shared_module.get_repo_files("owner", "repo", [".py"])

        # bad file is skipped silently
        assert "bad.py" not in result

    def test_multiple_extensions(self, shared_module):
        tree = [
            {"type": "blob", "path": "app.py", "url": "url1"},
            {"type": "blob", "path": "index.js", "url": "url2"},
            {"type": "blob", "path": "style.css", "url": "url3"},
        ]

        with patch("requests.get") as mock_get:
            mock_get.side_effect = [
                self._make_tree_response(tree),
                self._make_blob_response("python code"),
                self._make_blob_response("js code"),
            ]
            result = shared_module.get_repo_files("owner", "repo", [".py", ".js"])

        assert "app.py" in result
        assert "index.js" in result
        assert "style.css" not in result

    def test_url_construction(self, shared_module):
        with patch("requests.get") as mock_get:
            mock_get.return_value = self._make_tree_response([])
            shared_module.get_repo_files("myowner", "myrepo", [".py"])

        first_call_url = mock_get.call_args_list[0][0][0]
        assert "myowner" in first_call_url
        assert "myrepo" in first_call_url
        assert "recursive=1" in first_call_url


# ===========================================================================
# get_pr_diff
# ===========================================================================
class TestGetPrDiff:
    def test_happy_path_returns_diff_text(self, shared_module):
        diff_text = "diff --git a/file.py b/file.py\n+new line"
        with patch("requests.get") as mock_get:
            mock_get.return_value = _make_response(text=diff_text)
            result = shared_module.get_pr_diff("owner", "repo", 42)

        assert result == diff_text

    def test_truncates_to_30000_chars(self, shared_module):
        long_diff = "x" * 50000
        with patch("requests.get") as mock_get:
            mock_get.return_value = _make_response(text=long_diff)
            result = shared_module.get_pr_diff("owner", "repo", 1)

        assert len(result) == 30000

    def test_url_contains_pr_number(self, shared_module):
        with patch("requests.get