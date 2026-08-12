"""
Test suite for .github/scripts/shared.py

What is tested:
- call_claude(): Claude API invocation and response extraction
- clean_json(): Markdown fence stripping from JSON strings
- get_repo_files(): GitHub API tree traversal and file fetching with base64 decoding
- get_pr_diff(): GitHub API PR diff fetching and truncation
- write_output_file(): GitHub API file create/update with SHA detection
- post_pr_comment(): GitHub API PR comment posting
- send_email(): SendGrid API email dispatch and failure warning
- email_html(): HTML email body generation
- write_audit_entry(): Audit log construction and output repo writing

Mocks used:
- unittest.mock.patch for os.environ (to satisfy module-level env var reads)
- unittest.mock.MagicMock / patch for anthropic.Anthropic client
- unittest.mock.patch for requests.get, requests.post, requests.put
- unittest.mock.patch for base64.b64decode (selective)

TODOs:
- TODO: write_audit_entry full integration test needs real repo structure to verify
        appended JSON/Markdown content — stub provided below
- TODO: call_claude token limit / rate-limit error handling — not implemented in source
- TODO: get_repo_files pagination beyond max_files with real multi-page tree responses
"""

import base64
import datetime
import importlib
import sys
import types
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Helpers to import the module with required env vars injected
# ---------------------------------------------------------------------------

REQUIRED_ENV = {
    "ANTHROPIC_API_KEY": "test-anthropic-key",
    "GH_TOKEN": "test-gh-token",
    "SENDGRID_API_KEY": "test-sg-key",
    "OUTPUT_REPO": "ai-delivery-outputs",
    "OUTPUT_REPO_OWNER": "test-owner",
    "NOTIFY_EMAIL": "notify@example.com",
    "SENDER_EMAIL": "sender@example.com",
    "GITHUB_REPOSITORY_OWNER": "test-owner",
}


def _import_shared(extra_env=None):
    """Import (or re-import) shared with controlled environment."""
    env = {**REQUIRED_ENV, **(extra_env or {})}
    # Remove cached module so env vars are re-evaluated at module level
    sys.modules.pop("shared", None)
    # Also remove any previously cached version under the dotted path
    for key in list(sys.modules.keys()):
        if key.endswith("shared") and "scripts" in key:
            sys.modules.pop(key, None)
    with patch.dict("os.environ", env, clear=False):
        import importlib.util, pathlib, os
        spec = importlib.util.spec_from_file_location(
            "shared",
            pathlib.Path(__file__).parent.parent / ".github" / "scripts" / "shared.py",
        )
        mod = importlib.util.module_from_spec(spec)
        with patch.dict("os.environ", env, clear=False):
            spec.loader.exec_module(mod)
    return mod


# Import once for the majority of tests
@pytest.fixture(scope="module")
def shared():
    with patch.dict("os.environ", REQUIRED_ENV, clear=False):
        import importlib.util, pathlib
        spec = importlib.util.spec_from_file_location(
            "shared",
            pathlib.Path(__file__).parent.parent / ".github" / "scripts" / "shared.py",
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod


# ---------------------------------------------------------------------------
# clean_json
# ---------------------------------------------------------------------------

class TestCleanJson:
    def test_no_fences_unchanged(self, shared):
        raw = '{"key": "value"}'
        assert shared.clean_json(raw) == '{"key": "value"}'

    def test_strips_json_fence(self, shared):
        raw = '```json\n{"key": "value"}\n```'
        result = shared.clean_json(raw)
        assert result == '{"key": "value"}'

    def test_strips_plain_fence(self, shared):
        raw = '```\n{"key": "value"}\n```'
        result = shared.clean_json(raw)
        assert result == '{"key": "value"}'

    def test_leading_trailing_whitespace(self, shared):
        raw = '  ```json\n{"a": 1}\n```  '
        result = shared.clean_json(raw)
        assert result == '{"a": 1}'

    def test_multiline_json_preserved(self, shared):
        inner = '{\n  "model_name": "Underwriting Risk Classification",\n  "model_type": "CatBoostClassifier"\n}'
        raw = f"```json\n{inner}\n```"
        result = shared.clean_json(raw)
        assert result == inner

    def test_empty_string(self, shared):
        assert shared.clean_json("") == ""

    def test_only_whitespace(self, shared):
        assert shared.clean_json("   ") == ""

    def test_fence_without_closing(self, shared):
        # Should not raise; behaviour: drops opening line, no closing to strip
        raw = "```json\n{\"key\": \"val\"}"
        result = shared.clean_json(raw)
        assert '{"key": "val"}' in result

    def test_nested_backticks_inside_json(self, shared):
        raw = '{"code": "value without fences"}'
        assert shared.clean_json(raw) == raw

    @pytest.mark.parametrize("raw,expected", [
        ('```json\n[1,2,3]\n```', "[1,2,3]"),
        ('```\nnull\n```', "null"),
        ('"simple string"', '"simple string"'),
    ])
    def test_parametrized_cases(self, shared, raw, expected):
        assert shared.clean_json(raw) == expected


# ---------------------------------------------------------------------------
# call_claude
# ---------------------------------------------------------------------------

class TestCallClaude:
    def _mock_anthropic(self, shared, text_response="Hello"):
        mock_client = MagicMock()
        mock_message = MagicMock()
        mock_message.content = [MagicMock(text=text_response)]
        mock_client.messages.create.return_value = mock_message
        return mock_client

    def test_happy_path_returns_text(self, shared):
        mock_client = self._mock_anthropic(shared, "Generated text")
        with patch("anthropic.Anthropic", return_value=mock_client):
            result = shared.call_claude("system prompt", "user prompt")
        assert result == "Generated text"

    def test_passes_correct_model(self, shared):
        mock_client = self._mock_anthropic(shared)
        with patch("anthropic.Anthropic", return_value=mock_client):
            shared.call_claude("sys", "usr")
        _, kwargs = mock_client.messages.create.call_args
        assert kwargs["model"] == "claude-sonnet-4-6"

    def test_passes_system_and_user(self, shared):
        mock_client = self._mock_anthropic(shared)
        with patch("anthropic.Anthropic", return_value=mock_client):
            shared.call_claude("my system", "my user")
        _, kwargs = mock_client.messages.create.call_args
        assert kwargs["system"] == "my system"
        assert kwargs["messages"][0]["content"] == "my user"
        assert kwargs["messages"][0]["role"] == "user"

    def test_default_max_tokens(self, shared):
        mock_client = self._mock_anthropic(shared)
        with patch("anthropic.Anthropic", return_value=mock_client):
            shared.call_claude("s", "u")
        _, kwargs = mock_client.messages.create.call_args
        assert kwargs["max_tokens"] == 4096

    def test_custom_max_tokens(self, shared):
        mock_client = self._mock_anthropic(shared)
        with patch("anthropic.Anthropic", return_value=mock_client):
            shared.call_claude("s", "u", max_tokens=1024)
        _, kwargs = mock_client.messages.create.call_args
        assert kwargs["max_tokens"] == 1024

    def test_uses_api_key_from_env(self, shared):
        mock_client = self._mock_anthropic(shared)
        with patch("anthropic.Anthropic", return_value=mock_client) as mock_cls:
            shared.call_claude("s", "u")
        mock_cls.assert_called_once_with(api_key=shared.ANTHROPIC_API_KEY)

    def test_json_response_passthrough(self, shared):
        json_text = '{"key": "value", "list": [1, 2, 3]}'
        mock_client = self._mock_anthropic(shared, json_text)
        with patch("anthropic.Anthropic", return_value=mock_client):
            result = shared.call_claude("s", "u")
        assert result == json_text

    def test_anthropic_exception_propagates(self, shared):
        mock_client = MagicMock()
        mock_client.messages.create.side_effect = Exception("API Error")
        with patch("anthropic.Anthropic", return_value=mock_client):
            with pytest.raises(Exception, match="API Error"):
                shared.call_claude("s", "u")


# ---------------------------------------------------------------------------
# get_repo_files
# ---------------------------------------------------------------------------

class TestGetRepoFiles:
    def _make_tree_response(self, items):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"tree": items}
        return mock_resp

    def _make_blob_response(self, content_str):
        encoded = base64.b64encode(content_str.encode()).decode()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"content": encoded}
        return mock_resp

    def test_happy_path_single_file(self, shared):
        tree = [{"type": "blob", "path": "model_card.json", "url": "https://api.github.com/blob/abc"}]
        tree_resp = self._make_tree_response(tree)
        blob_resp = self._make_blob_response('{"model_name": "Underwriting Risk Classification"}')

        with patch("requests.get", side_effect=[tree_resp, blob_resp]):
            result = shared.get_repo_files("owner", "repo", [".json"])

        assert "model_card.json" in result
        assert "Underwriting Risk Classification" in result["model_card.json"]

    def test_filters_by_extension(self, shared):
        tree = [
            {"type": "blob", "path": "file.json", "url": "u1"},
            {"type": "blob", "path": "file.py", "url": "u2"},
            {"type": "blob", "path": "file.md", "url": "u3"},
        ]
        tree_resp = self._make_tree_response(tree)
        blob_resp = self._make_blob_response('{"key": "val"}')

        with patch("requests.get", side_effect=[tree_resp, blob_resp]) as mock_get:
            result = shared.get_repo_files("owner", "repo", [".json"])

        assert "file.json" in result
        assert "file.py" not in result
        assert "file.md" not in result

    def test_multiple_extensions(self, shared):
        tree = [
            {"type": "blob", "path": "a.json", "url": "u1"},
            {"type": "blob", "path": "b.py", "url": "u2"},
            {"type": "blob", "path": "c.txt", "url": "u3"},
        ]
        tree_resp = self._make_tree_response(tree)
        blob1 = self._make_blob_response("json content")
        blob2 = self._make_blob_response("python content")

        with patch("requests.get", side_effect=[tree_resp, blob1, blob2]):
            result = shared.get_repo_files("owner", "repo", [".json", ".py"])

        assert "a.json" in result
        assert "b.py" in result
        assert "c.txt" not in result

    def test_skips_non_blob_items(self, shared):
        tree = [
            {"type": "tree", "path": "somedir", "url": "u1"},
            {"type": "blob", "path": "file.json", "url": "u2"},
        ]
        tree_resp = self._make_tree_response(tree)
        blob_resp = self._make_blob_response('{}')

        with patch("requests.get", side_effect=[tree_resp, blob_resp]):
            result = shared.get_repo_files("owner", "repo", [".json"])

        assert "somedir" not in result
        assert "file.json" in result

    def test_respects_max_files_limit(self, shared):
        tree = [
            {"type": "blob", "path": f"file{i}.json", "url": f"u{i}"}
            for i in range(10)
        ]
        tree_resp = self._make_tree_response(tree)
        blob_resps = [self._make_blob_response(f'{{"i": {i}}}') for i in range(5)]

        with patch("requests.get", side_effect=[tree_resp] + blob_resps):
            result = shared.get_repo_files("owner", "repo", [".json"], max_files=5)

        assert len(result) == 5

    def test_empty_tree_returns_empty_dict(self, shared):
        tree_resp = MagicMock()
        tree_resp.json.return_value = {"tree": []}

        with patch("requests.get", return_value=tree_resp):
            result = shared.get_repo_files("owner", "repo", [".json"])

        assert result == {}

    def test_handles_decode_error_gracefully(self, shared):
        tree = [{"type": "blob", "path": "bad.json", "url": "u1"}]
        tree_resp = self._make_tree_response(tree)
        bad_blob = MagicMock()
        bad_blob.json.return_value = {"content": None}  # will raise on decode

        with patch("requests.get", side_effect=[tree_resp, bad_blob]):
            result = shared.get_repo_files("owner", "repo", [".json"])

        # Should not raise; file simply skipped
        assert "bad.json" not in result

    def test_constructs_correct_tree_url(self, shared):
        tree_resp = self._make_tree_response([])
        with patch("requests.get", return_value=tree_resp) as mock_get:
            shared.get_repo_files("myowner", "myrepo", [".py"])
        first_call_url = mock_get.call_args_list[0][0][0]
        assert "myowner" in first_call_url
        assert "myrepo" in first_call_url
        assert "recursive=1" in first_call_url

    def test_missing_tree_key_returns_empty(self, shared):
        tree_resp = MagicMock()
        tree_resp.json.return_value = {}  # no "tree" key

        with patch("requests.get", return_value=tree_resp):
            result = shared.get_repo_files("owner", "repo", [".json"])

        assert result == {}

    def test_unicode_content_decoded(self, shared):
        # Arabic content from synthetic data
        arabic = "إلغاء تأكيد متابعة"
        tree = [{"type": "blob", "path": "ar-SA.json", "url": "u1"}]
        tree_resp = self._make_tree_response(tree)
        blob_resp = self._make_blob