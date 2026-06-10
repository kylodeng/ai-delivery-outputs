"""
Tests for .github/scripts/shared.py

What is tested:
- call_claude(): happy path, response parsing, custom max_tokens
- clean_json(): markdown fence stripping (various formats), no-fence passthrough, edge cases
- get_repo_files(): happy path, extension filtering, max_files limit, base64 decode errors, empty tree
- get_pr_diff(): happy path, truncation at 30000 chars
- write_output_file(): create new file (no sha), update existing file (with sha), missing html_url fallback
- post_pr_comment(): happy path, correct URL/payload construction
- send_email(): success (200/202), failure warning path, default recipient
- email_html(): SUCCESS status (green), FAILURE status (red), HTML structure, field interpolation
- write_audit_entry(): stubbed (requires full source — source is truncated)

Mocks used:
- unittest.mock.patch for os.environ (to satisfy module-level env var reads)
- unittest.mock.MagicMock / patch for anthropic.Anthropic client
- unittest.mock.patch for requests.get, requests.post, requests.put
- unittest.mock.patch for base64.b64decode (where needed)
- builtins.print patched to capture warnings

TODOs:
- TODO: write_audit_entry() source is truncated — full implementation needed to test JSON/Markdown
        audit log writing, timestamp formatting, and details dict serialisation.
- TODO: confirm MODEL constant value if it changes — tests hard-code "claude-sonnet-4-6"
- TODO: integration-style test for get_repo_files() with a live GitHub token (skipped here)
"""

import base64
import importlib
import sys
import types
from unittest.mock import MagicMock, call, patch

import pytest

# ---------------------------------------------------------------------------
# Bootstrap: the module reads env vars at import time, so we must set them
# before importing shared.  We do this once at collection time via a fixture
# that patches os.environ and re-imports the module.
# ---------------------------------------------------------------------------

FAKE_ENV = {
    "ANTHROPIC_API_KEY": "fake-anthropic-key",
    "GH_TOKEN": "fake-gh-token",
    "SENDGRID_API_KEY": "fake-sendgrid-key",
    "OUTPUT_REPO": "ai-delivery-outputs",
    "OUTPUT_REPO_OWNER": "test-owner",
    "GITHUB_REPOSITORY_OWNER": "test-owner",
    "NOTIFY_EMAIL": "notify@example.com",
    "SENDER_EMAIL": "sender@example.com",
}


@pytest.fixture(scope="session", autouse=True)
def _patch_env_and_import():
    """Patch environment variables and import shared once for the session."""
    with patch.dict("os.environ", FAKE_ENV, clear=False):
        # Remove cached module so it re-evaluates module-level env reads
        sys.modules.pop("shared", None)
        sys.modules.pop(".github.scripts.shared", None)

        # Make the module importable from its non-package location
        import importlib.util, pathlib

        spec_path = pathlib.Path(__file__).parent / ".github" / "scripts" / "shared.py"
        # Fallback: try adjacent location used during CI
        if not spec_path.exists():
            spec_path = pathlib.Path(__file__).resolve().parent.parent / ".github" / "scripts" / "shared.py"

        if spec_path.exists():
            spec = importlib.util.spec_from_file_location("shared", spec_path)
            mod = importlib.util.module_from_spec(spec)
            # Provide a stub anthropic module so import doesn't fail in envs
            # where the package is absent
            if "anthropic" not in sys.modules:
                stub = types.ModuleType("anthropic")
                stub.Anthropic = MagicMock()
                sys.modules["anthropic"] = stub
            spec.loader.exec_module(mod)
            sys.modules["shared"] = mod
        else:
            # Last resort: try a direct import (works when pytest is run from repo root)
            if "anthropic" not in sys.modules:
                stub = types.ModuleType("anthropic")
                stub.Anthropic = MagicMock()
                sys.modules["anthropic"] = stub
            import shared  # noqa: F401

    yield


@pytest.fixture()
def shared_mod():
    """Return the already-imported shared module."""
    return sys.modules["shared"]


# ===========================================================================
# call_claude
# ===========================================================================

class TestCallClaude:
    def test_happy_path_returns_text(self, shared_mod):
        mock_text = "This is Claude's response."
        mock_client = MagicMock()
        mock_client.messages.create.return_value.content = [MagicMock(text=mock_text)]

        with patch("anthropic.Anthropic", return_value=mock_client):
            # Re-patch inside the module namespace
            with patch.object(
                sys.modules.get("anthropic", MagicMock()),
                "Anthropic",
                return_value=mock_client,
            ):
                # Patch directly on the module's reference
                with patch("shared.anthropic") as mock_anthropic_mod:
                    mock_anthropic_mod.Anthropic.return_value = mock_client
                    result = shared_mod.call_claude("sys prompt", "user msg")

        assert result == mock_text

    def test_uses_correct_model(self, shared_mod):
        mock_client = MagicMock()
        mock_client.messages.create.return_value.content = [MagicMock(text="ok")]

        with patch("shared.anthropic") as mock_anthropic_mod:
            mock_anthropic_mod.Anthropic.return_value = mock_client
            shared_mod.call_claude("sys", "user")
            _, kwargs = mock_client.messages.create.call_args
            assert kwargs.get("model") == shared_mod.MODEL

    def test_default_max_tokens(self, shared_mod):
        mock_client = MagicMock()
        mock_client.messages.create.return_value.content = [MagicMock(text="ok")]

        with patch("shared.anthropic") as mock_anthropic_mod:
            mock_anthropic_mod.Anthropic.return_value = mock_client
            shared_mod.call_claude("sys", "user")
            _, kwargs = mock_client.messages.create.call_args
            assert kwargs.get("max_tokens") == 4096

    def test_custom_max_tokens(self, shared_mod):
        mock_client = MagicMock()
        mock_client.messages.create.return_value.content = [MagicMock(text="ok")]

        with patch("shared.anthropic") as mock_anthropic_mod:
            mock_anthropic_mod.Anthropic.return_value = mock_client
            shared_mod.call_claude("sys", "user", max_tokens=1024)
            _, kwargs = mock_client.messages.create.call_args
            assert kwargs.get("max_tokens") == 1024

    def test_passes_system_and_user_messages(self, shared_mod):
        mock_client = MagicMock()
        mock_client.messages.create.return_value.content = [MagicMock(text="ok")]

        with patch("shared.anthropic") as mock_anthropic_mod:
            mock_anthropic_mod.Anthropic.return_value = mock_client
            shared_mod.call_claude("my system", "my user msg")
            _, kwargs = mock_client.messages.create.call_args
            assert kwargs.get("system") == "my system"
            assert kwargs.get("messages") == [{"role": "user", "content": "my user msg"}]

    def test_uses_api_key_from_env(self, shared_mod):
        mock_client = MagicMock()
        mock_client.messages.create.return_value.content = [MagicMock(text="ok")]

        with patch("shared.anthropic") as mock_anthropic_mod:
            mock_anthropic_mod.Anthropic.return_value = mock_client
            shared_mod.call_claude("s", "u")
            mock_anthropic_mod.Anthropic.assert_called_once_with(
                api_key=shared_mod.ANTHROPIC_API_KEY
            )


# ===========================================================================
# clean_json
# ===========================================================================

class TestCleanJson:
    @pytest.mark.parametrize("raw,expected", [
        # No fence — pass through
        ('{"key": "value"}', '{"key": "value"}'),
        # Leading/trailing whitespace only
        ('  {"key": "value"}  ', '{"key": "value"}'),
        # ```json fence
        ('```json\n{"key": "value"}\n```', '{"key": "value"}'),
        # ``` fence (no language tag)
        ('```\n{"key": "value"}\n```', '{"key": "value"}'),
        # Extra whitespace inside fences
        ('```json\n  {"key": "value"}  \n```', '{"key": "value"}'),
        # Multi-line JSON inside fences
        (
            '```json\n{\n  "a": 1,\n  "b": 2\n}\n```',
            '{\n  "a": 1,\n  "b": 2\n}',
        ),
        # Empty string edge case
        ("", ""),
        # Only fence markers (degenerate)
        ("```\n```", ""),
    ])
    def test_clean_json_variants(self, shared_mod, raw, expected):
        assert shared_mod.clean_json(raw) == expected

    def test_clean_json_no_mutation_on_plain_json(self, shared_mod):
        plain = '{"model_name": "Underwriting Risk Classification", "model_type": "CatBoostClassifier"}'
        assert shared_mod.clean_json(plain) == plain

    def test_clean_json_preserves_unicode(self, shared_mod):
        arabic = '{"cancel": "\\u0625\\u0644\\u063a\\u0627\\u0621"}'
        result = shared_mod.clean_json(f"```json\n{arabic}\n```")
        assert result == arabic

    def test_clean_json_nested_backticks_in_content(self, shared_mod):
        # Content that contains backticks but is NOT a fence
        raw = '{"code": "use `var` here"}'
        assert shared_mod.clean_json(raw) == raw


# ===========================================================================
# get_repo_files
# ===========================================================================

class TestGetRepoFiles:

    def _make_tree_response(self, items):
        return MagicMock(json=MagicMock(return_value={"tree": items}))

    def _make_blob_response(self, content: str):
        encoded = base64.b64encode(content.encode()).decode()
        return MagicMock(json=MagicMock(return_value={"content": encoded}))

    def test_happy_path_returns_matching_files(self, shared_mod):
        tree = [
            {"type": "blob", "path": "backend/model_card.json", "url": "https://api.github.com/blob/1"},
            {"type": "blob", "path": "backend/other.txt", "url": "https://api.github.com/blob/2"},
        ]
        file_content = '{"model_name": "Underwriting Risk Classification"}'

        tree_resp = self._make_tree_response(tree)
        blob_resp = self._make_blob_response(file_content)

        with patch("shared.requests.get", side_effect=[tree_resp, blob_resp]) as mock_get:
            result = shared_mod.get_repo_files("owner", "repo", [".json"])

        assert "backend/model_card.json" in result
        assert "backend/other.txt" not in result
        assert result["backend/model_card.json"] == file_content

    def test_extension_filtering_multiple_extensions(self, shared_mod):
        tree = [
            {"type": "blob", "path": "a.py", "url": "u1"},
            {"type": "blob", "path": "b.json", "url": "u2"},
            {"type": "blob", "path": "c.md", "url": "u3"},
            {"type": "blob", "path": "d.txt", "url": "u4"},
        ]

        tree_resp = self._make_tree_response(tree)
        py_blob = self._make_blob_response("print('hello')")
        json_blob = self._make_blob_response('{"x": 1}')

        with patch("shared.requests.get", side_effect=[tree_resp, py_blob, json_blob]):
            result = shared_mod.get_repo_files("owner", "repo", [".py", ".json"])

        assert set(result.keys()) == {"a.py", "b.json"}

    def test_max_files_limit_respected(self, shared_mod):
        tree = [
            {"type": "blob", "path": f"file{i}.json", "url": f"u{i}"}
            for i in range(10)
        ]

        tree_resp = self._make_tree_response(tree)
        blob_resps = [self._make_blob_response(f"content{i}") for i in range(3)]

        with patch("shared.requests.get", side_effect=[tree_resp] + blob_resps):
            result = shared_mod.get_repo_files("owner", "repo", [".json"], max_files=3)

        assert len(result) == 3

    def test_skips_tree_type_non_blob(self, shared_mod):
        tree = [
            {"type": "tree", "path": "somedir", "url": "u1"},
            {"type": "blob", "path": "file.json", "url": "u2"},
        ]
        tree_resp = self._make_tree_response(tree)
        blob_resp = self._make_blob_response('{}')

        with patch("shared.requests.get", side_effect=[tree_resp, blob_resp]):
            result = shared_mod.get_repo_files("owner", "repo", [".json"])

        assert "somedir" not in result
        assert "file.json" in result

    def test_decode_error_skips_file_gracefully(self, shared_mod):
        tree = [
            {"type": "blob", "path": "bad.json", "url": "u1"},
            {"type": "blob", "path": "good.json", "url": "u2"},
        ]
        tree_resp = self._make_tree_response(tree)

        # First blob returns something that causes an exception during decode
        bad_blob = MagicMock(json=MagicMock(return_value={"content": None}))  # None → exception
        good_blob = self._make_blob_response('{"ok": true}')

        with patch("shared.requests.get", side_effect=[tree_resp, bad_blob, good_blob]):
            result = shared_mod.get_repo_files("owner", "repo", [".json"])

        assert "bad.json" not in result
        assert "good.json" in result

    def test_empty_tree_returns_empty_dict(self, shared_mod):
        tree_resp = MagicMock(json=MagicMock(return_value={"tree": []}))
        with patch("shared.requests.get", return_value=tree_resp):
            result = shared_mod.get_repo_files("owner", "repo", [".json"])
        assert result == {}

    def test_missing_tree_key_returns_empty_dict(self, shared_mod):
        tree_resp = MagicMock(json=MagicMock(return_value={}))
        with patch("shared.requests.get", return_value=tree_resp):
            result = shared_mod.get_repo_files("owner", "repo", [".json"])
        assert result == {}

    def test_correct_url_constructed(self, shared_mod):
        tree_resp