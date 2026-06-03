"""
Test suite for .github/scripts/shared.py

What is tested:
- call_claude(): Claude API interaction, response extraction
- clean_json(): Markdown fence stripping, edge cases
- get_repo_files(): GitHub tree API traversal, base64 decoding, extension filtering, max_files limit
- get_pr_diff(): PR diff fetching, truncation
- write_output_file(): File create (no SHA) and update (with SHA) paths
- post_pr_comment(): PR comment posting
- send_email(): SendGrid integration, status code handling
- email_html(): HTML template generation
- write_audit_entry(): Audit log entry construction (partial — source truncated)

Mocks used:
- unittest.mock.patch for requests.get, requests.post, requests.put
- unittest.mock.MagicMock / patch for anthropic.Anthropic client
- os.environ patched via monkeypatch / patch.dict

TODOs:
- TODO: write_audit_entry full coverage requires complete source (source was truncated)
- TODO: get_repo_files UTF-8 error replacement path needs a blob with invalid bytes
"""

import base64
import datetime
import json
import os
import sys
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Ensure environment variables exist before the module-level code in shared.py
# executes during import.
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
    # Insert the scripts directory so the module can be imported
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".github", "scripts"))
    # Also try a relative path for when tests run from repo root
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".github", "scripts"))
    import shared  # noqa: E402


# ===========================================================================
# Fixtures
# ===========================================================================

@pytest.fixture(autouse=True)
def reset_module_globals(monkeypatch):
    """Keep module-level constants consistent across tests."""
    monkeypatch.setattr(shared, "OUTPUT_REPO", "ai-delivery-outputs")
    monkeypatch.setattr(shared, "OUTPUT_REPO_OWNER", "test-owner")
    monkeypatch.setattr(shared, "NOTIFY_EMAIL", "notify@example.com")
    monkeypatch.setattr(shared, "SENDER_EMAIL", "sender@example.com")
    monkeypatch.setattr(shared, "SENDGRID_API_KEY", "test-sendgrid-key")
    monkeypatch.setattr(shared, "GH_TOKEN", "test-gh-token")


# ===========================================================================
# call_claude
# ===========================================================================

class TestCallClaude:
    """Tests for the call_claude() function."""

    def _make_response(self, text: str):
        """Build a mock anthropic response object."""
        content_block = MagicMock()
        content_block.text = text
        response = MagicMock()
        response.content = [content_block]
        return response

    @patch("shared.anthropic.Anthropic")
    def test_happy_path_returns_text(self, mock_anthropic_cls):
        """call_claude returns the text from the first content block."""
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client
        mock_client.messages.create.return_value = self._make_response("Hello from Claude")

        result = shared.call_claude("You are helpful.", "Say hello.")

        assert result == "Hello from Claude"

    @patch("shared.anthropic.Anthropic")
    def test_passes_correct_model_and_tokens(self, mock_anthropic_cls):
        """call_claude forwards model, max_tokens, system and user message."""
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client
        mock_client.messages.create.return_value = self._make_response("ok")

        shared.call_claude("system prompt", "user prompt", max_tokens=1024)

        mock_client.messages.create.assert_called_once_with(
            model=shared.MODEL,
            max_tokens=1024,
            system="system prompt",
            messages=[{"role": "user", "content": "user prompt"}],
        )

    @patch("shared.anthropic.Anthropic")
    def test_default_max_tokens_is_4096(self, mock_anthropic_cls):
        """Default max_tokens should be 4096."""
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client
        mock_client.messages.create.return_value = self._make_response("ok")

        shared.call_claude("sys", "usr")
        _, kwargs = mock_client.messages.create.call_args
        assert kwargs["max_tokens"] == 4096

    @patch("shared.anthropic.Anthropic")
    def test_api_key_passed_to_client(self, mock_anthropic_cls):
        """Anthropic client is initialised with the module-level API key."""
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client
        mock_client.messages.create.return_value = self._make_response("ok")

        shared.call_claude("s", "u")
        mock_anthropic_cls.assert_called_once_with(api_key=shared.ANTHROPIC_API_KEY)

    @patch("shared.anthropic.Anthropic")
    def test_empty_response_text(self, mock_anthropic_cls):
        """call_claude handles an empty string response gracefully."""
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client
        mock_client.messages.create.return_value = self._make_response("")

        result = shared.call_claude("s", "u")
        assert result == ""

    @patch("shared.anthropic.Anthropic")
    def test_large_response_text(self, mock_anthropic_cls):
        """call_claude returns the full text regardless of length."""
        large_text = "x" * 100_000
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client
        mock_client.messages.create.return_value = self._make_response(large_text)

        result = shared.call_claude("s", "u")
        assert result == large_text

    @patch("shared.anthropic.Anthropic")
    def test_api_exception_propagates(self, mock_anthropic_cls):
        """call_claude lets exceptions from the Anthropic client propagate."""
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client
        mock_client.messages.create.side_effect = RuntimeError("quota exceeded")

        with pytest.raises(RuntimeError, match="quota exceeded"):
            shared.call_claude("s", "u")


# ===========================================================================
# clean_json
# ===========================================================================

class TestCleanJson:
    """Tests for the clean_json() function."""

    def test_no_fences_unchanged(self):
        raw = '{"key": "value"}'
        assert shared.clean_json(raw) == '{"key": "value"}'

    def test_strips_json_fence(self):
        raw = '```json\n{"key": "value"}\n```'
        result = shared.clean_json(raw)
        assert result == '{"key": "value"}'

    def test_strips_generic_code_fence(self):
        raw = '```\n{"key": "value"}\n```'
        result = shared.clean_json(raw)
        assert result == '{"key": "value"}'

    def test_strips_leading_and_trailing_whitespace(self):
        raw = '  \n```json\n{"a":1}\n```  \n'
        result = shared.clean_json(raw)
        assert result == '{"a":1}'

    def test_empty_string(self):
        assert shared.clean_json("") == ""

    def test_whitespace_only(self):
        assert shared.clean_json("   ") == ""

    def test_fence_with_extra_whitespace_inside(self):
        raw = '```json\n  {"spaced": true}  \n```'
        result = shared.clean_json(raw)
        assert result == '{"spaced": true}'

    def test_nested_backticks_not_stripped(self):
        """Only the outermost fence pair should be removed."""
        raw = '```json\n{"code": "use \\`\\`\\` here"}\n```'
        result = shared.clean_json(raw)
        # Should contain valid JSON content, not the outer fence markers
        assert "```" not in result.split("\n")[0]

    def test_model_card_json_sample(self):
        """Synthetic data: model card JSON wrapped in a fence."""
        payload = '{"model_name": "Underwriting Risk Classification", "model_type": "CatBoostClassifier"}'
        raw = f"```json\n{payload}\n```"
        result = shared.clean_json(raw)
        parsed = json.loads(result)
        assert parsed["model_name"] == "Underwriting Risk Classification"

    def test_multiline_json_preserved(self):
        raw = '```json\n{\n  "a": 1,\n  "b": 2\n}\n```'
        result = shared.clean_json(raw)
        parsed = json.loads(result)
        assert parsed == {"a": 1, "b": 2}

    @pytest.mark.parametrize("raw,expected", [
        ('{"x":1}', '{"x":1}'),
        ('```\n[1,2,3]\n```', '[1,2,3]'),
        ('```json\n"string"\n```', '"string"'),
    ])
    def test_parametrised_cases(self, raw, expected):
        assert shared.clean_json(raw) == expected


# ===========================================================================
# get_repo_files
# ===========================================================================

class TestGetRepoFiles:
    """Tests for get_repo_files()."""

    def _make_tree_response(self, items):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"tree": items}
        return mock_resp

    def _make_blob_response(self, content: str):
        encoded = base64.b64encode(content.encode()).decode()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"content": encoded + "\n"}
        return mock_resp

    @patch("shared.requests.get")
    def test_happy_path_returns_file_content(self, mock_get):
        tree_items = [
            {"type": "blob", "path": "src/main.py", "url": "https://api.github.com/blob/abc"},
        ]
        mock_get.side_effect = [
            self._make_tree_response(tree_items),
            self._make_blob_response("print('hello')"),
        ]

        result = shared.get_repo_files("owner", "repo", [".py"])

        assert "src/main.py" in result
        assert result["src/main.py"] == "print('hello')"

    @patch("shared.requests.get")
    def test_filters_by_extension(self, mock_get):
        tree_items = [
            {"type": "blob", "path": "readme.md", "url": "url1"},
            {"type": "blob", "path": "app.py", "url": "url2"},
        ]
        mock_get.side_effect = [
            self._make_tree_response(tree_items),
            self._make_blob_response("# python"),
        ]

        result = shared.get_repo_files("owner", "repo", [".py"])

        assert "app.py" in result
        assert "readme.md" not in result

    @patch("shared.requests.get")
    def test_multiple_extensions(self, mock_get):
        tree_items = [
            {"type": "blob", "path": "a.py", "url": "url1"},
            {"type": "blob", "path": "b.js", "url": "url2"},
            {"type": "blob", "path": "c.txt", "url": "url3"},
        ]
        mock_get.side_effect = [
            self._make_tree_response(tree_items),
            self._make_blob_response("python"),
            self._make_blob_response("javascript"),
        ]

        result = shared.get_repo_files("owner", "repo", [".py", ".js"])

        assert "a.py" in result
        assert "b.js" in result
        assert "c.txt" not in result

    @patch("shared.requests.get")
    def test_max_files_limit_respected(self, mock_get):
        tree_items = [
            {"type": "blob", "path": f"file{i}.py", "url": f"url{i}"}
            for i in range(10)
        ]
        mock_get.side_effect = (
            [self._make_tree_response(tree_items)]
            + [self._make_blob_response(f"content{i}") for i in range(3)]
        )

        result = shared.get_repo_files("owner", "repo", [".py"], max_files=3)

        assert len(result) == 3

    @patch("shared.requests.get")
    def test_skips_non_blob_items(self, mock_get):
        tree_items = [
            {"type": "tree", "path": "src/", "url": "url1"},
            {"type": "blob", "path": "main.py", "url": "url2"},
        ]
        mock_get.side_effect = [
            self._make_tree_response(tree_items),
            self._make_blob_response("code"),
        ]

        result = shared.get_repo_files("owner", "repo", [".py"])

        assert "src/" not in result
        assert "main.py" in result

    @patch("shared.requests.get")
    def test_empty_repo_returns_empty_dict(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"tree": []}
        mock_get.return_value = mock_resp

        result = shared.get_repo_files("owner", "repo", [".py"])

        assert result == {}

    @patch("shared.requests.get")
    def test_blob_decode_exception_skipped(self, mock_get):
        """Files that fail to decode are silently skipped."""
        tree_items = [
            {"type": "blob", "path": "broken.py", "url": "url1"},
        ]
        bad_blob_resp = MagicMock()
        bad_blob_resp.json.return_value = {}  # no "content" key → KeyError
        mock_get.side_effect = [
            self._make_tree_response(tree_items),
            bad_blob_resp,
        ]

        result = shared.get_repo_files("owner", "repo", [".py"])

        assert result == {}

    @patch("shared.requests.get")
    def test_correct_url_constructed(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"tree": []}
        mock_get.return_value = mock_resp

        shared.get_