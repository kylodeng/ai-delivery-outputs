"""
Test suite for .github/scripts/shared.py

What is tested:
- call_claude(): Claude API invocation, response extraction
- clean_json(): Markdown fence stripping, edge cases
- get_repo_files(): GitHub tree traversal, base64 decoding, extension filtering, max_files limit
- get_pr_diff(): PR diff fetching, truncation at 30000 chars
- write_output_file(): File create (no SHA) and update (with SHA) paths, URL fallback
- post_pr_comment(): PR comment posting
- send_email(): SendGrid payload construction, success/failure status handling
- email_html(): HTML generation, SUCCESS/FAILURE colour logic, field injection
- write_audit_entry(): Audit log JSON + Markdown construction (partial — file write stubbed)

Mocks used:
- unittest.mock.patch for os.environ (prevent KeyError on import)
- unittest.mock.MagicMock / patch for anthropic.Anthropic client
- unittest.mock.patch for requests.get / requests.post / requests.put
- datetime.datetime.utcnow patched for deterministic timestamps

TODOs:
- TODO: write_audit_entry full round-trip requires write_output_file to be called twice
        (JSON + Markdown); integration test needs a live-ish stub of write_output_file
- TODO: call_claude token-limit / rate-limit error handling is not implemented in source;
        add tests once retry logic exists
- TODO: get_repo_files pagination beyond max_files=20 with large repos
"""

import base64
import datetime
import importlib
import json
import sys
import types
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Bootstrap: provide mandatory env vars BEFORE shared.py is imported so the
# module-level os.environ[] accesses don't raise KeyError.
# ---------------------------------------------------------------------------
_ENV_DEFAULTS = {
    "ANTHROPIC_API_KEY": "test-anthropic-key",
    "GH_TOKEN": "test-gh-token",
    "SENDGRID_API_KEY": "test-sg-key",
    "OUTPUT_REPO": "ai-delivery-outputs",
    "OUTPUT_REPO_OWNER": "test-owner",
    "NOTIFY_EMAIL": "notify@example.com",
    "SENDER_EMAIL": "sender@example.com",
    "GITHUB_REPOSITORY_OWNER": "test-owner",
}


@pytest.fixture(autouse=True, scope="session")
def _patch_env_for_import():
    """Ensure all required env vars exist for the entire test session."""
    with patch.dict("os.environ", _ENV_DEFAULTS, clear=False):
        # (Re)import the module under the patched environment.
        if "shared" in sys.modules:
            del sys.modules["shared"]
        # Make the scripts directory importable.
        import os
        scripts_dir = os.path.join(os.path.dirname(__file__), "..", "..", ".github", "scripts")
        scripts_dir = os.path.abspath(scripts_dir)
        if scripts_dir not in sys.path:
            sys.path.insert(0, scripts_dir)
        yield


@pytest.fixture(scope="session")
def shared_module(_patch_env_for_import):
    """Return the shared module, imported exactly once per session."""
    import importlib
    with patch.dict("os.environ", _ENV_DEFAULTS, clear=False):
        import shared
        return shared


# Convenience alias used by most tests
@pytest.fixture()
def sh(shared_module):
    return shared_module


# ---------------------------------------------------------------------------
# clean_json
# ---------------------------------------------------------------------------
class TestCleanJson:
    def test_plain_json_unchanged(self, sh):
        raw = '{"key": "value"}'
        assert sh.clean_json(raw) == '{"key": "value"}'

    def test_strips_json_fence(self, sh):
        raw = "```json\n{\"key\": \"value\"}\n```"
        result = sh.clean_json(raw)
        assert result == '{"key": "value"}'

    def test_strips_plain_backtick_fence(self, sh):
        raw = "```\n{\"a\": 1}\n```"
        result = sh.clean_json(raw)
        assert result == '{"a": 1}'

    def test_strips_leading_trailing_whitespace(self, sh):
        raw = "   \n{\"x\": 2}\n   "
        assert sh.clean_json(raw) == '{"x": 2}'

    def test_empty_string(self, sh):
        assert sh.clean_json("") == ""

    def test_fence_with_extra_whitespace_inside(self, sh):
        raw = "```json\n\n  {\"k\": \"v\"}\n\n```"
        result = sh.clean_json(raw)
        assert result == '{"k": "v"}'

    def test_no_closing_fence_returns_content_after_first_line(self, sh):
        """If there's an opening fence but no closing ```, rsplit returns the whole remainder."""
        raw = "```json\n{\"only\": \"open\"}"
        result = sh.clean_json(raw)
        assert '{"only": "open"}' in result

    def test_nested_json_object(self, sh):
        nested = '{"a": {"b": [1, 2, 3]}}'
        raw = f"```json\n{nested}\n```"
        assert sh.clean_json(raw) == nested

    @pytest.mark.parametrize("raw,expected", [
        ('{"n": 1}', '{"n": 1}'),
        ("```\n[]\n```", "[]"),
        ("  ```json\n{}\n```  ", "{}"),
    ])
    def test_parametrized_cases(self, sh, raw, expected):
        assert sh.clean_json(raw) == expected


# ---------------------------------------------------------------------------
# call_claude
# ---------------------------------------------------------------------------
class TestCallClaude:
    def _make_mock_client(self, text="Hello from Claude"):
        mock_content = MagicMock()
        mock_content.text = text
        mock_response = MagicMock()
        mock_response.content = [mock_content]
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response
        return mock_client

    def test_happy_path_returns_text(self, sh):
        mock_client = self._make_mock_client("test response")
        with patch("anthropic.Anthropic", return_value=mock_client):
            result = sh.call_claude("system prompt", "user prompt")
        assert result == "test response"

    def test_passes_system_and_user(self, sh):
        mock_client = self._make_mock_client()
        with patch("anthropic.Anthropic", return_value=mock_client):
            sh.call_claude("sys", "usr", max_tokens=1024)
        mock_client.messages.create.assert_called_once_with(
            model=sh.MODEL,
            max_tokens=1024,
            system="sys",
            messages=[{"role": "user", "content": "usr"}],
        )

    def test_default_max_tokens(self, sh):
        mock_client = self._make_mock_client()
        with patch("anthropic.Anthropic", return_value=mock_client):
            sh.call_claude("s", "u")
        _, kwargs = mock_client.messages.create.call_args
        assert kwargs["max_tokens"] == 4096

    def test_uses_correct_model(self, sh):
        mock_client = self._make_mock_client()
        with patch("anthropic.Anthropic", return_value=mock_client):
            sh.call_claude("s", "u")
        _, kwargs = mock_client.messages.create.call_args
        assert kwargs["model"] == "claude-sonnet-4-6"

    def test_api_key_passed_to_client(self, sh):
        mock_client = self._make_mock_client()
        with patch("anthropic.Anthropic", return_value=mock_client) as mock_cls:
            sh.call_claude("s", "u")
        mock_cls.assert_called_once_with(api_key="test-anthropic-key")

    def test_propagates_exception(self, sh):
        mock_client = MagicMock()
        mock_client.messages.create.side_effect = RuntimeError("API down")
        with patch("anthropic.Anthropic", return_value=mock_client):
            with pytest.raises(RuntimeError, match="API down"):
                sh.call_claude("s", "u")

    def test_empty_response_text(self, sh):
        mock_client = self._make_mock_client("")
        with patch("anthropic.Anthropic", return_value=mock_client):
            result = sh.call_claude("s", "u")
        assert result == ""


# ---------------------------------------------------------------------------
# get_repo_files
# ---------------------------------------------------------------------------
class TestGetRepoFiles:
    def _tree_response(self, items):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"tree": items}
        return mock_resp

    def _blob_response(self, content: str):
        encoded = base64.b64encode(content.encode()).decode()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"content": encoded}
        return mock_resp

    def test_happy_path_filters_by_extension(self, sh):
        tree = [
            {"type": "blob", "path": "README.md", "url": "http://blob/readme"},
            {"type": "blob", "path": "main.py",   "url": "http://blob/main"},
            {"type": "blob", "path": "data.json", "url": "http://blob/data"},
        ]
        responses = {
            "http://blob/readme": self._blob_response("# Readme"),
            "http://blob/data":   self._blob_response('{"key": "val"}'),
        }

        def fake_get(url, headers=None):
            if "trees" in url:
                return self._tree_response(tree)
            return responses[url]

        with patch("requests.get", side_effect=fake_get):
            result = sh.get_repo_files("owner", "repo", [".md", ".json"])

        assert "README.md" in result
        assert "data.json" in result
        assert "main.py" not in result
        assert result["README.md"] == "# Readme"

    def test_max_files_limit(self, sh):
        tree = [
            {"type": "blob", "path": f"file{i}.py", "url": f"http://blob/{i}"}
            for i in range(10)
        ]

        def fake_get(url, headers=None):
            if "trees" in url:
                return self._tree_response(tree)
            mock = MagicMock()
            mock.json.return_value = {"content": base64.b64encode(b"code").decode()}
            return mock

        with patch("requests.get", side_effect=fake_get):
            result = sh.get_repo_files("owner", "repo", [".py"], max_files=3)

        assert len(result) == 3

    def test_skips_non_blob_items(self, sh):
        tree = [
            {"type": "tree", "path": "src/",       "url": "http://blob/dir"},
            {"type": "blob", "path": "src/app.py", "url": "http://blob/app"},
        ]

        def fake_get(url, headers=None):
            if "trees" in url:
                return self._tree_response(tree)
            mock = MagicMock()
            mock.json.return_value = {"content": base64.b64encode(b"print()").decode()}
            return mock

        with patch("requests.get", side_effect=fake_get):
            result = sh.get_repo_files("owner", "repo", [".py"])

        assert "src/" not in result
        assert "src/app.py" in result

    def test_empty_tree_returns_empty_dict(self, sh):
        def fake_get(url, headers=None):
            mock = MagicMock()
            mock.json.return_value = {"tree": []}
            return mock

        with patch("requests.get", side_effect=fake_get):
            result = sh.get_repo_files("owner", "repo", [".py"])

        assert result == {}

    def test_missing_tree_key_returns_empty_dict(self, sh):
        def fake_get(url, headers=None):
            mock = MagicMock()
            mock.json.return_value = {}
            return mock

        with patch("requests.get", side_effect=fake_get):
            result = sh.get_repo_files("owner", "repo", [".py"])

        assert result == {}

    def test_invalid_base64_content_skipped(self, sh):
        tree = [{"type": "blob", "path": "bad.py", "url": "http://blob/bad"}]

        def fake_get(url, headers=None):
            if "trees" in url:
                return self._tree_response(tree)
            mock = MagicMock()
            mock.json.return_value = {"content": "!!!not-valid-base64!!!"}
            return mock

        with patch("requests.get", side_effect=fake_get):
            # Should not raise; bad file just gets skipped
            result = sh.get_repo_files("owner", "repo", [".py"])

        assert result == {}

    def test_no_matching_extensions(self, sh):
        tree = [{"type": "blob", "path": "file.go", "url": "http://blob/go"}]

        def fake_get(url, headers=None):
            if "trees" in url:
                return self._tree_response(tree)
            return MagicMock()

        with patch("requests.get", side_effect=fake_get):
            result = sh.get_repo_files("owner", "repo", [".py", ".js"])

        assert result == {}

    def test_correct_url_constructed(self, sh):
        captured = []

        def fake_get(url, headers=None):
            captured.append(url)
            mock = MagicMock()
            mock.json.return_value = {"tree": []}
            return mock

        with patch("requests.get", side_effect=fake_get):
            sh.get_repo_files("myowner", "myrepo", [".py"])

        assert captured[0] == "https://api.github.com/repos/myowner/myrepo/git/trees/HEAD?recursive=1"

    def test_utf8_errors_replaced(self, sh):
        """Files with non-UTF-8 bytes should still be included with replacement chars."""
        bad_bytes = b"hello \xff\xfe world"
        encoded = base64.b64encode(bad_bytes).decode()
        tree = [{"type": "blob", "path": "binary.py", "url": "http://blob/bin"}]

        def fake_get(url, headers=None):
            if "trees" in url:
                return self._tree_response(tree)
            mock = MagicMock()
            mock.json.return_value = {"content": encoded}
            return mock

        with patch("requests.get", side_effect=fake_get):
            result = sh.get_repo_files("o", "r", [".py"])

        assert "binary.py" in result
        assert "hello" in result["binary.py"]


# ---------------------------------------------------------------------------
# get_pr_diff
# ---------------------------------------------------------------------------
class TestGetPrDiff:
    def test_returns_diff_text(self, sh):
        mock_resp = MagicMock()
        mock_resp.text = "diff --git a/f b/f\n+added line"
        with patch("requests.get", return_value=mock_resp):
            result = sh.get_pr_diff("owner", "repo", 42)
        assert result == "diff --git a/f b/f\n+added line"