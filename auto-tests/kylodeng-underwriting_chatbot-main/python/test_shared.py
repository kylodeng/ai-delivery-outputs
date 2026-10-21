"""
Test suite for .github/scripts/shared.py

What is tested:
- call_claude: happy path, API error, response content extraction
- clean_json: strips markdown fences, handles plain JSON, edge cases
- get_repo_files: fetches and decodes files, respects extension filter, respects max_files limit, handles decode errors
- get_pr_diff: happy path, truncation at 30000 chars
- write_output_file: creates new file (no sha), updates existing file (with sha), fallback URL
- post_pr_comment: happy path, correct URL construction
- send_email: happy path (200), happy path (202), failure warning (non-200/202)
- email_html: SUCCESS status renders green, FAILURE status renders red, contains expected fields
- write_audit_entry: tested via mocked write_output_file (stub — see TODO)

Mocks used:
- unittest.mock.patch for requests.get, requests.post, requests.put
- unittest.mock.patch for anthropic.Anthropic (client + messages.create)
- os.environ patched via pytest monkeypatch / unittest.mock.patch.dict

TODOs:
- TODO: write_audit_entry full integration test requires access to the complete source (source is truncated)
- TODO: Tests for any additional helpers defined after `write_audit_entry` (source truncated)
"""

import base64
import json
import os
import sys
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Environment bootstrap — must happen BEFORE shared.py is imported, because
# the module reads env vars at import time.
# ---------------------------------------------------------------------------
ENV_DEFAULTS = {
    "ANTHROPIC_API_KEY": "test-anthropic-key",
    "GH_TOKEN": "test-gh-token",
    "SENDGRID_API_KEY": "test-sendgrid-key",
    "OUTPUT_REPO": "ai-delivery-outputs",
    "OUTPUT_REPO_OWNER": "test-owner",
    "NOTIFY_EMAIL": "notify@example.com",
    "SENDER_EMAIL": "sender@example.com",
    "GITHUB_REPOSITORY_OWNER": "test-gh-owner",
}

with patch.dict(os.environ, ENV_DEFAULTS, clear=False):
    # Ensure the scripts directory is on sys.path
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".github", "scripts"))
    import shared  # noqa: E402  (import after env patch)


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_module_globals():
    """Ensure module-level constants reflect our test env."""
    with patch.dict(os.environ, ENV_DEFAULTS, clear=False):
        yield


def _make_anthropic_response(text: str):
    """Build a minimal fake Anthropic response object."""
    content_block = MagicMock()
    content_block.text = text
    response = MagicMock()
    response.content = [content_block]
    return response


# ===========================================================================
# call_claude
# ===========================================================================

class TestCallClaude:
    """Tests for shared.call_claude"""

    def test_happy_path_returns_text(self):
        fake_response = _make_anthropic_response("Hello from Claude!")
        mock_client = MagicMock()
        mock_client.messages.create.return_value = fake_response

        with patch("shared.anthropic.Anthropic", return_value=mock_client):
            result = shared.call_claude(system="You are helpful.", user="Say hello.")

        assert result == "Hello from Claude!"
        mock_client.messages.create.assert_called_once_with(
            model=shared.MODEL,
            max_tokens=4096,
            system="You are helpful.",
            messages=[{"role": "user", "content": "Say hello."}],
        )

    def test_custom_max_tokens(self):
        fake_response = _make_anthropic_response("short")
        mock_client = MagicMock()
        mock_client.messages.create.return_value = fake_response

        with patch("shared.anthropic.Anthropic", return_value=mock_client):
            result = shared.call_claude(system="sys", user="usr", max_tokens=512)

        assert result == "short"
        _, kwargs = mock_client.messages.create.call_args
        assert kwargs["max_tokens"] == 512

    def test_api_key_passed_to_client(self):
        fake_response = _make_anthropic_response("ok")
        mock_client = MagicMock()
        mock_client.messages.create.return_value = fake_response

        with patch("shared.anthropic.Anthropic", return_value=mock_client) as mock_cls:
            shared.call_claude(system="s", user="u")

        mock_cls.assert_called_once_with(api_key=shared.ANTHROPIC_API_KEY)

    def test_api_exception_propagates(self):
        mock_client = MagicMock()
        mock_client.messages.create.side_effect = RuntimeError("API down")

        with patch("shared.anthropic.Anthropic", return_value=mock_client):
            with pytest.raises(RuntimeError, match="API down"):
                shared.call_claude(system="s", user="u")

    def test_empty_system_and_user(self):
        fake_response = _make_anthropic_response("")
        mock_client = MagicMock()
        mock_client.messages.create.return_value = fake_response

        with patch("shared.anthropic.Anthropic", return_value=mock_client):
            result = shared.call_claude(system="", user="")

        assert result == ""

    def test_unicode_content_returned(self):
        arabic_text = "إلغاء تأكيد متابعة"
        fake_response = _make_anthropic_response(arabic_text)
        mock_client = MagicMock()
        mock_client.messages.create.return_value = fake_response

        with patch("shared.anthropic.Anthropic", return_value=mock_client):
            result = shared.call_claude(system="s", user="u")

        assert result == arabic_text


# ===========================================================================
# clean_json
# ===========================================================================

class TestCleanJson:
    """Tests for shared.clean_json"""

    def test_plain_json_unchanged(self):
        raw = '{"key": "value"}'
        assert shared.clean_json(raw) == '{"key": "value"}'

    def test_strips_json_code_fence(self):
        raw = "```json\n{\"key\": \"value\"}\n```"
        result = shared.clean_json(raw)
        assert result == '{"key": "value"}'

    def test_strips_plain_code_fence(self):
        raw = "```\n{\"key\": \"value\"}\n```"
        result = shared.clean_json(raw)
        assert result == '{"key": "value"}'

    def test_strips_leading_trailing_whitespace(self):
        raw = "   {\"key\": \"value\"}   "
        assert shared.clean_json(raw) == '{"key": "value"}'

    def test_strips_fence_with_surrounding_whitespace(self):
        raw = "  ```json\n{\"a\": 1}\n```  "
        result = shared.clean_json(raw)
        assert result == '{"a": 1}'

    def test_multiline_json_in_fence(self):
        inner = '{\n  "model_name": "Underwriting Risk Classification",\n  "model_type": "CatBoostClassifier"\n}'
        raw = f"```json\n{inner}\n```"
        result = shared.clean_json(raw)
        assert result == inner.strip()

    def test_empty_string(self):
        assert shared.clean_json("") == ""

    def test_fence_without_closing_backticks(self):
        # Only opening fence — should still drop first line
        raw = "```json\n{\"a\": 1}"
        result = shared.clean_json(raw)
        # rsplit on ``` won't find closing → entire remainder returned
        assert '{"a": 1}' in result

    def test_valid_json_parseable_after_clean(self):
        raw = "```json\n{\"status\": \"SUCCESS\", \"count\": 42}\n```"
        cleaned = shared.clean_json(raw)
        parsed = json.loads(cleaned)
        assert parsed["status"] == "SUCCESS"
        assert parsed["count"] == 42

    def test_array_json_in_fence(self):
        raw = "```json\n[1, 2, 3]\n```"
        result = shared.clean_json(raw)
        assert result == "[1, 2, 3]"

    def test_no_mutation_on_plain_json(self):
        raw = '{"deep": {"finance": "You are a finance assessment agent"}}'
        assert shared.clean_json(raw) == raw


# ===========================================================================
# get_repo_files
# ===========================================================================

class TestGetRepoFiles:
    """Tests for shared.get_repo_files"""

    def _make_tree_response(self, paths):
        """Return a mock response whose .json() gives a git tree."""
        tree = [
            {"type": "blob", "path": p, "url": f"https://api.github.com/repos/o/r/git/blobs/{i}"}
            for i, p in enumerate(paths)
        ]
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"tree": tree}
        return mock_resp

    def _make_blob_response(self, text: str):
        encoded = base64.b64encode(text.encode()).decode()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"content": encoded + "\n"}
        return mock_resp

    def test_happy_path_single_extension(self):
        tree_resp = self._make_tree_response(["a.py", "b.py", "c.md"])
        blob_py = self._make_blob_response("print('hello')")

        with patch("shared.requests.get", side_effect=[tree_resp, blob_py, blob_py]) as mock_get:
            result = shared.get_repo_files("owner", "repo", [".py"])

        assert "a.py" in result
        assert "b.py" in result
        assert "c.md" not in result
        assert result["a.py"] == "print('hello')"

    def test_multiple_extensions_filter(self):
        tree_resp = self._make_tree_response(["model_card.json", "readme.md", "script.py", "data.csv"])
        blob_json = self._make_blob_response('{"model_name": "Underwriting Risk Classification"}')
        blob_md = self._make_blob_response("# Readme")
        blob_py = self._make_blob_response("import os")

        with patch("shared.requests.get", side_effect=[tree_resp, blob_json, blob_md, blob_py]):
            result = shared.get_repo_files("owner", "repo", [".json", ".md", ".py"])

        assert "model_card.json" in result
        assert "readme.md" in result
        assert "script.py" in result
        assert "data.csv" not in result

    def test_max_files_limit(self):
        paths = [f"file{i}.py" for i in range(10)]
        tree_resp = self._make_tree_response(paths)
        blob = self._make_blob_response("x = 1")

        with patch("shared.requests.get", side_effect=[tree_resp] + [blob] * 5):
            result = shared.get_repo_files("owner", "repo", [".py"], max_files=5)

        assert len(result) == 5

    def test_empty_tree(self):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"tree": []}

        with patch("shared.requests.get", return_value=mock_resp):
            result = shared.get_repo_files("owner", "repo", [".py"])

        assert result == {}

    def test_no_matching_extensions(self):
        tree_resp = self._make_tree_response(["file.txt", "image.png"])

        with patch("shared.requests.get", return_value=tree_resp):
            result = shared.get_repo_files("owner", "repo", [".py"])

        assert result == {}

    def test_non_blob_items_skipped(self):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "tree": [
                {"type": "tree", "path": "src", "url": "http://x"},
                {"type": "blob", "path": "src/app.py", "url": "http://y"},
            ]
        }
        blob = self._make_blob_response("code here")

        with patch("shared.requests.get", side_effect=[mock_resp, blob]):
            result = shared.get_repo_files("owner", "repo", [".py"])

        assert "src" not in result
        assert "src/app.py" in result

    def test_decode_error_skips_file(self):
        tree_resp = self._make_tree_response(["bad.py", "good.py"])
        # bad blob: missing 'content' key → base64.b64decode will raise
        bad_blob = MagicMock()
        bad_blob.json.return_value = {}  # no 'content' key
        good_blob = self._make_blob_response("good code")

        with patch("shared.requests.get", side_effect=[tree_resp, bad_blob, good_blob]):
            result = shared.get_repo_files("owner", "repo", [".py"])

        assert "bad.py" not in result
        assert "good.py" in result

    def test_correct_url_construction(self):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"tree": []}

        with patch("shared.requests.get", return_value=mock_resp) as mock_get:
            shared.get_repo_files("myowner", "myrepo", [".py"])

        call_url = mock_get.call_args[0][0]
        assert "myowner" in call_url
        assert "myrepo" in call_url
        assert "recursive=1" in call_url

    def test_utf8_content_decoded(self):
        arabic = "إلغاء"
        tree_resp = self._make_tree_response(["ar.json"])
        blob = self._make_blob_response(arabic)

        with patch("shared.requests.get", side_effect=[tree_resp, blob]):
            result = shared.get_repo_files("owner", "repo", [".json"])

        assert result["ar.json"] == arabic

    def test_max_files_zero(self):
        tree_resp = self._make_tree_response(["file.py"])

        with patch("shared.requests.get", return_value=tree_resp):
            result = shared.get_repo_files("owner", "repo", [".py"], max_files=0)

        assert result == {}


# ===========================================================================
# get_pr_diff
# ===========================================================================

class TestGetPrDiff:
    """Tests for shared.get_pr_diff"""

    def test_happy_path_returns_diff(self):
        mock_resp = MagicMock()
        mock_resp.text = "--- a/file.py\n+++ b/file.py\n@@ -1 +1 @@\n-old\n+new\n"

        with patch("shared.requests.get", return_value=mock_resp) as mock_get:
            result = shared.get_pr_diff("owner", "repo", 42)

        assert "--- a/file.py" in result
        mock_get.assert_called_once()
        call_url = mock_get