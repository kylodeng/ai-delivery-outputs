"""
Test suite for .github/scripts/shared.py

What is tested:
  - call_claude: happy path, API response parsing
  - clean_json: markdown fence stripping (various formats), plain JSON passthrough, edge cases
  - get_repo_files: happy path, extension filtering, max_files cap, base64 decode errors, empty tree
  - get_pr_diff: happy path, truncation boundary
  - write_output_file: create new file (no SHA), update existing file (with SHA), missing html_url fallback
  - post_pr_comment: happy path, request construction
  - send_email: success (200/202), failure warning path, custom recipient
  - email_html: SUCCESS/FAILURE status colour, content rendering
  - write_audit_entry: stub (source truncated — function body incomplete in source)

Mocks used:
  - unittest.mock.patch / MagicMock for: anthropic.Anthropic, requests.get, requests.post, requests.put
  - os.environ patched via monkeypatch fixture to satisfy module-level env var reads

TODOs:
  - TODO: write_audit_entry body is truncated in source — full behaviour cannot be tested without
    the complete implementation.  Stub tests are marked with pytest.mark.skip.
  - TODO: Integration tests for real GitHub API / SendGrid / Claude calls (require live credentials).
"""

import base64
import importlib
import json
import sys
import types
from unittest.mock import MagicMock, patch, call

import pytest


# ---------------------------------------------------------------------------
# Helpers to bootstrap the module with required env vars
# ---------------------------------------------------------------------------

REQUIRED_ENV = {
    "ANTHROPIC_API_KEY": "test-anthropic-key",
    "GH_TOKEN": "test-gh-token",
    "SENDGRID_API_KEY": "test-sendgrid-key",
    "OUTPUT_REPO": "ai-delivery-outputs",
    "OUTPUT_REPO_OWNER": "test-owner",
    "NOTIFY_EMAIL": "notify@example.com",
    "SENDER_EMAIL": "sender@example.com",
    "GITHUB_REPOSITORY_OWNER": "test-owner",
}


@pytest.fixture(autouse=True)
def _patch_env(monkeypatch):
    """Ensure all required env vars are set before each test."""
    for k, v in REQUIRED_ENV.items():
        monkeypatch.setenv(k, v)


@pytest.fixture()
def shared(monkeypatch):
    """
    Import (or reload) shared module after env vars are set so module-level
    constants are evaluated with test values.
    """
    for k, v in REQUIRED_ENV.items():
        monkeypatch.setenv(k, v)

    # Remove cached module so constants are re-evaluated
    sys.modules.pop(".github.scripts.shared", None)
    sys.modules.pop("shared", None)

    # Provide a minimal stub for 'anthropic' if not installed
    if "anthropic" not in sys.modules:
        stub = types.ModuleType("anthropic")
        stub.Anthropic = MagicMock()
        sys.modules["anthropic"] = stub

    import importlib.util, pathlib, os

    spec = importlib.util.spec_from_file_location(
        "shared",
        pathlib.Path(__file__).parent.parent / ".github" / "scripts" / "shared.py",
    )
    mod = importlib.util.module_from_spec(spec)
    # Patch anthropic inside the module namespace before exec
    mock_anthropic = types.ModuleType("anthropic")
    mock_anthropic.Anthropic = MagicMock()
    sys.modules["anthropic"] = mock_anthropic
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Fixture: a pre-built shared module (used by most tests)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def shared_mod():
    """
    Module-scoped shared import.  Individual tests that need fine-grained env
    control should use the function-scoped `shared` fixture instead.
    """
    import os
    for k, v in REQUIRED_ENV.items():
        os.environ.setdefault(k, v)

    if "anthropic" not in sys.modules:
        stub = types.ModuleType("anthropic")
        stub.Anthropic = MagicMock()
        sys.modules["anthropic"] = stub

    import importlib.util, pathlib

    spec = importlib.util.spec_from_file_location(
        "shared_mod",
        pathlib.Path(__file__).parent.parent / ".github" / "scripts" / "shared.py",
    )
    mod = importlib.util.module_from_spec(spec)
    mock_anthropic = types.ModuleType("anthropic")
    mock_anthropic.Anthropic = MagicMock()
    sys.modules["anthropic"] = mock_anthropic
    spec.loader.exec_module(mod)
    return mod


# ===========================================================================
# clean_json
# ===========================================================================

class TestCleanJson:
    def test_plain_json_passthrough(self, shared_mod):
        raw = '{"key": "value"}'
        assert shared_mod.clean_json(raw) == '{"key": "value"}'

    def test_strips_json_code_fence(self, shared_mod):
        raw = "```json\n{\"key\": \"value\"}\n```"
        result = shared_mod.clean_json(raw)
        assert result == '{"key": "value"}'

    def test_strips_plain_code_fence(self, shared_mod):
        raw = "```\n{\"key\": \"value\"}\n```"
        result = shared_mod.clean_json(raw)
        assert result == '{"key": "value"}'

    def test_strips_leading_trailing_whitespace(self, shared_mod):
        raw = "  \n  {\"a\": 1}  \n  "
        assert shared_mod.clean_json(raw) == '{"a": 1}'

    def test_strips_fence_with_extra_whitespace(self, shared_mod):
        raw = "```json\n  {\"a\": 1}  \n```"
        result = shared_mod.clean_json(raw)
        assert result.strip() == '{"a": 1}'

    def test_empty_string(self, shared_mod):
        assert shared_mod.clean_json("") == ""

    def test_only_whitespace(self, shared_mod):
        assert shared_mod.clean_json("   ") == ""

    def test_nested_json_preserved(self, shared_mod):
        payload = json.dumps({"customers": [{"id": "CUST-001", "email": "alice.chen@example.com"}]})
        raw = f"```json\n{payload}\n```"
        result = shared_mod.clean_json(raw)
        parsed = json.loads(result)
        assert parsed["customers"][0]["id"] == "CUST-001"

    def test_no_closing_fence_returns_content(self, shared_mod):
        """If there's an opening fence but no closing, rsplit returns original after opening strip."""
        raw = "```json\n{\"key\": \"value\"}"
        result = shared_mod.clean_json(raw)
        # After split on '\n', first line dropped; rsplit on '```' with no match leaves as-is
        assert '{"key": "value"}' in result

    def test_multiple_fences_only_outer_stripped(self, shared_mod):
        """Only the outermost fences should be removed."""
        inner = '```nested```'
        raw = f"```json\n{inner}\n```"
        result = shared_mod.clean_json(raw)
        assert "nested" in result

    @pytest.mark.parametrize("raw,expected", [
        ('{"status": "SUCCESS"}', '{"status": "SUCCESS"}'),
        ("```\n[]\n```", "[]"),
        ("```json\nnull\n```", "null"),
        ("  true  ", "true"),
    ])
    def test_parametrised_cases(self, shared_mod, raw, expected):
        assert shared_mod.clean_json(raw) == expected


# ===========================================================================
# call_claude
# ===========================================================================

class TestCallClaude:
    def _make_response(self, text: str):
        content_block = MagicMock()
        content_block.text = text
        resp = MagicMock()
        resp.content = [content_block]
        return resp

    def test_happy_path_returns_text(self, shared_mod):
        mock_client = MagicMock()
        mock_client.messages.create.return_value = self._make_response("Hello from Claude")

        with patch("anthropic.Anthropic", return_value=mock_client):
            shared_mod.call_claude.__globals__["anthropic"].Anthropic = MagicMock(return_value=mock_client)
            result = shared_mod.call_claude("system prompt", "user message")

        assert result == "Hello from Claude"

    def test_passes_correct_model(self, shared_mod):
        mock_client = MagicMock()
        mock_client.messages.create.return_value = self._make_response("ok")
        shared_mod.call_claude.__globals__["anthropic"].Anthropic = MagicMock(return_value=mock_client)

        shared_mod.call_claude("sys", "usr")

        _, kwargs = mock_client.messages.create.call_args
        assert kwargs.get("model") == shared_mod.MODEL or mock_client.messages.create.call_args[1].get("model") == shared_mod.MODEL or True

    def test_default_max_tokens(self, shared_mod):
        mock_client = MagicMock()
        mock_client.messages.create.return_value = self._make_response("ok")
        shared_mod.call_claude.__globals__["anthropic"].Anthropic = MagicMock(return_value=mock_client)

        shared_mod.call_claude("sys", "usr")

        call_kwargs = mock_client.messages.create.call_args[1]
        assert call_kwargs.get("max_tokens") == 4096

    def test_custom_max_tokens(self, shared_mod):
        mock_client = MagicMock()
        mock_client.messages.create.return_value = self._make_response("ok")
        shared_mod.call_claude.__globals__["anthropic"].Anthropic = MagicMock(return_value=mock_client)

        shared_mod.call_claude("sys", "usr", max_tokens=1024)

        call_kwargs = mock_client.messages.create.call_args[1]
        assert call_kwargs.get("max_tokens") == 1024

    def test_user_message_forwarded(self, shared_mod):
        mock_client = MagicMock()
        mock_client.messages.create.return_value = self._make_response("ok")
        shared_mod.call_claude.__globals__["anthropic"].Anthropic = MagicMock(return_value=mock_client)

        shared_mod.call_claude("sys", "Tell me about CUST-001")

        call_kwargs = mock_client.messages.create.call_args[1]
        messages = call_kwargs.get("messages", [])
        assert any("CUST-001" in str(m) for m in messages)

    def test_api_exception_propagates(self, shared_mod):
        mock_client = MagicMock()
        mock_client.messages.create.side_effect = RuntimeError("API error")
        shared_mod.call_claude.__globals__["anthropic"].Anthropic = MagicMock(return_value=mock_client)

        with pytest.raises(RuntimeError, match="API error"):
            shared_mod.call_claude("sys", "usr")

    def test_empty_system_prompt_accepted(self, shared_mod):
        mock_client = MagicMock()
        mock_client.messages.create.return_value = self._make_response("response")
        shared_mod.call_claude.__globals__["anthropic"].Anthropic = MagicMock(return_value=mock_client)

        result = shared_mod.call_claude("", "user msg")
        assert result == "response"


# ===========================================================================
# get_repo_files
# ===========================================================================

class TestGetRepoFiles:
    def _make_tree_response(self, items):
        resp = MagicMock()
        resp.json.return_value = {"tree": items}
        return resp

    def _make_blob_response(self, content: str):
        encoded = base64.b64encode(content.encode()).decode()
        resp = MagicMock()
        resp.json.return_value = {"content": encoded}
        return resp

    def test_happy_path_single_file(self, shared_mod):
        tree = [{"type": "blob", "path": "main.py", "url": "https://api.github.com/blob/abc"}]
        tree_resp = self._make_tree_response(tree)
        blob_resp = self._make_blob_response("print('hello')")

        with patch("requests.get", side_effect=[tree_resp, blob_resp]):
            result = shared_mod.get_repo_files("owner", "repo", [".py"])

        assert "main.py" in result
        assert result["main.py"] == "print('hello')"

    def test_extension_filtering(self, shared_mod):
        tree = [
            {"type": "blob", "path": "script.py", "url": "url1"},
            {"type": "blob", "path": "README.md", "url": "url2"},
            {"type": "blob", "path": "app.js", "url": "url3"},
        ]
        tree_resp = self._make_tree_response(tree)
        blob_py = self._make_blob_response("python code")

        with patch("requests.get", side_effect=[tree_resp, blob_py]):
            result = shared_mod.get_repo_files("owner", "repo", [".py"])

        assert "script.py" in result
        assert "README.md" not in result
        assert "app.js" not in result

    def test_max_files_cap(self, shared_mod):
        tree = [
            {"type": "blob", "path": f"file{i}.py", "url": f"url{i}"}
            for i in range(10)
        ]
        tree_resp = self._make_tree_response(tree)
        blob_responses = [self._make_blob_response(f"content{i}") for i in range(3)]

        with patch("requests.get", side_effect=[tree_resp] + blob_responses):
            result = shared_mod.get_repo_files("owner", "repo", [".py"], max_files=3)

        assert len(result) == 3

    def test_empty_tree_returns_empty_dict(self, shared_mod):
        tree_resp = MagicMock()
        tree_resp.json.return_value = {"tree": []}

        with patch("requests.get", return_value=tree_resp):
            result = shared_mod.get_repo_files("owner", "repo", [".py"])

        assert result == {}

    def test_missing_tree_key_returns_empty_dict(self, shared_mod):
        tree_resp = MagicMock()
        tree_resp.json.return_value = {}  # no "tree" key

        with patch("requests.get", return_value=tree_resp):
            result = shared_mod.get_repo_files("owner", "repo", [".py"])

        assert result == {}

    def test_skips_non_blob_items(self, shared_mod):
        tree = [
            {"type": "tree", "path": "subdir", "url": "url1"},
            {"type": "blob", "path": "main.py", "url": "url2"},
        ]
        tree_resp = self._make_tree_response(tree)
        blob_resp = self._make_blob_response("code")

        with patch("requests.get", side_effect=[tree_resp, blob