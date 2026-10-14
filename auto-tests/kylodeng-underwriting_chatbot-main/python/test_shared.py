"""
Test suite for .github/scripts/shared.py

What is tested:
    - call_claude: happy path, API response parsing, token forwarding
    - clean_json: strips markdown fences, handles plain JSON, edge cases
    - get_repo_files: filtering by extension, max_files limit, base64 decoding, decode errors
    - get_pr_diff: URL construction, truncation at 30000 chars, header override
    - write_output_file: create new file (no SHA), update existing file (with SHA), URL fallback
    - post_pr_comment: correct URL and payload
    - send_email: happy path (200/202), warning on failure
    - email_html: SUCCESS/FAILURE status colour, content presence
    - write_audit_entry: (stub — source truncated, see TODO)

Mocks used:
    - unittest.mock.patch / MagicMock for anthropic.Anthropic client
    - unittest.mock.patch for requests.get / requests.post / requests.put
    - os.environ patched via monkeypatch / patch.dict

TODOs:
    - TODO: write_audit_entry body is truncated in source — full implementation needed to
      test JSON/Markdown audit log writing end-to-end.
    - TODO: Integration test for call_claude against real Anthropic sandbox (needs creds).
"""

import base64
import json
import os
import sys
import types
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Environment bootstrap — must happen BEFORE shared.py is imported so the
# module-level os.environ[] lookups don't raise KeyError.
# ---------------------------------------------------------------------------
_ENV_DEFAULTS = {
    "ANTHROPIC_API_KEY": "test-anthropic-key",
    "GH_TOKEN": "test-gh-token",
    "SENDGRID_API_KEY": "test-sendgrid-key",
    "OUTPUT_REPO": "ai-delivery-outputs",
    "OUTPUT_REPO_OWNER": "test-owner",
    "NOTIFY_EMAIL": "notify@example.com",
    "SENDER_EMAIL": "sender@example.com",
    "GITHUB_REPOSITORY_OWNER": "test-owner",
}

with patch.dict(os.environ, _ENV_DEFAULTS, clear=False):
    # Add the scripts directory to sys.path so the import resolves
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".github", "scripts"))
    # Also try the repo root in case pytest is run from there
    sys.path.insert(0, os.path.join(os.path.dirname(__file__)))
    import importlib
    import importlib.util

    _script_path = os.path.join(
        os.path.dirname(__file__), ".github", "scripts", "shared.py"
    )
    _spec = importlib.util.spec_from_file_location("shared", _script_path)
    shared = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(shared)


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_module_globals(monkeypatch):
    """Ensure module-level constants reflect test env values."""
    monkeypatch.setattr(shared, "OUTPUT_REPO", "ai-delivery-outputs")
    monkeypatch.setattr(shared, "OUTPUT_REPO_OWNER", "test-owner")
    monkeypatch.setattr(shared, "NOTIFY_EMAIL", "notify@example.com")
    monkeypatch.setattr(shared, "SENDER_EMAIL", "sender@example.com")
    monkeypatch.setattr(shared, "SENDGRID_API_KEY", "test-sendgrid-key")
    monkeypatch.setattr(shared, "GH_TOKEN", "test-gh-token")
    monkeypatch.setattr(shared, "ANTHROPIC_API_KEY", "test-anthropic-key")


def _b64(text: str) -> str:
    """Return base64-encoded string (as GitHub API returns it)."""
    return base64.b64encode(text.encode()).decode()


# ===========================================================================
# call_claude
# ===========================================================================

class TestCallClaude:
    def _make_client(self, text: str):
        """Build a mock Anthropic client whose messages.create returns `text`."""
        mock_content = MagicMock()
        mock_content.text = text
        mock_response = MagicMock()
        mock_response.content = [mock_content]
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response
        return mock_client

    def test_happy_path_returns_text(self):
        expected = "Claude says hello."
        mock_client = self._make_client(expected)

        with patch("anthropic.Anthropic", return_value=mock_client):
            result = shared.call_claude("system prompt", "user prompt")

        assert result == expected

    def test_passes_system_and_user_to_api(self):
        mock_client = self._make_client("ok")

        with patch("anthropic.Anthropic", return_value=mock_client):
            shared.call_claude("sys", "usr", max_tokens=512)

        mock_client.messages.create.assert_called_once_with(
            model=shared.MODEL,
            max_tokens=512,
            system="sys",
            messages=[{"role": "user", "content": "usr"}],
        )

    def test_default_max_tokens(self):
        mock_client = self._make_client("ok")

        with patch("anthropic.Anthropic", return_value=mock_client):
            shared.call_claude("s", "u")

        _, kwargs = mock_client.messages.create.call_args
        assert kwargs["max_tokens"] == 4096

    def test_uses_api_key_from_module(self):
        mock_client = self._make_client("ok")

        with patch("anthropic.Anthropic", return_value=mock_client) as mock_cls:
            shared.call_claude("s", "u")

        mock_cls.assert_called_once_with(api_key="test-anthropic-key")

    def test_custom_max_tokens(self):
        mock_client = self._make_client("response")

        with patch("anthropic.Anthropic", return_value=mock_client):
            result = shared.call_claude("s", "u", max_tokens=1024)

        assert result == "response"
        _, kwargs = mock_client.messages.create.call_args
        assert kwargs["max_tokens"] == 1024

    def test_multiline_response(self):
        text = "Line 1\nLine 2\nLine 3"
        mock_client = self._make_client(text)

        with patch("anthropic.Anthropic", return_value=mock_client):
            result = shared.call_claude("s", "u")

        assert result == text

    def test_empty_response_text(self):
        mock_client = self._make_client("")

        with patch("anthropic.Anthropic", return_value=mock_client):
            result = shared.call_claude("s", "u")

        assert result == ""

    def test_api_exception_propagates(self):
        mock_client = MagicMock()
        mock_client.messages.create.side_effect = RuntimeError("API failure")

        with patch("anthropic.Anthropic", return_value=mock_client):
            with pytest.raises(RuntimeError, match="API failure"):
                shared.call_claude("s", "u")


# ===========================================================================
# clean_json
# ===========================================================================

class TestCleanJson:
    @pytest.mark.parametrize("raw,expected", [
        # Plain JSON — untouched
        ('{"key": "value"}', '{"key": "value"}'),
        # Markdown fence with json label
        ('```json\n{"key": "value"}\n```', '{"key": "value"}'),
        # Markdown fence without label
        ('```\n{"key": "value"}\n```', '{"key": "value"}'),
        # Leading/trailing whitespace only
        ('  {"a": 1}  ', '{"a": 1}'),
        # Fence with surrounding whitespace
        ('  ```json\n{"x":1}\n```  ', '{"x":1}'),
        # Array JSON
        ('```json\n[1, 2, 3]\n```', '[1, 2, 3]'),
        # Empty string
        ('', ''),
        # Just a fence opening — edge case
        ('```json\n```', ''),
    ])
    def test_clean_json_variants(self, raw, expected):
        assert shared.clean_json(raw) == expected

    def test_no_mutation_on_valid_json(self):
        raw = '{"model_name": "Underwriting Risk Classification", "model_type": "CatBoostClassifier"}'
        assert shared.clean_json(raw) == raw

    def test_nested_json(self):
        inner = '{"a": {"b": [1, 2, 3]}}'
        fenced = f"```json\n{inner}\n```"
        result = shared.clean_json(fenced)
        assert json.loads(result) == {"a": {"b": [1, 2, 3]}}

    def test_multiple_backtick_blocks_only_strips_outermost(self):
        """Only the first fence is stripped; inner ``` remain in content."""
        raw = "```json\n{\"code\": \"```inner```\"}\n```"
        result = shared.clean_json(raw)
        # Should be valid JSON after stripping outer fences
        parsed = json.loads(result)
        assert "code" in parsed


# ===========================================================================
# get_repo_files
# ===========================================================================

class TestGetRepoFiles:
    def _tree_item(self, path: str, url: str = "https://api.github.com/blob/abc"):
        return {"type": "blob", "path": path, "url": url}

    def _blob_response(self, content: str):
        resp = MagicMock()
        resp.json.return_value = {"content": _b64(content)}
        return resp

    def _tree_response(self, items: list):
        resp = MagicMock()
        resp.json.return_value = {"tree": items}
        return resp

    def test_happy_path_filters_by_extension(self):
        tree = [
            self._tree_item("README.md"),
            self._tree_item("src/main.py"),
            self._tree_item("src/util.js"),
            self._tree_item("docs/index.html"),
        ]

        def mock_get(url, headers=None):
            if "git/trees" in url:
                return self._tree_response(tree)
            if "README.md" in url or "index.html" in url:
                return self._blob_response("blob content")
            return self._blob_response("python content")

        with patch("requests.get", side_effect=mock_get):
            result = shared.get_repo_files("owner", "repo", [".py"])

        assert set(result.keys()) == {"src/main.py"}

    def test_max_files_limit(self):
        tree = [self._tree_item(f"file{i}.py") for i in range(30)]

        def mock_get(url, headers=None):
            if "git/trees" in url:
                return self._tree_response(tree)
            return self._blob_response(f"content")

        with patch("requests.get", side_effect=mock_get):
            result = shared.get_repo_files("owner", "repo", [".py"], max_files=5)

        assert len(result) == 5

    def test_multiple_extensions(self):
        tree = [
            self._tree_item("a.py"),
            self._tree_item("b.js"),
            self._tree_item("c.go"),
            self._tree_item("d.txt"),
        ]

        def mock_get(url, headers=None):
            if "git/trees" in url:
                return self._tree_response(tree)
            return self._blob_response("code")

        with patch("requests.get", side_effect=mock_get):
            result = shared.get_repo_files("owner", "repo", [".py", ".js"])

        assert set(result.keys()) == {"a.py", "b.js"}

    def test_skips_non_blob_items(self):
        tree = [
            {"type": "tree", "path": "src", "url": "https://x"},
            self._tree_item("src/file.py"),
        ]

        def mock_get(url, headers=None):
            if "git/trees" in url:
                return self._tree_response(tree)
            return self._blob_response("py content")

        with patch("requests.get", side_effect=mock_get):
            result = shared.get_repo_files("owner", "repo", [".py"])

        assert list(result.keys()) == ["src/file.py"]

    def test_decodes_base64_content_correctly(self):
        original = "print('hello world')\n# unicode: こんにちは"
        tree = [self._tree_item("hello.py")]

        def mock_get(url, headers=None):
            if "git/trees" in url:
                return self._tree_response(tree)
            return self._blob_response(original)

        with patch("requests.get", side_effect=mock_get):
            result = shared.get_repo_files("owner", "repo", [".py"])

        assert result["hello.py"] == original

    def test_handles_decode_exception_gracefully(self):
        """Files that raise on decode are silently skipped."""
        tree = [self._tree_item("bad.py")]

        bad_blob = MagicMock()
        bad_blob.json.return_value = {"content": "!!!NOT BASE64!!!"}

        def mock_get(url, headers=None):
            if "git/trees" in url:
                return self._tree_response(tree)
            return bad_blob

        with patch("requests.get", side_effect=mock_get):
            result = shared.get_repo_files("owner", "repo", [".py"])

        # File is skipped, no exception raised
        assert result == {}

    def test_empty_tree_returns_empty_dict(self):
        def mock_get(url, headers=None):
            return self._tree_response([])

        with patch("requests.get", side_effect=mock_get):
            result = shared.get_repo_files("owner", "repo", [".py"])

        assert result == {}

    def test_no_matching_extensions_returns_empty(self):
        tree = [self._tree_item("file.go"), self._tree_item("file.rs")]

        def mock_get(url, headers=None):
            if "git/trees" in url:
                return self._tree_response(tree)
            return self._blob_response("code")

        with patch("requests.get", side_effect=mock_get):
            result = shared.get_repo_files("owner", "repo", [".py"])

        assert result == {}

    def test_correct_api_url_constructed(self):
        responses = []

        def mock_get(url, headers=None):
            responses.append(url)
            return self._tree_response([])

        with patch("requests.get", side_effect=mock_get):
            shared.get_repo_files("my-owner", "my-repo", [".py"])

        assert responses[0] == "https://api.github.com/repos/my-owner/my-repo/git/trees/HEAD?recursive=1"

    def test_default_max_files_is_20(self):
        tree = [self._tree_item(f"file{i}.py") for i in range(25)]

        def mock_get(url, headers=None):
            if "git/trees" in url:
                return self._tree_response(tree)
            return self._blob_response("content")

        with patch("requests.get", side_effect=mock_