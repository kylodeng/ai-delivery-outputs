"""
Test module for .github/scripts/shared.py

What is tested:
- call_claude(): Claude API wrapper
- clean_json(): markdown fence stripping utility
- get_repo_files(): GitHub repository file fetching
- get_pr_diff(): GitHub PR diff fetching
- write_output_file(): GitHub file create/update
- post_pr_comment(): GitHub PR comment posting
- send_email(): SendGrid email sending
- email_html(): HTML email body generation
- write_audit_entry(): Audit log writing (stub — requires full source)

Mocks used:
- unittest.mock.patch for os.environ (to satisfy module-level env var reads)
- unittest.mock.MagicMock / patch for anthropic.Anthropic client
- unittest.mock.patch for requests.get, requests.post, requests.put
- base64 decoding tested with real base64 content

TODOs:
- TODO: write_audit_entry full test requires the complete source (truncated in provided code)
- TODO: get_repo_files pagination / max_files boundary test with large trees needs more fixture data
- TODO: Integration test for call_claude with real API key (skipped — no real calls)
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
# Helpers to import shared.py with a controlled environment
# ---------------------------------------------------------------------------

FAKE_ENV = {
    "ANTHROPIC_API_KEY": "test-anthropic-key",
    "GH_TOKEN": "test-gh-token",
    "SENDGRID_API_KEY": "test-sg-key",
    "OUTPUT_REPO": "ai-delivery-outputs",
    "OUTPUT_REPO_OWNER": "test-owner",
    "NOTIFY_EMAIL": "notify@example.com",
    "SENDER_EMAIL": "sender@example.com",
    "GITHUB_REPOSITORY_OWNER": "test-owner",
}


def import_shared(extra_env: dict = None):
    """Import (or re-import) shared with a mocked environment."""
    env = {**FAKE_ENV, **(extra_env or {})}
    # Remove cached module so we get a fresh import with patched env
    for key in list(sys.modules.keys()):
        if "shared" in key and "test" not in key:
            del sys.modules[key]

    with patch.dict("os.environ", env, clear=True):
        # anthropic must be importable; mock the whole package
        fake_anthropic = types.ModuleType("anthropic")
        fake_anthropic.Anthropic = MagicMock()
        with patch.dict("sys.modules", {"anthropic": fake_anthropic}):
            import importlib.util, pathlib

            spec = importlib.util.spec_from_file_location(
                "shared",
                pathlib.Path(__file__).parent.parent / ".github" / "scripts" / "shared.py",
            )
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Fixture: shared module loaded once per test session
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def shared():
    fake_anthropic = types.ModuleType("anthropic")
    fake_anthropic.Anthropic = MagicMock()
    with patch.dict("os.environ", FAKE_ENV, clear=True):
        with patch.dict("sys.modules", {"anthropic": fake_anthropic}):
            import importlib.util, pathlib

            spec = importlib.util.spec_from_file_location(
                "shared",
                pathlib.Path(__file__).parent.parent / ".github" / "scripts" / "shared.py",
            )
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
    return mod


# ===========================================================================
# clean_json tests
# ===========================================================================


class TestCleanJson:
    def test_no_fences_unchanged(self, shared):
        raw = '{"key": "value"}'
        assert shared.clean_json(raw) == '{"key": "value"}'

    def test_strips_json_fence(self, shared):
        raw = "```json\n{\"key\": \"value\"}\n```"
        result = shared.clean_json(raw)
        assert result == '{"key": "value"}'

    def test_strips_plain_fence(self, shared):
        raw = "```\n{\"key\": \"value\"}\n```"
        result = shared.clean_json(raw)
        assert result == '{"key": "value"}'

    def test_strips_leading_trailing_whitespace(self, shared):
        raw = "   \n{\"a\": 1}\n   "
        assert shared.clean_json(raw) == '{"a": 1}'

    def test_empty_string(self, shared):
        assert shared.clean_json("") == ""

    def test_only_fences(self, shared):
        raw = "```\n```"
        result = shared.clean_json(raw)
        assert result == ""

    def test_nested_json_content_preserved(self, shared):
        inner = '{"products": ["Generations II", "health_products"], "count": 2}'
        raw = f"```json\n{inner}\n```"
        assert shared.clean_json(raw) == inner

    def test_multiline_json_in_fences(self, shared):
        raw = "```json\n{\n  \"a\": 1,\n  \"b\": 2\n}\n```"
        result = shared.clean_json(raw)
        assert result == '{\n  "a": 1,\n  "b": 2\n}'

    def test_fence_without_closing(self, shared):
        """If there's an opening fence but no closing, rsplit returns the full content."""
        raw = "```json\n{\"a\": 1}"
        result = shared.clean_json(raw)
        # After stripping opening fence line we get '{"a": 1}'
        # rsplit on ``` when not present returns the whole string
        assert '{"a": 1}' in result

    @pytest.mark.parametrize("fence_lang", ["json", "python", ""])
    def test_various_fence_languages(self, shared, fence_lang):
        raw = f"```{fence_lang}\n{{\"x\": 1}}\n```"
        result = shared.clean_json(raw)
        assert result == '{"x": 1}'


# ===========================================================================
# call_claude tests
# ===========================================================================


class TestCallClaude:
    def _make_response(self, text="Hello from Claude"):
        content_block = MagicMock()
        content_block.text = text
        response = MagicMock()
        response.content = [content_block]
        return response

    def test_happy_path_returns_text(self, shared):
        fake_client = MagicMock()
        fake_client.messages.create.return_value = self._make_response("Output text")

        with patch.object(shared.anthropic, "Anthropic", return_value=fake_client):
            result = shared.call_claude("system prompt", "user prompt")

        assert result == "Output text"

    def test_passes_correct_model(self, shared):
        fake_client = MagicMock()
        fake_client.messages.create.return_value = self._make_response()

        with patch.object(shared.anthropic, "Anthropic", return_value=fake_client):
            shared.call_claude("sys", "usr")

        _, kwargs = fake_client.messages.create.call_args
        assert kwargs["model"] == shared.MODEL

    def test_default_max_tokens(self, shared):
        fake_client = MagicMock()
        fake_client.messages.create.return_value = self._make_response()

        with patch.object(shared.anthropic, "Anthropic", return_value=fake_client):
            shared.call_claude("sys", "usr")

        _, kwargs = fake_client.messages.create.call_args
        assert kwargs["max_tokens"] == 4096

    def test_custom_max_tokens(self, shared):
        fake_client = MagicMock()
        fake_client.messages.create.return_value = self._make_response()

        with patch.object(shared.anthropic, "Anthropic", return_value=fake_client):
            shared.call_claude("sys", "usr", max_tokens=1024)

        _, kwargs = fake_client.messages.create.call_args
        assert kwargs["max_tokens"] == 1024

    def test_messages_structure(self, shared):
        fake_client = MagicMock()
        fake_client.messages.create.return_value = self._make_response()

        with patch.object(shared.anthropic, "Anthropic", return_value=fake_client):
            shared.call_claude("my system", "my user")

        _, kwargs = fake_client.messages.create.call_args
        assert kwargs["system"] == "my system"
        assert kwargs["messages"] == [{"role": "user", "content": "my user"}]

    def test_api_key_passed_to_client(self, shared):
        fake_anthropic_cls = MagicMock()
        fake_instance = MagicMock()
        fake_instance.messages.create.return_value = self._make_response()
        fake_anthropic_cls.return_value = fake_instance

        with patch.object(shared.anthropic, "Anthropic", fake_anthropic_cls):
            shared.call_claude("s", "u")

        fake_anthropic_cls.assert_called_once_with(api_key=shared.ANTHROPIC_API_KEY)

    def test_raises_on_api_error(self, shared):
        fake_client = MagicMock()
        fake_client.messages.create.side_effect = Exception("API Error")

        with patch.object(shared.anthropic, "Anthropic", return_value=fake_client):
            with pytest.raises(Exception, match="API Error"):
                shared.call_claude("sys", "usr")

    def test_empty_system_prompt(self, shared):
        fake_client = MagicMock()
        fake_client.messages.create.return_value = self._make_response("ok")

        with patch.object(shared.anthropic, "Anthropic", return_value=fake_client):
            result = shared.call_claude("", "user prompt")

        assert result == "ok"


# ===========================================================================
# get_repo_files tests
# ===========================================================================


class TestGetRepoFiles:
    def _make_blob(self, path, content_str):
        encoded = base64.b64encode(content_str.encode()).decode()
        return {
            "type": "blob",
            "path": path,
            "url": f"https://api.github.com/repos/owner/repo/git/blobs/abc123_{path}",
            "content": encoded,
        }

    def _make_tree_response(self, items):
        r = MagicMock()
        r.json.return_value = {"tree": items}
        return r

    def _make_blob_response(self, content_str):
        encoded = base64.b64encode(content_str.encode()).decode()
        r = MagicMock()
        r.json.return_value = {"content": encoded}
        return r

    def test_happy_path_fetches_matching_files(self, shared):
        tree_items = [
            {"type": "blob", "path": "README.md", "url": "http://url/blob1"},
            {"type": "blob", "path": "main.py", "url": "http://url/blob2"},
        ]
        tree_resp = MagicMock()
        tree_resp.json.return_value = {"tree": tree_items}

        md_content = "# Hello"
        py_content = "print('hi')"

        blob_md = MagicMock()
        blob_md.json.return_value = {"content": base64.b64encode(md_content.encode()).decode()}
        blob_py = MagicMock()
        blob_py.json.return_value = {"content": base64.b64encode(py_content.encode()).decode()}

        with patch("requests.get", side_effect=[tree_resp, blob_md, blob_py]):
            result = shared.get_repo_files("owner", "repo", [".md", ".py"])

        assert result == {"README.md": "# Hello", "main.py": "print('hi')"}

    def test_filters_by_extension(self, shared):
        tree_items = [
            {"type": "blob", "path": "README.md", "url": "http://url/blob1"},
            {"type": "blob", "path": "data.json", "url": "http://url/blob2"},
            {"type": "blob", "path": "image.png", "url": "http://url/blob3"},
        ]
        tree_resp = MagicMock()
        tree_resp.json.return_value = {"tree": tree_items}

        md_blob = MagicMock()
        md_blob.json.return_value = {"content": base64.b64encode(b"# doc").decode()}

        with patch("requests.get", side_effect=[tree_resp, md_blob]):
            result = shared.get_repo_files("owner", "repo", [".md"])

        assert "README.md" in result
        assert "data.json" not in result
        assert "image.png" not in result

    def test_skips_non_blob_items(self, shared):
        tree_items = [
            {"type": "tree", "path": "src", "url": "http://url/tree1"},
            {"type": "blob", "path": "app.py", "url": "http://url/blob1"},
        ]
        tree_resp = MagicMock()
        tree_resp.json.return_value = {"tree": tree_items}

        blob_resp = MagicMock()
        blob_resp.json.return_value = {"content": base64.b64encode(b"code").decode()}

        with patch("requests.get", side_effect=[tree_resp, blob_resp]):
            result = shared.get_repo_files("owner", "repo", [".py"])

        assert "app.py" in result
        assert "src" not in result

    def test_respects_max_files(self, shared):
        tree_items = [
            {"type": "blob", "path": f"file{i}.py", "url": f"http://url/blob{i}"}
            for i in range(10)
        ]
        tree_resp = MagicMock()
        tree_resp.json.return_value = {"tree": tree_items}

        blob_resp = MagicMock()
        blob_resp.json.return_value = {"content": base64.b64encode(b"x").decode()}

        with patch("requests.get", side_effect=[tree_resp] + [blob_resp] * 10):
            result = shared.get_repo_files("owner", "repo", [".py"], max_files=3)

        assert len(result) == 3

    def test_empty_tree_returns_empty_dict(self, shared):
        tree_resp = MagicMock()
        tree_resp.json.return_value = {"tree": []}

        with patch("requests.get", return_value=tree_resp):
            result = shared.get_repo_files("owner", "repo", [".py"])

        assert result == {}

    def test_handles_decode_error_gracefully(self, shared):
        """Files that raise during decode are silently skipped."""
        tree_items = [
            {"type": "blob", "path": "bad.py", "url": "http://url/blob1"},
            {"type": "blob", "path": "good.py", "url": "http://url/blob2"},
        ]
        tree_resp = MagicMock()
        tree_resp.json.return_value = {"tree": tree_items}

        bad_blob = MagicMock()
        bad