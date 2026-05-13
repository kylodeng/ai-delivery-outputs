"""
Test suite for .github/scripts/shared.py

What is tested:
    - call_claude(): happy path, API response parsing, token forwarding
    - clean_json(): markdown fence stripping (various formats), passthrough, edge cases
    - get_repo_files(): file fetching, extension filtering, max_files limit, decode errors
    - get_pr_diff(): URL construction, truncation at 30 000 chars, header override
    - write_output_file(): create (no SHA), update (with SHA), fallback URL
    - post_pr_comment(): correct endpoint, payload
    - send_email(): success (200/202), warning on failure
    - email_html(): SUCCESS/FAILURE colour, field interpolation
    - write_audit_entry(): JSON + Markdown appended to output repo (stubbed)

Mocks used:
    - unittest.mock.patch / MagicMock for:
        - anthropic.Anthropic (Claude client)
        - requests.get / requests.post / requests.put
        - base64 (passthrough – real lib used)
    - Environment variables injected via monkeypatch / os.environ patching

TODOs:
    - TODO: write_audit_entry() body is truncated in the source – full implementation
            needed to test JSON/Markdown round-trip properly (stub tests provided).
    - TODO: Integration test for real SendGrid/GitHub endpoints (skipped – needs secrets).
"""

import base64
import importlib
import json
import os
import sys
import types
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Environment bootstrap – must happen BEFORE shared.py is imported because the
# module reads os.environ at import time.
# ---------------------------------------------------------------------------

ENV_DEFAULTS = {
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
def _patch_env():
    """Inject required env vars for the whole session before shared.py loads."""
    with patch.dict(os.environ, ENV_DEFAULTS, clear=False):
        yield


# Lazy import so the env patch above is applied first.
@pytest.fixture(scope="session")
def shared(tmp_path_factory):
    """Return the shared module, imported once after env is patched."""
    # Remove cached version if any (re-run safety)
    sys.modules.pop("shared", None)
    # Add the scripts directory to path
    scripts_dir = os.path.join(os.path.dirname(__file__), ".github", "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)

    # Also try direct path resolution relative to repo root
    repo_root = os.path.dirname(os.path.abspath(__file__))
    alt_path = os.path.join(repo_root, ".github", "scripts")
    if alt_path not in sys.path:
        sys.path.insert(0, alt_path)

    import shared as _shared
    return _shared


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def mock_anthropic(shared):
    """Patch anthropic.Anthropic inside the shared module."""
    with patch.object(shared.anthropic, "Anthropic") as mock_cls:
        mock_client = MagicMock()
        mock_cls.return_value = mock_client
        yield mock_cls, mock_client


@pytest.fixture()
def mock_requests_get(shared):
    with patch.object(shared.requests, "get") as mock_get:
        yield mock_get


@pytest.fixture()
def mock_requests_post(shared):
    with patch.object(shared.requests, "post") as mock_post:
        yield mock_post


@pytest.fixture()
def mock_requests_put(shared):
    with patch.object(shared.requests, "put") as mock_put:
        yield mock_put


# ---------------------------------------------------------------------------
# call_claude
# ---------------------------------------------------------------------------

class TestCallClaude:
    def test_happy_path_returns_text(self, shared, mock_anthropic):
        mock_cls, mock_client = mock_anthropic
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="Hello from Claude")]
        mock_client.messages.create.return_value = mock_response

        result = shared.call_claude("system prompt", "user prompt")

        assert result == "Hello from Claude"

    def test_uses_correct_model(self, shared, mock_anthropic):
        mock_cls, mock_client = mock_anthropic
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="ok")]
        mock_client.messages.create.return_value = mock_response

        shared.call_claude("sys", "usr")

        _, kwargs = mock_client.messages.create.call_args
        assert kwargs["model"] == shared.MODEL

    def test_default_max_tokens(self, shared, mock_anthropic):
        mock_cls, mock_client = mock_anthropic
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="ok")]
        mock_client.messages.create.return_value = mock_response

        shared.call_claude("sys", "usr")

        _, kwargs = mock_client.messages.create.call_args
        assert kwargs["max_tokens"] == 4096

    def test_custom_max_tokens(self, shared, mock_anthropic):
        mock_cls, mock_client = mock_anthropic
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="ok")]
        mock_client.messages.create.return_value = mock_response

        shared.call_claude("sys", "usr", max_tokens=1024)

        _, kwargs = mock_client.messages.create.call_args
        assert kwargs["max_tokens"] == 1024

    def test_passes_system_and_user(self, shared, mock_anthropic):
        mock_cls, mock_client = mock_anthropic
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="ok")]
        mock_client.messages.create.return_value = mock_response

        shared.call_claude("my system", "my user")

        _, kwargs = mock_client.messages.create.call_args
        assert kwargs["system"] == "my system"
        assert kwargs["messages"] == [{"role": "user", "content": "my user"}]

    def test_uses_api_key_from_env(self, shared, mock_anthropic):
        mock_cls, mock_client = mock_anthropic
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="ok")]
        mock_client.messages.create.return_value = mock_response

        shared.call_claude("sys", "usr")

        mock_cls.assert_called_once_with(api_key=shared.ANTHROPIC_API_KEY)

    def test_returns_first_content_block(self, shared, mock_anthropic):
        """Ensures only the first content block text is returned."""
        mock_cls, mock_client = mock_anthropic
        mock_response = MagicMock()
        mock_response.content = [
            MagicMock(text="first block"),
            MagicMock(text="second block"),
        ]
        mock_client.messages.create.return_value = mock_response

        result = shared.call_claude("sys", "usr")
        assert result == "first block"

    def test_empty_string_response(self, shared, mock_anthropic):
        mock_cls, mock_client = mock_anthropic
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="")]
        mock_client.messages.create.return_value = mock_response

        result = shared.call_claude("sys", "usr")
        assert result == ""

    def test_api_exception_propagates(self, shared, mock_anthropic):
        mock_cls, mock_client = mock_anthropic
        mock_client.messages.create.side_effect = Exception("API error")

        with pytest.raises(Exception, match="API error"):
            shared.call_claude("sys", "usr")


# ---------------------------------------------------------------------------
# clean_json
# ---------------------------------------------------------------------------

class TestCleanJson:
    @pytest.mark.parametrize("raw,expected", [
        # Plain JSON – no fences
        ('{"key": "value"}', '{"key": "value"}'),
        # Whitespace only around it
        ('  {"key": "value"}  ', '{"key": "value"}'),
        # ```json fence
        ('```json\n{"key": "value"}\n```', '{"key": "value"}'),
        # ``` fence (no language tag)
        ('```\n{"key": "value"}\n```', '{"key": "value"}'),
        # Fence with trailing newline before closing
        ('```json\n{"a":1}\n```\n', '{"a":1}'),
        # Multi-line JSON in fence
        ('```json\n{\n  "a": 1,\n  "b": 2\n}\n```', '{\n  "a": 1,\n  "b": 2\n}'),
        # Array in fence
        ('```json\n[1, 2, 3]\n```', '[1, 2, 3]'),
        # Empty string
        ('', ''),
        # Only whitespace
        ('   ', ''),
    ])
    def test_variants(self, shared, raw, expected):
        assert shared.clean_json(raw) == expected

    def test_no_fence_passthrough(self, shared):
        data = '{"model_name": "Underwriting Risk Classification", "model_type": "CatBoostClassifier"}'
        assert shared.clean_json(data) == data

    def test_nested_json_preserved(self, shared):
        raw = '```json\n{"global_feature_importance": {"Age": 34.57}}\n```'
        result = shared.clean_json(raw)
        parsed = json.loads(result)
        assert parsed["global_feature_importance"]["Age"] == pytest.approx(34.57)

    def test_real_model_card_snippet(self, shared):
        """Synthetic data: model card JSON inside code fence."""
        inner = json.dumps({"model_name": "Underwriting Risk Classification",
                            "model_type": "CatBoostClassifier"})
        fenced = f"```json\n{inner}\n```"
        result = shared.clean_json(fenced)
        assert json.loads(result)["model_name"] == "Underwriting Risk Classification"


# ---------------------------------------------------------------------------
# get_repo_files
# ---------------------------------------------------------------------------

class TestGetRepoFiles:
    def _make_tree_response(self, items):
        resp = MagicMock()
        resp.json.return_value = {"tree": items}
        return resp

    def _make_blob_response(self, text: str):
        encoded = base64.b64encode(text.encode()).decode()
        resp = MagicMock()
        resp.json.return_value = {"content": encoded}
        return resp

    def test_happy_path_single_file(self, shared, mock_requests_get):
        tree = [{"type": "blob", "path": "app.py", "url": "https://api.github.com/blob/1"}]
        mock_requests_get.side_effect = [
            self._make_tree_response(tree),
            self._make_blob_response("print('hello')"),
        ]

        result = shared.get_repo_files("owner", "repo", [".py"])

        assert "app.py" in result
        assert result["app.py"] == "print('hello')"

    def test_extension_filtering(self, shared, mock_requests_get):
        tree = [
            {"type": "blob", "path": "main.py", "url": "u1"},
            {"type": "blob", "path": "style.css", "url": "u2"},
            {"type": "blob", "path": "README.md", "url": "u3"},
        ]
        mock_requests_get.side_effect = [
            self._make_tree_response(tree),
            self._make_blob_response("python code"),
        ]

        result = shared.get_repo_files("owner", "repo", [".py"])

        assert "main.py" in result
        assert "style.css" not in result
        assert "README.md" not in result

    def test_max_files_limit(self, shared, mock_requests_get):
        tree = [
            {"type": "blob", "path": f"file{i}.py", "url": f"u{i}"}
            for i in range(25)
        ]
        blob_resp = self._make_blob_response("code")
        mock_requests_get.side_effect = [self._make_tree_response(tree)] + [blob_resp] * 25

        result = shared.get_repo_files("owner", "repo", [".py"], max_files=5)

        assert len(result) == 5

    def test_skips_non_blob_items(self, shared, mock_requests_get):
        tree = [
            {"type": "tree", "path": "src", "url": "u1"},
            {"type": "blob", "path": "app.py", "url": "u2"},
        ]
        mock_requests_get.side_effect = [
            self._make_tree_response(tree),
            self._make_blob_response("code"),
        ]

        result = shared.get_repo_files("owner", "repo", [".py"])

        assert "src" not in result
        assert "app.py" in result

    def test_empty_tree(self, shared, mock_requests_get):
        mock_requests_get.return_value = self._make_tree_response([])

        result = shared.get_repo_files("owner", "repo", [".py"])

        assert result == {}

    def test_multiple_extensions(self, shared, mock_requests_get):
        tree = [
            {"type": "blob", "path": "app.py", "url": "u1"},
            {"type": "blob", "path": "index.js", "url": "u2"},
        ]
        mock_requests_get.side_effect = [
            self._make_tree_response(tree),
            self._make_blob_response("python"),
            self._make_blob_response("javascript"),
        ]

        result = shared.get_repo_files("owner", "repo", [".py", ".js"])

        assert "app.py" in result
        assert "index.js" in result

    def test_decode_error_skips_file(self, shared, mock_requests_get):
        """If base64 decode fails (missing content key), the file is skipped."""
        tree = [{"type": "blob", "path": "bad.py", "url": "u1"}]
        bad_blob = MagicMock()
        bad_blob.json.return_value = {}  # no "content" key → KeyError → except pass
        mock_requests_get.side_effect = [
            self._make_tree_response(tree),
            bad_blob,
        ]

        result = shared.get_repo_files("owner", "repo", [".py"])

        assert result == {}

    def test_correct_api_url_constructed(self, shared, mock_requests_get):
        mock_requests_get.return_value = self._make_tree_response([])

        shared.get_repo_files("myowner", "myrepo", [".py"])

        first_call_url = mock_requests_get.call_args_list[0][0][0]
        assert "myowner/myrepo/git/trees/HEAD" in first_call_url
        assert "