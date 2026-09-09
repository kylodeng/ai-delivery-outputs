"""
Test suite for .github/scripts/shared.py

What is tested:
- call_claude(): Claude API invocation, response extraction
- clean_json(): Markdown fence stripping (happy path, edge cases, no-fence input)
- get_repo_files(): GitHub tree fetching, extension filtering, max_files cap, base64 decoding, error handling
- get_pr_diff(): PR diff fetching, truncation at 30 000 chars
- write_output_file(): File creation (no SHA) and update (with SHA), URL fallback
- post_pr_comment(): PR comment posting
- send_email(): SendGrid payload construction, success and failure status codes
- email_html(): HTML output structure for SUCCESS and FAILURE statuses
- write_audit_entry(): Audit log entries (tested via mock write_output_file)

Mocks used:
- unittest.mock.patch for requests.get, requests.post, requests.put
- unittest.mock.MagicMock for anthropic.Anthropic client
- os.environ patched for all required environment variables

TODOs:
- TODO: Integration test for call_claude() against a real (sandboxed) Anthropic endpoint
- TODO: Test write_audit_entry() JSON/Markdown format in detail once source truncation is resolved
  (source file is truncated before write_audit_entry body is complete)
"""

import base64
import json
import os
import sys
import datetime
import importlib
from unittest import mock
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Environment bootstrap – must happen BEFORE importing shared
# ---------------------------------------------------------------------------
FAKE_ENV = {
    "ANTHROPIC_API_KEY": "test-anthropic-key",
    "GH_TOKEN": "test-gh-token",
    "SENDGRID_API_KEY": "test-sendgrid-key",
    "OUTPUT_REPO": "ai-delivery-outputs",
    "OUTPUT_REPO_OWNER": "test-owner",
    "NOTIFY_EMAIL": "notify@example.com",
    "SENDER_EMAIL": "sender@example.com",
    "GITHUB_REPOSITORY_OWNER": "test-owner",
}


@pytest.fixture(autouse=True, scope="session")
def _patch_env_session():
    """Patch environment variables for the entire test session before import."""
    with mock.patch.dict(os.environ, FAKE_ENV, clear=False):
        yield


# Import the module under test after env is set
with mock.patch.dict(os.environ, FAKE_ENV, clear=False):
    # Make sure the scripts directory is importable
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".github", "scripts"))
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".github", "scripts"))
    sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), ".github/scripts")))

    # Try multiple path strategies
    _script_dir = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        ".github",
        "scripts",
    )
    if os.path.isdir(_script_dir):
        sys.path.insert(0, _script_dir)
    else:
        # Running from repo root
        sys.path.insert(0, os.path.join(os.getcwd(), ".github", "scripts"))

    import shared  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_blob_response(text: str) -> dict:
    """Return a fake GitHub blob API response with base64-encoded content."""
    encoded = base64.b64encode(text.encode()).decode() + "\n"
    return {"content": encoded, "encoding": "base64"}


def _make_tree_response(items: list[dict]) -> dict:
    return {"tree": items}


# ---------------------------------------------------------------------------
# clean_json
# ---------------------------------------------------------------------------

class TestCleanJson:
    def test_no_fence_passthrough(self):
        raw = '{"key": "value"}'
        assert shared.clean_json(raw) == '{"key": "value"}'

    def test_json_fence_stripped(self):
        raw = "```json\n{\"key\": \"value\"}\n```"
        result = shared.clean_json(raw)
        assert result == '{"key": "value"}'

    def test_plain_fence_stripped(self):
        raw = "```\n{\"key\": \"value\"}\n```"
        result = shared.clean_json(raw)
        assert result == '{"key": "value"}'

    def test_leading_trailing_whitespace(self):
        raw = "  \n```json\n{\"a\": 1}\n```\n  "
        result = shared.clean_json(raw)
        assert result == '{"a": 1}'

    def test_empty_string(self):
        assert shared.clean_json("") == ""

    def test_only_whitespace(self):
        assert shared.clean_json("   ") == ""

    def test_multiple_fences_only_outermost_stripped(self):
        """Only the first ``` line and last ``` are stripped."""
        raw = "```json\n```inner```\n```"
        result = shared.clean_json(raw)
        # Inner content preserved, outer fences removed
        assert "inner" in result

    def test_fence_without_closing(self):
        """If there's no closing fence, rsplit still works gracefully."""
        raw = "```json\n{\"key\": \"value\"}"
        result = shared.clean_json(raw)
        # Should at least drop the opening fence line
        assert "```json" not in result

    def test_valid_json_after_clean(self):
        raw = "```json\n{\"model_name\": \"Underwriting Risk Classification\"}\n```"
        result = shared.clean_json(raw)
        parsed = json.loads(result)
        assert parsed["model_name"] == "Underwriting Risk Classification"

    @pytest.mark.parametrize("raw,expected_contains", [
        ('{"key": "val"}', '"key"'),
        ('```json\n[1,2,3]\n```', "[1,2,3]"),
        ("```\nnull\n```", "null"),
    ])
    def test_parametrized_cases(self, raw, expected_contains):
        assert expected_contains in shared.clean_json(raw)


# ---------------------------------------------------------------------------
# call_claude
# ---------------------------------------------------------------------------

class TestCallClaude:
    def _make_mock_client(self, response_text: str):
        mock_content = MagicMock()
        mock_content.text = response_text

        mock_response = MagicMock()
        mock_response.content = [mock_content]

        mock_messages = MagicMock()
        mock_messages.create.return_value = mock_response

        mock_client = MagicMock()
        mock_client.messages = mock_messages
        return mock_client

    @patch("shared.anthropic.Anthropic")
    def test_happy_path_returns_text(self, mock_anthropic_cls):
        expected = "Hello from Claude"
        mock_anthropic_cls.return_value = self._make_mock_client(expected)

        result = shared.call_claude(system="You are helpful.", user="Say hello")
        assert result == expected

    @patch("shared.anthropic.Anthropic")
    def test_passes_correct_model(self, mock_anthropic_cls):
        mock_client = self._make_mock_client("ok")
        mock_anthropic_cls.return_value = mock_client

        shared.call_claude(system="sys", user="usr")
        _, kwargs = mock_client.messages.create.call_args
        assert kwargs.get("model") == shared.MODEL or mock_client.messages.create.call_args[1].get("model") == shared.MODEL

    @patch("shared.anthropic.Anthropic")
    def test_passes_system_and_user(self, mock_anthropic_cls):
        mock_client = self._make_mock_client("response")
        mock_anthropic_cls.return_value = mock_client

        shared.call_claude(system="my system", user="my user")
        create_kwargs = mock_client.messages.create.call_args[1]
        assert create_kwargs["system"] == "my system"
        assert create_kwargs["messages"][0]["role"] == "user"
        assert create_kwargs["messages"][0]["content"] == "my user"

    @patch("shared.anthropic.Anthropic")
    def test_default_max_tokens(self, mock_anthropic_cls):
        mock_client = self._make_mock_client("ok")
        mock_anthropic_cls.return_value = mock_client

        shared.call_claude(system="s", user="u")
        create_kwargs = mock_client.messages.create.call_args[1]
        assert create_kwargs["max_tokens"] == 4096

    @patch("shared.anthropic.Anthropic")
    def test_custom_max_tokens(self, mock_anthropic_cls):
        mock_client = self._make_mock_client("ok")
        mock_anthropic_cls.return_value = mock_client

        shared.call_claude(system="s", user="u", max_tokens=1024)
        create_kwargs = mock_client.messages.create.call_args[1]
        assert create_kwargs["max_tokens"] == 1024

    @patch("shared.anthropic.Anthropic")
    def test_api_key_passed(self, mock_anthropic_cls):
        mock_client = self._make_mock_client("ok")
        mock_anthropic_cls.return_value = mock_client

        shared.call_claude(system="s", user="u")
        mock_anthropic_cls.assert_called_once_with(api_key=FAKE_ENV["ANTHROPIC_API_KEY"])

    @patch("shared.anthropic.Anthropic")
    def test_json_response_cleaned_by_caller(self, mock_anthropic_cls):
        """call_claude returns raw text; caller is responsible for clean_json."""
        raw_with_fence = '```json\n{"model": "test"}\n```'
        mock_anthropic_cls.return_value = self._make_mock_client(raw_with_fence)

        result = shared.call_claude(system="s", user="u")
        assert result == raw_with_fence  # raw, not cleaned

    @patch("shared.anthropic.Anthropic")
    def test_anthropic_exception_propagates(self, mock_anthropic_cls):
        mock_client = MagicMock()
        mock_client.messages.create.side_effect = Exception("API Error")
        mock_anthropic_cls.return_value = mock_client

        with pytest.raises(Exception, match="API Error"):
            shared.call_claude(system="s", user="u")


# ---------------------------------------------------------------------------
# get_repo_files
# ---------------------------------------------------------------------------

class TestGetRepoFiles:
    def _tree_item(self, path: str, url: str = "https://api.github.com/blob/abc", blob_type: str = "blob") -> dict:
        return {"type": blob_type, "path": path, "url": url}

    @patch("shared.requests.get")
    def test_happy_path_filters_by_extension(self, mock_get):
        tree = _make_tree_response([
            self._tree_item("src/main.py"),
            self._tree_item("src/helper.py"),
            self._tree_item("README.md"),
        ])
        blob_py = _make_blob_response("print('hello')")
        blob_md = _make_blob_response("# README")

        def side_effect(url, headers):
            resp = MagicMock()
            if "trees" in url:
                resp.json.return_value = tree
            elif "README" in url or url.endswith("abc"):
                resp.json.return_value = blob_md
            else:
                resp.json.return_value = blob_py
            return resp

        mock_get.side_effect = side_effect

        # Only request .py files
        mock_get.reset_mock()

        # Re-setup side effect
        call_count = {"n": 0}

        def side_effect2(url, headers):
            resp = MagicMock()
            if "trees" in url:
                resp.json.return_value = tree
            else:
                resp.json.return_value = blob_py
            return resp

        mock_get.side_effect = side_effect2
        result = shared.get_repo_files("owner", "repo", [".py"])
        assert "src/main.py" in result
        assert "src/helper.py" in result
        assert "README.md" not in result

    @patch("shared.requests.get")
    def test_max_files_cap(self, mock_get):
        # Create 25 .py items but cap at 5
        items = [self._tree_item(f"file{i}.py") for i in range(25)]
        tree = _make_tree_response(items)
        blob = _make_blob_response("content")

        def side_effect(url, headers):
            resp = MagicMock()
            if "trees" in url:
                resp.json.return_value = tree
            else:
                resp.json.return_value = blob
            return resp

        mock_get.side_effect = side_effect
        result = shared.get_repo_files("owner", "repo", [".py"], max_files=5)
        assert len(result) == 5

    @patch("shared.requests.get")
    def test_default_max_files_20(self, mock_get):
        items = [self._tree_item(f"file{i}.py") for i in range(30)]
        tree = _make_tree_response(items)
        blob = _make_blob_response("content")

        def side_effect(url, headers):
            resp = MagicMock()
            if "trees" in url:
                resp.json.return_value = tree
            else:
                resp.json.return_value = blob
            return resp

        mock_get.side_effect = side_effect
        result = shared.get_repo_files("owner", "repo", [".py"])
        assert len(result) == 20

    @patch("shared.requests.get")
    def test_non_blob_items_skipped(self, mock_get):
        tree = _make_tree_response([
            {"type": "tree", "path": "src", "url": "..."},
            self._tree_item("src/main.py"),
        ])
        blob = _make_blob_response("code")

        def side_effect(url, headers):
            resp = MagicMock()
            if "trees" in url:
                resp.json.return_value = tree
            else:
                resp.json.return_value = blob
            return resp

        mock_get.side_effect = side_effect
        result = shared.get_repo_files("owner", "repo", [".py"])
        assert "src" not in result
        assert "src/main.py" in result

    @patch("shared.requests.get")
    def test_empty_repo_returns_empty_dict(self, mock_get):
        resp = MagicMock()
        resp.json.return_value = {"tree": []}
        mock_get.return_value = resp

        result = shared.get_repo_files("owner", "repo", [".py"])
        assert result == {}

    @patch("shared.requests.get")
    def test_bad_blob_content_skipped_silently(self, mock_get):
        tree = _make_tree_response([self._tree_item("bad.py")])

        def side_effect(url, headers):
            resp = MagicMock()
            if "trees" in url:
                resp.json.return_value = tree
            else:
                # Missing 'content' key → base64.b64decode will raise
                resp.json.return_value = {}
            return resp

        mock_get.side_effect = side_effect
        # Should not raise; just skip
        result = shared.get