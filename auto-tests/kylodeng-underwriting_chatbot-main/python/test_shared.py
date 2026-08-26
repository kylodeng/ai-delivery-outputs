"""
Test suite for .github/scripts/shared.py

What is tested:
    - call_claude(): Claude API invocation, response parsing
    - clean_json(): Markdown fence stripping, edge cases
    - get_repo_files(): GitHub tree fetching, extension filtering, max_files cap, decode errors
    - get_pr_diff(): PR diff fetching, truncation
    - write_output_file(): File creation (no SHA), file update (with SHA), URL fallback
    - post_pr_comment(): PR comment posting
    - send_email(): SendGrid payload construction, success/failure status codes
    - email_html(): HTML generation, SUCCESS/FAILURE colour logic, field interpolation
    - write_audit_entry(): Audit log construction (stub — incomplete source truncated)

Mocks used:
    - unittest.mock.patch / MagicMock for: anthropic.Anthropic, requests.get,
      requests.post, requests.put
    - Environment variables patched via monkeypatch / os.environ

TODOs:
    - write_audit_entry(): source is truncated; full behaviour cannot be verified
      without the complete implementation.
    - call_claude(): extended tests for network errors (anthropic SDK exceptions)
      require knowing the exact exception types raised by the SDK version in use.
"""

import base64
import json
import os
import sys
import types
import importlib
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Environment bootstrap — must happen BEFORE shared.py is imported because the
# module reads env vars at import time.
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
def _patch_env_for_import():
    """Patch environment before the module is first imported."""
    with patch.dict(os.environ, FAKE_ENV, clear=False):
        yield


# Import shared after env is patched
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".github", "scripts"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__)))

# We may be running from repo root or from the scripts directory; try both.
with patch.dict(os.environ, FAKE_ENV):
    try:
        import shared  # type: ignore
    except ModuleNotFoundError:
        # Attempt relative path when pytest is invoked from repo root
        spec_path = os.path.join(os.path.dirname(__file__), ".github", "scripts", "shared.py")
        spec = importlib.util.spec_from_file_location("shared", spec_path)
        shared = importlib.util.module_from_spec(spec)  # type: ignore
        spec.loader.exec_module(shared)  # type: ignore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _b64(text: str) -> str:
    """Return base64-encoded string as GitHub API would return it."""
    return base64.b64encode(text.encode()).decode()


# ---------------------------------------------------------------------------
# clean_json
# ---------------------------------------------------------------------------

class TestCleanJson:
    def test_plain_json_unchanged(self):
        raw = '{"key": "value"}'
        assert shared.clean_json(raw) == '{"key": "value"}'

    def test_strips_json_fence(self):
        raw = "```json\n{\"key\": \"value\"}\n```"
        result = shared.clean_json(raw)
        assert result == '{"key": "value"}'

    def test_strips_plain_fence(self):
        raw = "```\n{\"key\": \"value\"}\n```"
        result = shared.clean_json(raw)
        assert result == '{"key": "value"}'

    def test_strips_leading_trailing_whitespace(self):
        raw = "   \n{\"key\": \"value\"}\n   "
        assert shared.clean_json(raw) == '{"key": "value"}'

    def test_fence_with_extra_whitespace_inside(self):
        raw = "```json\n  {\"a\": 1}  \n```"
        result = shared.clean_json(raw)
        assert result == '{"a": 1}'

    def test_empty_string(self):
        assert shared.clean_json("") == ""

    def test_no_fence_multiline(self):
        raw = '{\n  "key": "value"\n}'
        assert shared.clean_json(raw) == '{\n  "key": "value"\n}'

    def test_fence_without_closing_backticks(self):
        """If there is no closing ``` the rsplit returns original content."""
        raw = "```json\n{\"key\": \"value\"}"
        result = shared.clean_json(raw)
        # rsplit on ``` with no match returns the whole string
        assert '{"key": "value"}' in result

    def test_synthetic_model_card_json(self):
        payload = json.dumps({"model_name": "Underwriting Risk Classification",
                              "model_type": "CatBoostClassifier"})
        fenced = f"```json\n{payload}\n```"
        cleaned = shared.clean_json(fenced)
        assert json.loads(cleaned)["model_name"] == "Underwriting Risk Classification"

    def test_synthetic_arabic_json(self):
        payload = json.dumps({"cancel": "\u0625\u0644\u063a\u0627\u0621"})
        fenced = f"```json\n{payload}\n```"
        cleaned = shared.clean_json(fenced)
        assert "\u0625\u0644\u063a\u0627\u0621" in cleaned

    @pytest.mark.parametrize("raw,expected", [
        ('{"a":1}', '{"a":1}'),
        ('  {"b":2}  ', '{"b":2}'),
        ("```\n[]\n```", "[]"),
        ("```json\nnull\n```", "null"),
    ])
    def test_parametrized_inputs(self, raw, expected):
        assert shared.clean_json(raw) == expected


# ---------------------------------------------------------------------------
# call_claude
# ---------------------------------------------------------------------------

class TestCallClaude:
    def _make_response(self, text: str):
        content_block = MagicMock()
        content_block.text = text
        response = MagicMock()
        response.content = [content_block]
        return response

    def test_returns_text_from_first_content_block(self):
        mock_client = MagicMock()
        mock_client.messages.create.return_value = self._make_response("Hello World")

        with patch("anthropic.Anthropic", return_value=mock_client):
            result = shared.call_claude("sys prompt", "user prompt")

        assert result == "Hello World"

    def test_passes_correct_arguments(self):
        mock_client = MagicMock()
        mock_client.messages.create.return_value = self._make_response("ok")

        with patch("anthropic.Anthropic", return_value=mock_client):
            shared.call_claude("my system", "my user", max_tokens=1024)

        mock_client.messages.create.assert_called_once_with(
            model=shared.MODEL,
            max_tokens=1024,
            system="my system",
            messages=[{"role": "user", "content": "my user"}],
        )

    def test_default_max_tokens(self):
        mock_client = MagicMock()
        mock_client.messages.create.return_value = self._make_response("ok")

        with patch("anthropic.Anthropic", return_value=mock_client):
            shared.call_claude("s", "u")

        _, kwargs = mock_client.messages.create.call_args
        assert kwargs["max_tokens"] == 4096

    def test_uses_configured_api_key(self):
        mock_client = MagicMock()
        mock_client.messages.create.return_value = self._make_response("ok")

        with patch("anthropic.Anthropic", return_value=mock_client) as mock_cls:
            shared.call_claude("s", "u")

        mock_cls.assert_called_once_with(api_key=shared.ANTHROPIC_API_KEY)

    def test_json_response_parseable(self):
        payload = json.dumps({"status": "SUCCESS", "summary": "All good"})
        mock_client = MagicMock()
        mock_client.messages.create.return_value = self._make_response(payload)

        with patch("anthropic.Anthropic", return_value=mock_client):
            raw = shared.call_claude("s", "u")

        data = json.loads(raw)
        assert data["status"] == "SUCCESS"

    def test_fenced_json_response_cleaned(self):
        """Simulate Claude wrapping JSON in fences — clean_json should handle it."""
        payload = json.dumps({"model_type": "CatBoostClassifier"})
        fenced = f"```json\n{payload}\n```"
        mock_client = MagicMock()
        mock_client.messages.create.return_value = self._make_response(fenced)

        with patch("anthropic.Anthropic", return_value=mock_client):
            raw = shared.call_claude("s", "u")

        cleaned = shared.clean_json(raw)
        assert json.loads(cleaned)["model_type"] == "CatBoostClassifier"


# ---------------------------------------------------------------------------
# get_repo_files
# ---------------------------------------------------------------------------

class TestGetRepoFiles:
    def _tree_response(self, items):
        resp = MagicMock()
        resp.json.return_value = {"tree": items}
        return resp

    def _blob_response(self, content: str):
        resp = MagicMock()
        resp.json.return_value = {"content": _b64(content)}
        return resp

    def _make_tree_item(self, path: str, url: str = "https://api.github.com/blob/abc"):
        return {"type": "blob", "path": path, "url": url}

    def test_returns_files_matching_extension(self):
        tree_items = [
            self._make_tree_item("src/main.py", "url1"),
            self._make_tree_item("README.md", "url2"),
            self._make_tree_item("src/util.py", "url3"),
        ]
        blob_py = self._blob_response("print('hello')")
        blob_md = self._blob_response("# README")

        def fake_get(url, headers=None):
            if "trees" in url:
                return self._tree_response(tree_items)
            if url == "url1":
                return blob_py
            if url == "url2":
                return blob_md
            if url == "url3":
                return self._blob_response("def util(): pass")
            raise ValueError(f"Unexpected URL: {url}")

        with patch("requests.get", side_effect=fake_get):
            files = shared.get_repo_files("owner", "repo", [".py"])

        assert "src/main.py" in files
        assert "src/util.py" in files
        assert "README.md" not in files

    def test_respects_max_files(self):
        tree_items = [self._make_tree_item(f"file{i}.py", f"url{i}") for i in range(10)]

        def fake_get(url, headers=None):
            if "trees" in url:
                return self._tree_response(tree_items)
            idx = int(url.replace("url", ""))
            return self._blob_response(f"content {idx}")

        with patch("requests.get", side_effect=fake_get):
            files = shared.get_repo_files("owner", "repo", [".py"], max_files=3)

        assert len(files) == 3

    def test_skips_non_blob_items(self):
        tree_items = [
            {"type": "tree", "path": "src/", "url": "url_tree"},
            self._make_tree_item("src/main.py", "url1"),
        ]

        def fake_get(url, headers=None):
            if "trees" in url:
                return self._tree_response(tree_items)
            return self._blob_response("code")

        with patch("requests.get", side_effect=fake_get):
            files = shared.get_repo_files("owner", "repo", [".py"])

        assert "src/" not in files
        assert "src/main.py" in files

    def test_empty_tree_returns_empty_dict(self):
        def fake_get(url, headers=None):
            resp = MagicMock()
            resp.json.return_value = {"tree": []}
            return resp

        with patch("requests.get", side_effect=fake_get):
            files = shared.get_repo_files("owner", "repo", [".py"])

        assert files == {}

    def test_multiple_extensions(self):
        tree_items = [
            self._make_tree_item("app.py", "u1"),
            self._make_tree_item("config.json", "u2"),
            self._make_tree_item("style.css", "u3"),
        ]

        def fake_get(url, headers=None):
            if "trees" in url:
                return self._tree_response(tree_items)
            return self._blob_response("data")

        with patch("requests.get", side_effect=fake_get):
            files = shared.get_repo_files("owner", "repo", [".py", ".json"])

        assert "app.py" in files
        assert "config.json" in files
        assert "style.css" not in files

    def test_decode_error_silently_skipped(self):
        """Files that raise during base64 decode should be silently skipped."""
        tree_items = [self._make_tree_item("bad.py", "u_bad")]

        def fake_get(url, headers=None):
            if "trees" in url:
                return self._tree_response(tree_items)
            resp = MagicMock()
            resp.json.return_value = {"content": "!!!not valid base64!!!@@@"}
            return resp

        with patch("requests.get", side_effect=fake_get):
            # Should not raise; bad files are skipped
            files = shared.get_repo_files("owner", "repo", [".py"])

        assert isinstance(files, dict)

    def test_missing_content_key_handled(self):
        tree_items = [self._make_tree_item("empty.py", "u_empty")]

        def fake_get(url, headers=None):
            if "trees" in url:
                return self._tree_response(tree_items)
            resp = MagicMock()
            resp.json.return_value = {}  # no "content" key
            return resp

        with patch("requests.get", side_effect=fake_get):
            files = shared.get_repo_files("owner", "repo", [".py"])

        assert isinstance(files, dict)

    def test_correct_api_url_constructed(self):
        calls = []

        def fake_get(url, headers=None):
            calls.append(url)
            resp = MagicMock()
            resp.json.return_value = {"tree": []}
            return resp

        with patch("requests.get", side_effect=fake_get):
            shared.get_repo_files("myowner", "myrepo", [".py"])

        assert calls[0] == f"{shared.GH_API}/repos/myowner/myrepo/git/trees/HEAD?