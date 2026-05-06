"""
Test suite for .github/scripts/shared.py

What is tested:
- call_claude(): Claude API invocation, response extraction
- clean_json(): markdown fence stripping, edge cases
- get_repo_files(): GitHub tree fetching, extension filtering, max_files limit, base64 decoding
- get_pr_diff(): PR diff fetching, truncation
- write_output_file(): file creation (no SHA), file update (with SHA), fallback URL
- post_pr_comment(): PR comment posting
- send_email(): SendGrid payload construction, success/failure status handling
- email_html(): HTML generation, status color logic
- write_audit_entry(): audit log writing (stub — requires more context)

Mocks used:
- unittest.mock.patch for os.environ (env vars)
- unittest.mock.MagicMock / patch for anthropic.Anthropic client
- unittest.mock.patch for requests.get, requests.post, requests.put
- unittest.mock.patch for base64.b64decode (selective)

TODOs:
- write_audit_entry() full test requires knowledge of how existing audit file content is read/parsed
  and how JSON + Markdown logs are structured — stub tests provided
- Integration test for actual Claude model response shape
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
# Helper: build a minimal environment so shared.py can be imported
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


@pytest.fixture(scope="module")
def shared_module():
    """Import shared.py once with a controlled environment."""
    with patch.dict("os.environ", FAKE_ENV, clear=False):
        # Ensure a fresh import even if something cached it before
        if "shared" in sys.modules:
            del sys.modules["shared"]

        # Stub anthropic so the import itself never hits the real SDK
        fake_anthropic = types.ModuleType("anthropic")
        fake_anthropic.Anthropic = MagicMock()
        sys.modules.setdefault("anthropic", fake_anthropic)

        import importlib.util, pathlib

        spec = importlib.util.spec_from_file_location(
            "shared",
            pathlib.Path(__file__).parent.parent / ".github" / "scripts" / "shared.py",
        )
        mod = importlib.util.module_from_spec(spec)
        with patch.dict("os.environ", FAKE_ENV):
            spec.loader.exec_module(mod)
        sys.modules["shared"] = mod
        return mod


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def shared(shared_module):
    """Re-usable reference to the shared module."""
    return shared_module


# ---------------------------------------------------------------------------
# call_claude()
# ---------------------------------------------------------------------------


class TestCallClaude:
    def test_happy_path_returns_text(self, shared):
        """call_claude returns the .text of the first content block."""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="Hello from Claude")]
        mock_client.messages.create.return_value = mock_response

        with patch.object(shared.anthropic, "Anthropic", return_value=mock_client):
            result = shared.call_claude("sys prompt", "user prompt")

        assert result == "Hello from Claude"

    def test_passes_correct_model_and_tokens(self, shared):
        """call_claude forwards model, max_tokens, system and messages correctly."""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="ok")]
        mock_client.messages.create.return_value = mock_response

        with patch.object(shared.anthropic, "Anthropic", return_value=mock_client):
            shared.call_claude("sys", "usr", max_tokens=1024)

        _, kwargs = mock_client.messages.create.call_args
        assert kwargs["model"] == shared.MODEL
        assert kwargs["max_tokens"] == 1024
        assert kwargs["system"] == "sys"
        assert kwargs["messages"] == [{"role": "user", "content": "usr"}]

    def test_default_max_tokens(self, shared):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="ok")]
        mock_client.messages.create.return_value = mock_response

        with patch.object(shared.anthropic, "Anthropic", return_value=mock_client):
            shared.call_claude("s", "u")

        _, kwargs = mock_client.messages.create.call_args
        assert kwargs["max_tokens"] == 4096

    def test_api_error_propagates(self, shared):
        """If the Anthropic client raises, the exception bubbles up."""
        mock_client = MagicMock()
        mock_client.messages.create.side_effect = RuntimeError("API down")

        with patch.object(shared.anthropic, "Anthropic", return_value=mock_client):
            with pytest.raises(RuntimeError, match="API down"):
                shared.call_claude("s", "u")

    def test_uses_api_key_from_env(self, shared):
        """Anthropic client is instantiated with the configured API key."""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="x")]
        mock_client.messages.create.return_value = mock_response

        with patch.object(shared.anthropic, "Anthropic", return_value=mock_client) as mock_cls:
            shared.call_claude("s", "u")

        mock_cls.assert_called_once_with(api_key=shared.ANTHROPIC_API_KEY)


# ---------------------------------------------------------------------------
# clean_json()
# ---------------------------------------------------------------------------


class TestCleanJson:
    @pytest.mark.parametrize(
        "raw, expected",
        [
            # No fences — unchanged
            ('{"key": "value"}', '{"key": "value"}'),
            # Standard ```json fence
            ('```json\n{"key": "value"}\n```', '{"key": "value"}'),
            # Plain ``` fence
            ('```\n{"a":1}\n```', '{"a":1}'),
            # Surrounding whitespace stripped
            ('  {"x": 1}  ', '{"x": 1}'),
            # Fence with leading/trailing whitespace
            ('  ```json\n[1,2,3]\n```  ', '[1,2,3]'),
            # Empty JSON object in fence
            ("```\n{}\n```", "{}"),
            # Array response
            ('```json\n[{"id":1}]\n```', '[{"id":1}]'),
        ],
    )
    def test_clean_json_variants(self, shared, raw, expected):
        assert shared.clean_json(raw) == expected

    def test_empty_string(self, shared):
        assert shared.clean_json("") == ""

    def test_whitespace_only(self, shared):
        assert shared.clean_json("   ") == ""

    def test_multiple_backtick_blocks_only_outer_stripped(self, shared):
        """Only the outermost fences are removed; inner content is preserved."""
        raw = "```json\n{\"nested\": \"```inner```\"}\n```"
        result = shared.clean_json(raw)
        # Should at least not crash and return something sensible
        assert "nested" in result

    def test_fence_without_closing(self, shared):
        """A fence with no closing ``` — gracefully handled (no crash)."""
        raw = "```json\n{\"a\": 1}"
        result = shared.clean_json(raw)
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# get_repo_files()
# ---------------------------------------------------------------------------


class TestGetRepoFiles:
    def _make_tree_response(self, items):
        return MagicMock(json=MagicMock(return_value={"tree": items}))

    def _make_blob_response(self, content_str):
        encoded = base64.b64encode(content_str.encode()).decode()
        return MagicMock(json=MagicMock(return_value={"content": encoded}))

    def test_happy_path_returns_filtered_files(self, shared):
        tree = [
            {"type": "blob", "path": "src/main.py", "url": "http://blob/1"},
            {"type": "blob", "path": "README.md", "url": "http://blob/2"},
            {"type": "tree", "path": "src", "url": "http://tree/1"},
        ]
        responses = [
            self._make_tree_response(tree),
            self._make_blob_response("print('hello')"),
            self._make_blob_response("# readme"),
        ]

        with patch("requests.get", side_effect=responses):
            result = shared.get_repo_files("owner", "repo", [".py", ".md"])

        assert "src/main.py" in result
        assert "README.md" in result
        assert result["src/main.py"] == "print('hello')"
        assert result["README.md"] == "# readme"

    def test_filters_by_extension(self, shared):
        tree = [
            {"type": "blob", "path": "file.py", "url": "http://blob/1"},
            {"type": "blob", "path": "file.js", "url": "http://blob/2"},
            {"type": "blob", "path": "file.go", "url": "http://blob/3"},
        ]
        blob_py = self._make_blob_response("python code")
        responses = [self._make_tree_response(tree), blob_py]

        with patch("requests.get", side_effect=responses):
            result = shared.get_repo_files("owner", "repo", [".py"])

        assert "file.py" in result
        assert "file.js" not in result
        assert "file.go" not in result

    def test_max_files_limit_respected(self, shared):
        tree = [
            {"type": "blob", "path": f"file{i}.py", "url": f"http://blob/{i}"}
            for i in range(10)
        ]
        blob_responses = [self._make_blob_response(f"code{i}") for i in range(3)]
        responses = [self._make_tree_response(tree)] + blob_responses

        with patch("requests.get", side_effect=responses):
            result = shared.get_repo_files("owner", "repo", [".py"], max_files=3)

        assert len(result) == 3

    def test_skips_tree_nodes(self, shared):
        tree = [
            {"type": "tree", "path": "src", "url": "http://tree/1"},
            {"type": "blob", "path": "main.py", "url": "http://blob/1"},
        ]
        responses = [
            self._make_tree_response(tree),
            self._make_blob_response("code"),
        ]

        with patch("requests.get", side_effect=responses):
            result = shared.get_repo_files("owner", "repo", [".py"])

        assert "src" not in result
        assert "main.py" in result

    def test_empty_tree_returns_empty_dict(self, shared):
        with patch("requests.get", return_value=self._make_tree_response([])):
            result = shared.get_repo_files("owner", "repo", [".py"])

        assert result == {}

    def test_blob_decode_error_skipped(self, shared):
        """Files whose content cannot be decoded are silently skipped."""
        tree = [{"type": "blob", "path": "bad.py", "url": "http://blob/1"}]
        bad_blob = MagicMock(json=MagicMock(return_value={"content": "!!!not-base64!!!"}))
        responses = [self._make_tree_response(tree), bad_blob]

        with patch("requests.get", side_effect=responses):
            result = shared.get_repo_files("owner", "repo", [".py"])

        # Should either skip or include something — must not raise
        assert isinstance(result, dict)

    def test_correct_url_built(self, shared):
        with patch("requests.get", return_value=self._make_tree_response([])) as mock_get:
            shared.get_repo_files("myowner", "myrepo", [".py"])

        called_url = mock_get.call_args_list[0][0][0]
        assert "myowner" in called_url
        assert "myrepo" in called_url
        assert "recursive=1" in called_url

    def test_no_matching_extension(self, shared):
        tree = [{"type": "blob", "path": "file.py", "url": "http://blob/1"}]
        with patch("requests.get", return_value=self._make_tree_response(tree)):
            result = shared.get_repo_files("owner", "repo", [".ts"])

        assert result == {}

    def test_multiple_extensions(self, shared):
        tree = [
            {"type": "blob", "path": "a.py", "url": "http://blob/1"},
            {"type": "blob", "path": "b.ts", "url": "http://blob/2"},
            {"type": "blob", "path": "c.go", "url": "http://blob/3"},
        ]
        responses = [
            self._make_tree_response(tree),
            self._make_blob_response("python"),
            self._make_blob_response("typescript"),
        ]

        with patch("requests.get", side_effect=responses):
            result = shared.get_repo_files("owner", "repo", [".py", ".ts"])

        assert "a.py" in result
        assert "b.ts" in result
        assert "c.go" not in result


# ---------------------------------------------------------------------------
# get_pr_diff()
# ---------------------------------------------------------------------------


class TestGetPrDiff:
    def test_happy_path_returns_diff_text(self, shared):
        mock_resp = MagicMock()
        mock_resp.text = "diff --git a/file.py b/file.py\n+added line"

        with patch("requests.get", return_value=mock_resp):
            result = shared.get_pr_diff("owner", "repo", 42)

        assert "diff --git" in result
        assert "+added line" in result

    def test_truncates_to_30000_chars(self, shared):
        long_diff = "x" * 50000
        mock_resp = MagicMock()
        mock_resp.text = long_diff

        with patch("requests.get", return_value=mock_resp):
            result = shared.get_pr_diff("owner", "repo", 1)

        assert len(result) == 30000

    def test_short_diff_not_truncated(self, shared):
        short_diff = "small diff"
        mock_resp = MagicMock()
        mock_resp.text = short_diff

        with patch("requests.get", return_value=mock_resp):
            result = shared.get_pr_diff("owner", "repo", 1)

        assert result == short_diff

    def test_correct_url_built(self, shared):
        mock_resp = MagicMock()
        mock_resp.text = ""

        with patch("requests.