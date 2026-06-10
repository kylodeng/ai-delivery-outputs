"""
Test suite for .github/scripts/shared.py

What is tested:
- call_claude(): Claude API invocation, response extraction
- clean_json(): markdown fence stripping, edge cases
- get_repo_files(): GitHub tree fetching, extension filtering, max_files limit, decode errors
- get_pr_diff(): PR diff fetching, truncation
- write_output_file(): create new file, update existing file (with SHA), fallback URL
- post_pr_comment(): PR comment posting
- send_email(): SendGrid happy path, failure warning
- email_html(): HTML output structure, status color logic
- write_audit_entry(): audit log construction (partial — source truncated)

Mocks used:
- unittest.mock.patch for os.environ (prevents KeyError on import)
- unittest.mock.MagicMock / patch for requests.get, requests.post, requests.put
- unittest.mock.MagicMock for anthropic.Anthropic client

TODOs:
- TODO: write_audit_entry full test requires seeing the rest of the function body (source truncated)
- TODO: Integration test for actual Claude model response format verification
- TODO: Test thread-safety / concurrent calls to write_output_file
"""

import base64
import importlib
import json
import sys
import types
from unittest import mock
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Environment bootstrap — must happen BEFORE shared.py is imported
# ---------------------------------------------------------------------------

ENV_DEFAULTS = {
    "ANTHROPIC_API_KEY": "test-anthropic-key",
    "GH_TOKEN": "test-gh-token",
    "SENDGRID_API_KEY": "test-sendgrid-key",
    "OUTPUT_REPO": "ai-delivery-outputs",
    "OUTPUT_REPO_OWNER": "test-owner",
    "GITHUB_REPOSITORY_OWNER": "test-owner",
    "NOTIFY_EMAIL": "notify@example.com",
    "SENDER_EMAIL": "sender@example.com",
}


@pytest.fixture(autouse=True, scope="session")
def _patch_env_for_import():
    """Patch environment variables before the module is imported."""
    with mock.patch.dict("os.environ", ENV_DEFAULTS, clear=False):
        yield


# We need to import (or re-import) shared after env is patched.
# Use a session-scoped fixture to load it once.
@pytest.fixture(scope="session")
def shared_module(_patch_env_for_import):
    with mock.patch.dict("os.environ", ENV_DEFAULTS, clear=False):
        # Stub out anthropic so we don't need the real package at import time
        fake_anthropic = types.ModuleType("anthropic")
        fake_anthropic.Anthropic = MagicMock()
        sys.modules.setdefault("anthropic", fake_anthropic)

        if "shared" in sys.modules:
            del sys.modules["shared"]

        # Add the script directory to path
        import importlib.util, os
        script_path = os.path.join(
            os.path.dirname(__file__), ".github", "scripts", "shared.py"
        )
        # Fall back to current directory layout used in CI
        if not os.path.exists(script_path):
            script_path = os.path.join(
                os.path.dirname(os.path.abspath(__file__)),
                ".github", "scripts", "shared.py",
            )

        spec = importlib.util.spec_from_file_location("shared", script_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module


# Convenience alias so individual tests can just request `sh`
@pytest.fixture()
def sh(shared_module):
    return shared_module


# ---------------------------------------------------------------------------
# Helper builders
# ---------------------------------------------------------------------------

def _make_response(status_code=200, json_data=None, text=""):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data if json_data is not None else {}
    resp.text = text
    return resp


# ===========================================================================
# clean_json
# ===========================================================================

class TestCleanJson:
    def test_no_fences_unchanged(self, sh):
        raw = '{"key": "value"}'
        assert sh.clean_json(raw) == '{"key": "value"}'

    def test_strips_json_code_fence(self, sh):
        raw = "```json\n{\"key\": \"value\"}\n```"
        result = sh.clean_json(raw)
        assert result == '{"key": "value"}'

    def test_strips_plain_code_fence(self, sh):
        raw = "```\n{\"key\": \"value\"}\n```"
        result = sh.clean_json(raw)
        assert result == '{"key": "value"}'

    def test_strips_leading_trailing_whitespace(self, sh):
        raw = "   \n{\"key\": \"value\"}\n   "
        result = sh.clean_json(raw)
        assert result == '{"key": "value"}'

    def test_fence_with_extra_whitespace_inside(self, sh):
        raw = "```json\n  {\"a\": 1}  \n```"
        result = sh.clean_json(raw)
        assert result == '{"a": 1}'

    def test_empty_string(self, sh):
        assert sh.clean_json("") == ""

    def test_only_fences_no_content(self, sh):
        raw = "```json\n```"
        result = sh.clean_json(raw)
        assert result == ""

    def test_multiline_json(self, sh):
        inner = '{\n  "a": 1,\n  "b": 2\n}'
        raw = f"```json\n{inner}\n```"
        result = sh.clean_json(raw)
        assert result == inner

    def test_no_closing_fence_leaves_content(self, sh):
        """If there's no closing fence, rsplit returns the original tail."""
        raw = "```json\n{\"key\": \"value\"}"
        result = sh.clean_json(raw)
        # After split on first newline we get '{"key": "value"}'
        # rsplit on ``` finds nothing, returns same string
        assert '{"key": "value"}' in result

    def test_valid_json_after_clean(self, sh):
        raw = "```json\n{\"product_name\": \"Generations II\", \"doc_type\": \"product_brochure\"}\n```"
        result = sh.clean_json(raw)
        parsed = json.loads(result)
        assert parsed["product_name"] == "Generations II"

    @pytest.mark.parametrize("raw,expected_contains", [
        ('{"doc_type": "supplementary"}', "supplementary"),
        ("```\n[1,2,3]\n```", "[1,2,3]"),
        ("  plain text  ", "plain text"),
    ])
    def test_parametrized_inputs(self, sh, raw, expected_contains):
        assert expected_contains in sh.clean_json(raw)


# ===========================================================================
# call_claude
# ===========================================================================

class TestCallClaude:
    def _make_claude_client(self, text_response="Hello from Claude"):
        fake_content = MagicMock()
        fake_content.text = text_response
        fake_message = MagicMock()
        fake_message.content = [fake_content]
        fake_client = MagicMock()
        fake_client.messages.create.return_value = fake_message
        return fake_client

    def test_returns_text_from_first_content_block(self, sh):
        fake_client = self._make_claude_client("response text")
        with patch("anthropic.Anthropic", return_value=fake_client):
            sh_anthropic_patcher = patch.object(
                sys.modules["anthropic"], "Anthropic", return_value=fake_client
            )
            with sh_anthropic_patcher:
                # Patch at the module level where it's used
                with patch.object(type(sh).__module__,
                                  "anthropic",
                                  create=True) as _:
                    pass

        # Direct approach: mock the client constructor inside shared module
        with patch.object(sh, "anthropic") as mock_anthropic_mod:
            mock_anthropic_mod.Anthropic.return_value = fake_client
            result = sh.call_claude("system prompt", "user message")
        assert result == "response text"

    def test_passes_correct_model(self, sh):
        fake_client = self._make_claude_client("ok")
        with patch.object(sh, "anthropic") as mock_anthropic_mod:
            mock_anthropic_mod.Anthropic.return_value = fake_client
            sh.call_claude("sys", "usr")
        call_kwargs = fake_client.messages.create.call_args
        assert call_kwargs.kwargs["model"] == sh.MODEL

    def test_passes_system_and_user(self, sh):
        fake_client = self._make_claude_client("ok")
        with patch.object(sh, "anthropic") as mock_anthropic_mod:
            mock_anthropic_mod.Anthropic.return_value = fake_client
            sh.call_claude("my system", "my user")
        call_kwargs = fake_client.messages.create.call_args
        assert call_kwargs.kwargs["system"] == "my system"
        messages = call_kwargs.kwargs["messages"]
        assert messages[0]["role"] == "user"
        assert messages[0]["content"] == "my user"

    def test_default_max_tokens(self, sh):
        fake_client = self._make_claude_client("ok")
        with patch.object(sh, "anthropic") as mock_anthropic_mod:
            mock_anthropic_mod.Anthropic.return_value = fake_client
            sh.call_claude("s", "u")
        call_kwargs = fake_client.messages.create.call_args
        assert call_kwargs.kwargs["max_tokens"] == 4096

    def test_custom_max_tokens(self, sh):
        fake_client = self._make_claude_client("ok")
        with patch.object(sh, "anthropic") as mock_anthropic_mod:
            mock_anthropic_mod.Anthropic.return_value = fake_client
            sh.call_claude("s", "u", max_tokens=1024)
        call_kwargs = fake_client.messages.create.call_args
        assert call_kwargs.kwargs["max_tokens"] == 1024

    def test_uses_api_key_from_module(self, sh):
        fake_client = self._make_claude_client("ok")
        with patch.object(sh, "anthropic") as mock_anthropic_mod:
            mock_anthropic_mod.Anthropic.return_value = fake_client
            sh.call_claude("s", "u")
        mock_anthropic_mod.Anthropic.assert_called_once_with(
            api_key=sh.ANTHROPIC_API_KEY
        )

    def test_empty_response_content(self, sh):
        """Edge case: empty string from Claude."""
        fake_client = self._make_claude_client("")
        with patch.object(sh, "anthropic") as mock_anthropic_mod:
            mock_anthropic_mod.Anthropic.return_value = fake_client
            result = sh.call_claude("s", "u")
        assert result == ""

    def test_json_response_content(self, sh):
        """Claude returning JSON string."""
        json_str = '{"product_name": "Generations II"}'
        fake_client = self._make_claude_client(json_str)
        with patch.object(sh, "anthropic") as mock_anthropic_mod:
            mock_anthropic_mod.Anthropic.return_value = fake_client
            result = sh.call_claude("s", "u")
        assert json.loads(result)["product_name"] == "Generations II"


# ===========================================================================
# get_repo_files
# ===========================================================================

class TestGetRepoFiles:
    def _tree_item(self, path, item_type="blob", url="http://blob/url"):
        return {"type": item_type, "path": path, "url": url}

    def _blob_response(self, content_str):
        encoded = base64.b64encode(content_str.encode()).decode()
        return {"content": encoded + "\n"}  # GitHub adds newline

    def test_fetches_matching_extensions(self, sh):
        tree = [
            self._tree_item("src/main.py"),
            self._tree_item("src/utils.js"),
            self._tree_item("README.md"),
        ]
        tree_resp = _make_response(json_data={"tree": tree})
        blob_resp = _make_response(json_data=self._blob_response("print('hello')"))

        with patch.object(sh.requests, "get", side_effect=[tree_resp, blob_resp]) as mock_get:
            result = sh.get_repo_files("owner", "repo", [".py"])

        assert "src/main.py" in result
        assert "src/utils.js" not in result
        assert "README.md" not in result

    def test_respects_max_files_limit(self, sh):
        tree = [self._tree_item(f"file{i}.py") for i in range(10)]
        tree_resp = _make_response(json_data={"tree": tree})
        blob_resps = [
            _make_response(json_data=self._blob_response(f"content {i}"))
            for i in range(10)
        ]

        with patch.object(sh.requests, "get", side_effect=[tree_resp] + blob_resps):
            result = sh.get_repo_files("owner", "repo", [".py"], max_files=3)

        assert len(result) == 3

    def test_multiple_extensions(self, sh):
        tree = [
            self._tree_item("a.py"),
            self._tree_item("b.js"),
            self._tree_item("c.md"),
            self._tree_item("d.ts"),
        ]
        tree_resp = _make_response(json_data={"tree": tree})
        blob_py = _make_response(json_data=self._blob_response("py content"))
        blob_js = _make_response(json_data=self._blob_response("js content"))

        with patch.object(sh.requests, "get", side_effect=[tree_resp, blob_py, blob_js]):
            result = sh.get_repo_files("owner", "repo", [".py", ".js"])

        assert "a.py" in result
        assert "b.js" in result
        assert "c.md" not in result

    def test_skips_non_blob_items(self, sh):
        tree = [
            {"type": "tree", "path": "src", "url": "http://tree/url"},
            self._tree_item("src/main.py"),
        ]
        tree_resp = _make_response(json_data={"tree": tree})
        blob_resp = _make_response(json_data=self._blob_response("code"))

        with patch.object(sh.requests, "get", side_effect=[tree_resp, blob_resp]):
            result = sh.get_repo_files("owner", "repo", [".py"])

        assert len(result) == 1
        assert "src/main.py" in result

    def test_handles_decode_error_gracefully(self, sh):
        tree = [self._tree_item("bad.py")]
        tree_resp = _make_response(json_data={"tree": tree})
        # Return blob with no 'content' key to trigger exception
        blob_resp = _make_response(json_data={})

        with patch.object(sh.requests, "get", side_effect=[tree_resp, blob_resp]):
            result = sh.get_repo_files("owner", "repo", [".py"])

        # Should not raise, just skip