"""
Test suite for .github/scripts/shared.py

What is tested:
    - call_claude(): Claude API invocation, response extraction
    - clean_json(): markdown fence stripping, edge cases
    - get_repo_files(): GitHub tree fetch, extension filtering, base64 decoding, max_files limit
    - get_pr_diff(): PR diff fetch, truncation behaviour
    - write_output_file(): file create (no SHA) and update (with SHA) paths, fallback URL
    - post_pr_comment(): POST to GitHub issues comments endpoint
    - send_email(): SendGrid payload construction, success/failure status handling
    - email_html(): HTML template rendering for SUCCESS and FAILURE statuses
    - write_audit_entry(): audit log construction (tested via mocked write_output_file)

Mocks used:
    - unittest.mock.patch for os.environ (module-level env vars)
    - unittest.mock.MagicMock / patch for anthropic.Anthropic client
    - unittest.mock.patch("requests.get") for all GitHub API GET calls
    - unittest.mock.patch("requests.post") for GitHub comments and SendGrid
    - unittest.mock.patch("requests.put") for GitHub contents PUT
    - unittest.mock.patch for write_output_file inside write_audit_entry

TODOs:
    - TODO: Integration test for actual Claude model round-trip (requires live ANTHROPIC_API_KEY)
    - TODO: Test write_audit_entry full JSON/Markdown content written to output repo
      (requires inspecting the exact audit format once source is complete — source truncated)
    - TODO: Verify GH_HEADERS are forwarded correctly in each request (requires deeper header inspection)
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
# Helper: bootstrap the module with mandatory env vars present so the import
# does not raise KeyError regardless of the real environment.
# ---------------------------------------------------------------------------
REQUIRED_ENV = {
    "ANTHROPIC_API_KEY": "test-anthropic-key",
    "GH_TOKEN": "test-gh-token",
    "SENDGRID_API_KEY": "test-sendgrid-key",
    "OUTPUT_REPO": "ai-delivery-outputs",
    "OUTPUT_REPO_OWNER": "test-owner",
    "NOTIFY_EMAIL": "notify@example.com",
    "SENDER_EMAIL": "sender@example.com",
}


@pytest.fixture(scope="session", autouse=True)
def _patch_env_for_import():
    """Ensure all required env vars exist before shared.py is imported."""
    with mock.patch.dict("os.environ", REQUIRED_ENV, clear=False):
        # Force (re)import so module-level constants pick up our env vars.
        if "shared" in sys.modules:
            del sys.modules["shared"]
        # Make the scripts directory importable.
        import importlib.util, pathlib

        script_path = pathlib.Path(__file__).parent.parent / ".github" / "scripts" / "shared.py"
        spec = importlib.util.spec_from_file_location("shared", script_path)
        mod = importlib.util.module_from_spec(spec)
        # Stub out anthropic at import time so we don't need the real package.
        fake_anthropic = types.ModuleType("anthropic")
        fake_anthropic.Anthropic = MagicMock()
        sys.modules.setdefault("anthropic", fake_anthropic)
        sys.modules["shared"] = mod
        spec.loader.exec_module(mod)
        yield mod


@pytest.fixture()
def shared():
    return sys.modules["shared"]


# ===========================================================================
# clean_json
# ===========================================================================
class TestCleanJson:
    def test_plain_json_unchanged(self, shared):
        raw = '{"key": "value"}'
        assert shared.clean_json(raw) == '{"key": "value"}'

    def test_strips_backtick_json_fence(self, shared):
        raw = "```json\n{\"key\": \"value\"}\n```"
        result = shared.clean_json(raw)
        assert result == '{"key": "value"}'

    def test_strips_plain_backtick_fence(self, shared):
        raw = "```\n{\"a\": 1}\n```"
        result = shared.clean_json(raw)
        assert result == '{"a": 1}'

    def test_leading_trailing_whitespace_stripped(self, shared):
        raw = "   \n{\"x\": 2}\n   "
        assert shared.clean_json(raw) == '{"x": 2}'

    def test_empty_string(self, shared):
        assert shared.clean_json("") == ""

    def test_fence_with_extra_whitespace_inside(self, shared):
        raw = "```json\n  { \"a\": 1 }  \n```"
        result = shared.clean_json(raw)
        assert result == '{ "a": 1 }'

    def test_no_fence_with_newlines(self, shared):
        raw = '{\n  "key": "value"\n}'
        assert shared.clean_json(raw) == '{\n  "key": "value"\n}'

    def test_multiple_backtick_blocks_only_outermost_stripped(self, shared):
        # Only the opening and closing fences should be removed.
        raw = "```json\n{\"inner\": \"```code```\"}\n```"
        result = shared.clean_json(raw)
        # The inner backticks should survive; the outer ones are stripped.
        assert "inner" in result

    def test_fence_without_closing(self, shared):
        # No closing ``` — rsplit returns original tail; just verify no crash.
        raw = "```json\n{\"a\": 1}"
        result = shared.clean_json(raw)
        assert isinstance(result, str)

    def test_valid_json_parseable_after_clean(self, shared):
        raw = "```json\n{\"product_name\": \"Generations II\"}\n```"
        result = shared.clean_json(raw)
        parsed = json.loads(result)
        assert parsed["product_name"] == "Generations II"


# ===========================================================================
# call_claude
# ===========================================================================
class TestCallClaude:
    def _make_response(self, text: str):
        content_block = MagicMock()
        content_block.text = text
        response = MagicMock()
        response.content = [content_block]
        return response

    def test_returns_text_from_response(self, shared):
        fake_response = self._make_response("Hello from Claude")
        fake_client = MagicMock()
        fake_client.messages.create.return_value = fake_response

        with patch("anthropic.Anthropic", return_value=fake_client):
            # Also patch the module-level reference
            shared_anthropic = sys.modules["anthropic"]
            orig = shared_anthropic.Anthropic
            shared_anthropic.Anthropic = MagicMock(return_value=fake_client)
            try:
                result = shared.call_claude("system prompt", "user prompt")
            finally:
                shared_anthropic.Anthropic = orig

        assert result == "Hello from Claude"

    def test_passes_correct_model_and_max_tokens(self, shared):
        fake_response = self._make_response("ok")
        fake_client = MagicMock()
        fake_client.messages.create.return_value = fake_response

        shared_anthropic = sys.modules["anthropic"]
        orig = shared_anthropic.Anthropic
        shared_anthropic.Anthropic = MagicMock(return_value=fake_client)
        try:
            shared.call_claude("sys", "usr", max_tokens=512)
            _, kwargs = fake_client.messages.create.call_args
            assert kwargs["max_tokens"] == 512
            assert kwargs["model"] == shared.MODEL
        finally:
            shared_anthropic.Anthropic = orig

    def test_passes_system_and_user_messages(self, shared):
        fake_response = self._make_response("ok")
        fake_client = MagicMock()
        fake_client.messages.create.return_value = fake_response

        shared_anthropic = sys.modules["anthropic"]
        orig = shared_anthropic.Anthropic
        shared_anthropic.Anthropic = MagicMock(return_value=fake_client)
        try:
            shared.call_claude("my system", "my user")
            _, kwargs = fake_client.messages.create.call_args
            assert kwargs["system"] == "my system"
            assert kwargs["messages"] == [{"role": "user", "content": "my user"}]
        finally:
            shared_anthropic.Anthropic = orig

    def test_default_max_tokens_is_4096(self, shared):
        fake_response = self._make_response("ok")
        fake_client = MagicMock()
        fake_client.messages.create.return_value = fake_response

        shared_anthropic = sys.modules["anthropic"]
        orig = shared_anthropic.Anthropic
        shared_anthropic.Anthropic = MagicMock(return_value=fake_client)
        try:
            shared.call_claude("s", "u")
            _, kwargs = fake_client.messages.create.call_args
            assert kwargs["max_tokens"] == 4096
        finally:
            shared_anthropic.Anthropic = orig

    @pytest.mark.skip(reason="TODO: Integration test — requires live ANTHROPIC_API_KEY and network access")
    def test_integration_real_claude(self, shared):
        pass


# ===========================================================================
# get_repo_files
# ===========================================================================
class TestGetRepoFiles:
    def _make_blob_item(self, path: str, url: str = "http://blob-url"):
        return {"type": "blob", "path": path, "url": url}

    def _encode_content(self, text: str) -> str:
        return base64.b64encode(text.encode()).decode() + "\n"

    def test_returns_files_matching_extension(self, shared):
        tree = [
            self._make_blob_item("src/main.py"),
            self._make_blob_item("docs/readme.md"),
            self._make_blob_item("src/utils.py"),
        ]
        blob_content = {"content": self._encode_content("print('hello')")}

        def fake_get(url, headers=None):
            resp = MagicMock()
            if "trees" in url:
                resp.json.return_value = {"tree": tree}
            else:
                resp.json.return_value = blob_content
            return resp

        with patch("requests.get", side_effect=fake_get):
            result = shared.get_repo_files("owner", "repo", [".py"])

        assert "src/main.py" in result
        assert "src/utils.py" in result
        assert "docs/readme.md" not in result

    def test_max_files_limit_respected(self, shared):
        tree = [self._make_blob_item(f"file{i}.py") for i in range(10)]
        blob_content = {"content": self._encode_content("code")}

        def fake_get(url, headers=None):
            resp = MagicMock()
            if "trees" in url:
                resp.json.return_value = {"tree": tree}
            else:
                resp.json.return_value = blob_content
            return resp

        with patch("requests.get", side_effect=fake_get):
            result = shared.get_repo_files("owner", "repo", [".py"], max_files=3)

        assert len(result) == 3

    def test_skips_non_blob_items(self, shared):
        tree = [
            {"type": "tree", "path": "src/", "url": "http://tree-url"},
            self._make_blob_item("src/main.py"),
        ]
        blob_content = {"content": self._encode_content("code")}

        def fake_get(url, headers=None):
            resp = MagicMock()
            if "trees" in url:
                resp.json.return_value = {"tree": tree}
            else:
                resp.json.return_value = blob_content
            return resp

        with patch("requests.get", side_effect=fake_get):
            result = shared.get_repo_files("owner", "repo", [".py"])

        assert "src/" not in result
        assert "src/main.py" in result

    def test_filters_multiple_extensions(self, shared):
        tree = [
            self._make_blob_item("a.py"),
            self._make_blob_item("b.js"),
            self._make_blob_item("c.txt"),
        ]
        blob_content = {"content": self._encode_content("data")}

        def fake_get(url, headers=None):
            resp = MagicMock()
            if "trees" in url:
                resp.json.return_value = {"tree": tree}
            else:
                resp.json.return_value = blob_content
            return resp

        with patch("requests.get", side_effect=fake_get):
            result = shared.get_repo_files("owner", "repo", [".py", ".js"])

        assert "a.py" in result
        assert "b.js" in result
        assert "c.txt" not in result

    def test_empty_tree_returns_empty_dict(self, shared):
        def fake_get(url, headers=None):
            resp = MagicMock()
            resp.json.return_value = {"tree": []}
            return resp

        with patch("requests.get", side_effect=fake_get):
            result = shared.get_repo_files("owner", "repo", [".py"])

        assert result == {}

    def test_missing_tree_key_returns_empty_dict(self, shared):
        def fake_get(url, headers=None):
            resp = MagicMock()
            resp.json.return_value = {}
            return resp

        with patch("requests.get", side_effect=fake_get):
            result = shared.get_repo_files("owner", "repo", [".py"])

        assert result == {}

    def test_blob_decode_error_skips_file(self, shared):
        tree = [self._make_blob_item("bad.py")]
        # Return invalid base64 that will raise during decode
        def fake_get(url, headers=None):
            resp = MagicMock()
            if "trees" in url:
                resp.json.return_value = {"tree": tree}
            else:
                resp.json.return_value = {"content": "!!!not-valid-base64!!!"}
            return resp

        with patch("requests.get", side_effect=fake_get):
            result = shared.get_repo_files("owner", "repo", [".py"])

        # Should not raise; bad file is silently skipped
        assert isinstance(result, dict)

    def test_correct_url_built(self, shared):
        tree = []

        captured_urls = []

        def fake_get(url, headers=None):
            captured_urls.append(url)
            resp = MagicMock()
            resp.json.return_value = {"tree": tree}
            return resp

        with patch("requests.get", side_effect=fake_get):
            shared.get_repo_files("myowner", "myrepo", [".py"])

        assert any("myowner/myrepo" in u for u in captured_urls)
        assert any("recursive=1" in u for u in captured_urls)

    def test_decoded_content_stored(self, shared):
        tree = [self._make_blob_item("hello.py")]
        blob_content = {"content": self._encode_content("print('world')")}

        def fake_get(url, headers=None):
            resp = MagicMock()
            if "trees" in url:
                resp.json.return_value = {"tree": tree}
            else:
                resp.json.