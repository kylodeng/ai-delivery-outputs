"""
Test suite for .github/scripts/shared.py

What is tested:
- call_claude(): Claude API invocation and response extraction
- clean_json(): Markdown fence stripping logic
- get_repo_files(): GitHub API tree fetching, extension filtering, base64 decoding, max_files cap
- get_pr_diff(): GitHub API PR diff fetching and truncation
- write_output_file(): File creation and update (with/without existing SHA)
- post_pr_comment(): PR comment posting
- send_email(): SendGrid email dispatch, warning on failure
- email_html(): HTML email body generation
- write_audit_entry(): Audit log writing (stubbed — requires more context)

Mocks used:
- unittest.mock.patch for os.environ (to satisfy module-level env var reads)
- unittest.mock.MagicMock / patch for requests.get, requests.post, requests.put
- unittest.mock.MagicMock for anthropic.Anthropic client

TODOs:
- TODO: write_audit_entry full coverage requires inspecting the complete function body
        (source is truncated). Stub tests are skipped below.
- TODO: Confirm MODEL constant value if it changes ("claude-sonnet-4-6" assumed).
- TODO: Integration tests for real GitHub/SendGrid/Claude endpoints (out of scope here).
"""

import base64
import datetime
import importlib
import json
import sys
import types
from unittest import mock
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Helpers to import the module under test with mocked environment variables
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


@pytest.fixture(scope="session", autouse=True)
def _mock_env_and_import():
    """Patch environment variables before the module is imported for the first time."""
    with mock.patch.dict("os.environ", REQUIRED_ENV, clear=False):
        # Provide a fake anthropic module so the import does not require the real package
        fake_anthropic = types.ModuleType("anthropic")
        fake_anthropic.Anthropic = MagicMock()
        sys.modules.setdefault("anthropic", fake_anthropic)

        import importlib
        # Force (re)import with patched env
        if "shared" in sys.modules:
            del sys.modules["shared"]
        # Add the script path so we can import it
        import os
        script_dir = os.path.join(os.path.dirname(__file__), "..", ".github", "scripts")
        script_dir = os.path.normpath(script_dir)
        if script_dir not in sys.path:
            sys.path.insert(0, script_dir)

        yield


@pytest.fixture()
def shared_module():
    """Return a freshly accessible reference to the shared module."""
    import importlib
    import shared  # noqa: PLC0415
    return shared


# ---------------------------------------------------------------------------
# clean_json
# ---------------------------------------------------------------------------
class TestCleanJson:
    def test_plain_json_unchanged(self, shared_module):
        raw = '{"key": "value"}'
        assert shared_module.clean_json(raw) == '{"key": "value"}'

    def test_strips_json_code_fence(self, shared_module):
        raw = "```json\n{\"key\": \"value\"}\n```"
        result = shared_module.clean_json(raw)
        assert result == '{"key": "value"}'

    def test_strips_generic_code_fence(self, shared_module):
        raw = "```\n{\"key\": \"value\"}\n```"
        result = shared_module.clean_json(raw)
        assert result == '{"key": "value"}'

    def test_strips_surrounding_whitespace(self, shared_module):
        raw = "   \n```json\n{\"a\":1}\n```\n   "
        result = shared_module.clean_json(raw)
        assert result == '{"a":1}'

    def test_empty_string(self, shared_module):
        assert shared_module.clean_json("") == ""

    def test_only_whitespace(self, shared_module):
        assert shared_module.clean_json("   ") == ""

    def test_multiline_json_in_fence(self, shared_module):
        inner = '{\n  "a": 1,\n  "b": 2\n}'
        raw = f"```json\n{inner}\n```"
        result = shared_module.clean_json(raw)
        assert result == inner

    def test_no_closing_fence_preserves_content(self, shared_module):
        # If there's an opening fence but no closing, rsplit on ``` should still work
        raw = "```json\n{\"key\": \"value\"}"
        result = shared_module.clean_json(raw)
        # rsplit on ``` with no closing fence returns the whole remaining string
        assert '{"key": "value"}' in result

    def test_valid_json_parseable_after_clean(self, shared_module):
        raw = "```json\n[1, 2, 3]\n```"
        result = shared_module.clean_json(raw)
        assert json.loads(result) == [1, 2, 3]

    def test_insurance_product_json_in_fence(self, shared_module):
        """Synthetic data: insurance annotation JSON wrapped in a code fence."""
        inner = json.dumps({
            "doc": {
                "product_name": "Generations II",
                "doc_type": "product_brochure",
                "linked_product": "Generations II",
            }
        })
        raw = f"```json\n{inner}\n```"
        result = shared_module.clean_json(raw)
        parsed = json.loads(result)
        assert parsed["doc"]["product_name"] == "Generations II"


# ---------------------------------------------------------------------------
# call_claude
# ---------------------------------------------------------------------------
class TestCallClaude:
    @pytest.fixture()
    def mock_anthropic_client(self, shared_module):
        fake_text = MagicMock()
        fake_text.text = "Hello from Claude"
        fake_response = MagicMock()
        fake_response.content = [fake_text]

        fake_client = MagicMock()
        fake_client.messages.create.return_value = fake_response

        with patch("anthropic.Anthropic", return_value=fake_client):
            # Also patch the module-level usage
            original = shared_module.anthropic.Anthropic
            shared_module.anthropic.Anthropic = MagicMock(return_value=fake_client)
            yield fake_client, fake_response
            shared_module.anthropic.Anthropic = original

    def test_returns_text_from_first_content_block(self, shared_module, mock_anthropic_client):
        fake_client, _ = mock_anthropic_client
        result = shared_module.call_claude("system prompt", "user message")
        assert result == "Hello from Claude"

    def test_calls_create_with_correct_params(self, shared_module, mock_anthropic_client):
        fake_client, _ = mock_anthropic_client
        shared_module.call_claude("sys", "usr", max_tokens=512)
        fake_client.messages.create.assert_called_once_with(
            model=shared_module.MODEL,
            max_tokens=512,
            system="sys",
            messages=[{"role": "user", "content": "usr"}],
        )

    def test_default_max_tokens(self, shared_module, mock_anthropic_client):
        fake_client, _ = mock_anthropic_client
        shared_module.call_claude("sys", "usr")
        _, kwargs = fake_client.messages.create.call_args
        assert fake_client.messages.create.call_args[1].get("max_tokens") == 4096 or \
               fake_client.messages.create.call_args[0][1] == 4096 or \
               fake_client.messages.create.call_args.kwargs.get("max_tokens", 4096) == 4096

    def test_propagates_anthropic_exception(self, shared_module):
        fake_client = MagicMock()
        fake_client.messages.create.side_effect = RuntimeError("API error")
        original = shared_module.anthropic.Anthropic
        shared_module.anthropic.Anthropic = MagicMock(return_value=fake_client)
        try:
            with pytest.raises(RuntimeError, match="API error"):
                shared_module.call_claude("sys", "usr")
        finally:
            shared_module.anthropic.Anthropic = original

    def test_model_constant(self, shared_module):
        assert shared_module.MODEL == "claude-sonnet-4-6"


# ---------------------------------------------------------------------------
# get_repo_files
# ---------------------------------------------------------------------------
class TestGetRepoFiles:

    def _make_tree_response(self, items):
        """Build a mock response for the repo tree API."""
        resp = MagicMock()
        resp.json.return_value = {"tree": items}
        return resp

    def _make_blob_response(self, content_str):
        encoded = base64.b64encode(content_str.encode()).decode()
        resp = MagicMock()
        resp.json.return_value = {"content": encoded}
        return resp

    def test_returns_files_matching_extension(self, shared_module):
        tree_items = [
            {"type": "blob", "path": "src/main.py", "url": "http://blob/main.py"},
            {"type": "blob", "path": "src/utils.py", "url": "http://blob/utils.py"},
            {"type": "blob", "path": "README.md", "url": "http://blob/readme"},
        ]
        tree_resp = self._make_tree_response(tree_items)
        blob_py = self._make_blob_response("print('hello')")
        blob_py2 = self._make_blob_response("x = 1")

        with patch("requests.get", side_effect=[tree_resp, blob_py, blob_py2]) as mock_get:
            result = shared_module.get_repo_files("owner", "repo", [".py"])
        assert "src/main.py" in result
        assert "src/utils.py" in result
        assert "README.md" not in result

    def test_filters_by_multiple_extensions(self, shared_module):
        tree_items = [
            {"type": "blob", "path": "a.py", "url": "http://blob/a"},
            {"type": "blob", "path": "b.js", "url": "http://blob/b"},
            {"type": "blob", "path": "c.txt", "url": "http://blob/c"},
        ]
        tree_resp = self._make_tree_response(tree_items)
        blob_a = self._make_blob_response("py content")
        blob_b = self._make_blob_response("js content")

        with patch("requests.get", side_effect=[tree_resp, blob_a, blob_b]):
            result = shared_module.get_repo_files("o", "r", [".py", ".js"])
        assert "a.py" in result
        assert "b.js" in result
        assert "c.txt" not in result

    def test_respects_max_files_limit(self, shared_module):
        tree_items = [
            {"type": "blob", "path": f"file{i}.py", "url": f"http://blob/{i}"}
            for i in range(10)
        ]
        tree_resp = self._make_tree_response(tree_items)
        blob_resps = [self._make_blob_response(f"content {i}") for i in range(3)]

        with patch("requests.get", side_effect=[tree_resp] + blob_resps):
            result = shared_module.get_repo_files("o", "r", [".py"], max_files=3)
        assert len(result) == 3

    def test_skips_tree_type_non_blob(self, shared_module):
        tree_items = [
            {"type": "tree", "path": "src/", "url": "http://tree/src"},
            {"type": "blob", "path": "main.py", "url": "http://blob/main"},
        ]
        tree_resp = self._make_tree_response(tree_items)
        blob = self._make_blob_response("code")

        with patch("requests.get", side_effect=[tree_resp, blob]):
            result = shared_module.get_repo_files("o", "r", [".py"])
        assert "main.py" in result
        assert "src/" not in result

    def test_handles_base64_decode_error_gracefully(self, shared_module):
        tree_items = [
            {"type": "blob", "path": "bad.py", "url": "http://blob/bad"},
        ]
        tree_resp = self._make_tree_response(tree_items)
        bad_blob = MagicMock()
        bad_blob.json.return_value = {}  # missing 'content' key → KeyError

        with patch("requests.get", side_effect=[tree_resp, bad_blob]):
            result = shared_module.get_repo_files("o", "r", [".py"])
        # Should not raise; bad file simply omitted
        assert "bad.py" not in result

    def test_empty_tree_returns_empty_dict(self, shared_module):
        tree_resp = MagicMock()
        tree_resp.json.return_value = {"tree": []}

        with patch("requests.get", return_value=tree_resp):
            result = shared_module.get_repo_files("o", "r", [".py"])
        assert result == {}

    def test_no_matching_extension_returns_empty(self, shared_module):
        tree_items = [
            {"type": "blob", "path": "main.go", "url": "http://blob/go"},
        ]
        tree_resp = self._make_tree_response(tree_items)

        with patch("requests.get", return_value=tree_resp):
            result = shared_module.get_repo_files("o", "r", [".py"])
        assert result == {}

    def test_decodes_utf8_content_correctly(self, shared_module):
        content = "# Hello → World"
        tree_items = [{"type": "blob", "path": "a.py", "url": "http://blob/a"}]
        tree_resp = self._make_tree_response(tree_items)
        blob = self._make_blob_response(content)

        with patch("requests.get", side_effect=[tree_resp, blob]):
            result = shared_module.get_repo_files("o", "r", [".py"])
        assert result["a.py"] == content

    def test_max_files_zero_returns_empty(self, shared_module):
        tree_items = [{"type": "blob", "path": "a.py", "url": "http://blob/a"}]
        tree_resp = self._make_tree_response(tree_items)

        with patch("requests.get", return_value=tree_resp):
            result = shared_module.get_repo_files("o", "r", [".py"], max_files=0)
        assert result == {}

    def test_insurance_json_annotation_files(self, shared_module):
        """Synthetic data: fetch annotation JSON files from an insurance repo."""
        tree_items = [
            {"type": "blob", "path": "data/Generations-II_PB_EN.pdf.annot.json",
             "url": "http://blob/gen2"},
            {"type": "blob", "