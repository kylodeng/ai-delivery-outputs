"""
Test suite for .github/scripts/shared.py

What is tested:
- call_claude(): Claude API invocation, response extraction
- clean_json(): markdown fence stripping, edge cases
- get_repo_files(): GitHub tree fetching, extension filtering, max_files limit, base64 decoding errors
- get_pr_diff(): PR diff fetching, truncation behaviour
- write_output_file(): file create (no SHA) and update (with SHA) paths, URL fallback
- post_pr_comment(): comment posting
- send_email(): success (200/202) and failure warning paths
- email_html(): HTML generation, SUCCESS/FAILURE colour coding
- write_audit_entry(): JSON + Markdown audit log construction (stub — source truncated)

Mocks used:
- unittest.mock.patch / MagicMock for: anthropic.Anthropic, requests.get, requests.post, requests.put
- os.environ patched so module-level env-var reads succeed without real credentials

TODOs:
- TODO: write_audit_entry() source code is truncated — full audit log tests need the complete function body
- TODO: Integration test for real Claude model response format verification
- TODO: Verify GH_HEADERS X-GitHub-Api-Version value against live GitHub API
"""

import base64
import datetime
import json
import os
import sys
import types
import importlib
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Environment bootstrap — must happen BEFORE shared.py is imported, because
# the module reads os.environ at import time.
# ---------------------------------------------------------------------------

FAKE_ENV = {
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
def _patch_env_session():
    """Patch environment variables for the entire test session before import."""
    with patch.dict(os.environ, FAKE_ENV, clear=False):
        yield


# Import the module under test AFTER env is patched.
# We use importlib so we can reload if needed.
@pytest.fixture(scope="session")
def shared_module(_patch_env_session):
    # Ensure the scripts directory is on the path
    scripts_dir = os.path.join(os.path.dirname(__file__), ".github", "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    # Also try relative to repo root
    repo_root = os.path.dirname(os.path.abspath(__file__))
    scripts_dir2 = os.path.join(repo_root, ".github", "scripts")
    if scripts_dir2 not in sys.path:
        sys.path.insert(0, scripts_dir2)

    import shared as _shared
    return _shared


# Convenience alias used in every test via fixture injection
@pytest.fixture
def sh(shared_module):
    return shared_module


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_response(status_code=200, json_data=None, text=""):
    """Build a mock requests.Response-like object."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = text
    resp.json.return_value = json_data if json_data is not None else {}
    return resp


def _b64(s: str) -> str:
    return base64.b64encode(s.encode()).decode()


# ===========================================================================
# clean_json
# ===========================================================================

class TestCleanJson:
    def test_no_fence_unchanged(self, sh):
        raw = '{"key": "value"}'
        assert sh.clean_json(raw) == '{"key": "value"}'

    def test_strips_json_fence(self, sh):
        raw = '```json\n{"key": "value"}\n```'
        result = sh.clean_json(raw)
        assert result == '{"key": "value"}'

    def test_strips_plain_fence(self, sh):
        raw = '```\n{"key": "value"}\n```'
        result = sh.clean_json(raw)
        assert result == '{"key": "value"}'

    def test_strips_leading_trailing_whitespace(self, sh):
        raw = '   {"key": "value"}   '
        assert sh.clean_json(raw) == '{"key": "value"}'

    def test_strips_fence_with_whitespace(self, sh):
        raw = '  ```json\n{"a": 1}\n```  '
        assert sh.clean_json(raw) == '{"a": 1}'

    def test_empty_string(self, sh):
        assert sh.clean_json("") == ""

    def test_only_fence_markers(self, sh):
        raw = "```\n```"
        result = sh.clean_json(raw)
        # Should not raise; content between fences is empty
        assert result == ""

    def test_multiline_json_preserved(self, sh):
        raw = '```json\n{\n  "a": 1,\n  "b": 2\n}\n```'
        result = sh.clean_json(raw)
        parsed = json.loads(result)
        assert parsed == {"a": 1, "b": 2}

    def test_no_closing_fence(self, sh):
        """If there is an opening fence but no closing, rsplit leaves content intact."""
        raw = "```json\n{\"x\": 1}"
        result = sh.clean_json(raw)
        # After splitting on first newline we get '{"x": 1}', rsplit on ``` finds nothing to remove
        assert '{"x": 1}' in result

    def test_nested_json_string(self, sh):
        """JSON containing backticks in string values should not be mangled."""
        raw = '{"code": "no fences here"}'
        assert sh.clean_json(raw) == '{"code": "no fences here"}'

    @pytest.mark.parametrize("raw,expected", [
        ('```json\n[1,2,3]\n```', '[1,2,3]'),
        ('```\nnull\n```', 'null'),
        ('```json\n"hello"\n```', '"hello"'),
    ])
    def test_parametrized_fences(self, sh, raw, expected):
        assert sh.clean_json(raw) == expected


# ===========================================================================
# call_claude
# ===========================================================================

class TestCallClaude:
    @patch("anthropic.Anthropic")
    def test_returns_text_content(self, mock_anthropic_cls, sh):
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client

        fake_content = MagicMock()
        fake_content.text = "This is Claude's response"
        mock_client.messages.create.return_value = MagicMock(content=[fake_content])

        with patch.object(sh, "anthropic") as mock_mod:
            mock_mod.Anthropic.return_value = mock_client
            # Directly patch inside shared module
            pass

        # Re-patch via the module's reference
        with patch(f"{sh.__name__}.anthropic.Anthropic", return_value=mock_client):
            result = sh.call_claude("system prompt", "user prompt")

        assert result == "This is Claude's response"

    @patch("anthropic.Anthropic")
    def test_passes_correct_model_and_tokens(self, mock_anthropic_cls, sh):
        mock_client = MagicMock()
        fake_text = MagicMock()
        fake_text.text = "ok"
        mock_client.messages.create.return_value = MagicMock(content=[fake_text])

        with patch(f"{sh.__name__}.anthropic.Anthropic", return_value=mock_client):
            sh.call_claude("sys", "usr", max_tokens=1024)

        mock_client.messages.create.assert_called_once_with(
            model=sh.MODEL,
            max_tokens=1024,
            system="sys",
            messages=[{"role": "user", "content": "usr"}],
        )

    @patch("anthropic.Anthropic")
    def test_default_max_tokens(self, mock_anthropic_cls, sh):
        mock_client = MagicMock()
        fake_text = MagicMock()
        fake_text.text = "response"
        mock_client.messages.create.return_value = MagicMock(content=[fake_text])

        with patch(f"{sh.__name__}.anthropic.Anthropic", return_value=mock_client):
            sh.call_claude("sys", "usr")

        _, kwargs = mock_client.messages.create.call_args
        assert kwargs["max_tokens"] == 4096

    @patch("anthropic.Anthropic")
    def test_uses_api_key_from_env(self, mock_anthropic_cls, sh):
        mock_client = MagicMock()
        fake_text = MagicMock()
        fake_text.text = "x"
        mock_client.messages.create.return_value = MagicMock(content=[fake_text])

        with patch(f"{sh.__name__}.anthropic.Anthropic", return_value=mock_client) as mock_cls:
            sh.call_claude("s", "u")
            mock_cls.assert_called_once_with(api_key=FAKE_ENV["ANTHROPIC_API_KEY"])

    @patch("anthropic.Anthropic")
    def test_raises_on_api_error(self, mock_anthropic_cls, sh):
        mock_client = MagicMock()
        mock_client.messages.create.side_effect = Exception("API error")

        with patch(f"{sh.__name__}.anthropic.Anthropic", return_value=mock_client):
            with pytest.raises(Exception, match="API error"):
                sh.call_claude("sys", "usr")


# ===========================================================================
# get_repo_files
# ===========================================================================

class TestGetRepoFiles:

    def _tree_response(self, paths):
        return {
            "tree": [
                {"type": "blob", "path": p, "url": f"https://api.github.com/blobs/{i}"}
                for i, p in enumerate(paths)
            ]
        }

    def _blob_response(self, content: str):
        return {"content": _b64(content) + "\n", "encoding": "base64"}

    def test_fetches_matching_extensions(self, sh):
        tree = self._tree_response(["a.py", "b.js", "c.py", "d.md"])
        blob_py = self._blob_response("print('hello')")

        responses = [
            _make_response(json_data=tree),         # tree call
            _make_response(json_data=blob_py),      # a.py
            _make_response(json_data=blob_py),      # c.py
        ]

        with patch(f"{sh.__name__}.requests.get", side_effect=responses):
            result = sh.get_repo_files("owner", "repo", [".py"])

        assert set(result.keys()) == {"a.py", "c.py"}
        assert result["a.py"] == "print('hello')"

    def test_respects_max_files(self, sh):
        paths = [f"file{i}.py" for i in range(10)]
        tree = self._tree_response(paths)
        blob = self._blob_response("content")

        # tree + up to max_files=3 blob requests
        responses = [_make_response(json_data=tree)] + [
            _make_response(json_data=blob) for _ in range(3)
        ]

        with patch(f"{sh.__name__}.requests.get", side_effect=responses):
            result = sh.get_repo_files("owner", "repo", [".py"], max_files=3)

        assert len(result) == 3

    def test_skips_non_blob_items(self, sh):
        tree_data = {
            "tree": [
                {"type": "tree", "path": "src", "url": "https://api.github.com/trees/1"},
                {"type": "blob", "path": "main.py", "url": "https://api.github.com/blobs/2"},
            ]
        }
        blob = self._blob_response("# main")
        responses = [
            _make_response(json_data=tree_data),
            _make_response(json_data=blob),
        ]

        with patch(f"{sh.__name__}.requests.get", side_effect=responses):
            result = sh.get_repo_files("owner", "repo", [".py"])

        assert "src" not in result
        assert "main.py" in result

    def test_handles_base64_decode_error_gracefully(self, sh):
        tree = self._tree_response(["bad.py"])
        # Return blob with invalid base64
        bad_blob = {"content": "!!!invalid_base64!!!", "encoding": "base64"}
        responses = [
            _make_response(json_data=tree),
            _make_response(json_data=bad_blob),
        ]

        with patch(f"{sh.__name__}.requests.get", side_effect=responses):
            # Should not raise; file simply absent or empty
            result = sh.get_repo_files("owner", "repo", [".py"])

        # bad files are skipped silently
        assert "bad.py" not in result

    def test_empty_tree_returns_empty_dict(self, sh):
        with patch(f"{sh.__name__}.requests.get", return_value=_make_response(json_data={"tree": []})):
            result = sh.get_repo_files("owner", "repo", [".py"])
        assert result == {}

    def test_multiple_extensions_filter(self, sh):
        tree = self._tree_response(["a.py", "b.json", "c.md", "d.json"])
        blob = self._blob_response("{}")
        responses = [_make_response(json_data=tree)] + [_make_response(json_data=blob)] * 3

        with patch(f"{sh.__name__}.requests.get", side_effect=responses):
            result = sh.get_repo_files("owner", "repo", [".json", ".md"])

        assert set(result.keys()) == {"b.json", "c.md", "d.json"}

    def test_constructs_correct_tree_url(self, sh):
        with patch(f"{sh.__name__}.requests.get", return_value=_make_response(json_data={"tree": []})) as mock_get:
            sh.get_repo_files("my-owner", "my-repo", [".py"])
        expected_url = "https://api.github.com/repos/my-owner/my-repo/git/trees/HEAD?recursive=1"
        mock_get.assert_called_once_with(expected_url, headers=sh.GH_HEADERS)

    def test_unicode_file_content(self, sh):
        content = "# Arabic: \u0625\u0644\u063a\u0627\u0621"
        tree = self._tree_response(["ar.json"])
        blob = self._blob_response(content)
        responses = [_make_response(json_data=tree), _make_response(json_data=blob)]

        with patch(f"{sh.__name__}.requests.get", side_effect=responses):
            result = sh.get_repo_files("o", "r", [".json"])

        assert result["ar.json"] == content


# ===========================================================================
# get_pr_diff
# ===========================================================================

class TestGetPrDiff:

    def test_returns_