"""
Test suite for tool2_tech_docs.py

What is tested:
- generate_docs(): fetches files, calls Claude three times, returns docs dict
- build_index(): constructs correct markdown index with links and metadata
- __main__ block behaviour: success path and failure/exception path

Mocks used:
- shared.call_claude          — stubbed to return deterministic strings
- shared.get_repo_files       — stubbed to return synthetic file dicts
- shared.write_output_file    — stubbed to return fake GitHub URLs
- shared.send_email           — stubbed (no-op)
- shared.email_html           — stubbed to return HTML string
- shared.write_audit_entry    — stubbed (no-op)
- shared.OUTPUT_REPO_OWNER    — patched constant
- shared.OUTPUT_REPO          — patched constant
- datetime.datetime.utcnow    — patched to return a fixed timestamp
- os.environ                  — patched for __main__ tests

TODOs:
- TODO: Integration test against a real GitHub repo requires live credentials — skipped
- TODO: Test behaviour when get_repo_files raises a network error mid-stream
- TODO: Verify actual Claude prompt content in depth (requires contract-style testing)
"""

import sys
import os
import importlib
import types
import datetime
from unittest import mock
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Helpers to build a fake "shared" module so the import at the top of the
# source file doesn't require the real module to be present.
# ---------------------------------------------------------------------------

FAKE_OUTPUT_REPO_OWNER = "ai-bot-owner"
FAKE_OUTPUT_REPO = "ai-bot-output"


def _make_fake_shared():
    shared = types.ModuleType("shared")
    shared.call_claude = MagicMock(return_value="# Generated content")
    shared.get_repo_files = MagicMock(return_value={})
    shared.write_output_file = MagicMock(return_value="https://github.com/fake/url")
    shared.send_email = MagicMock(return_value=None)
    shared.email_html = MagicMock(return_value="<html>body</html>")
    shared.write_audit_entry = MagicMock(return_value=None)
    shared.OUTPUT_REPO_OWNER = FAKE_OUTPUT_REPO_OWNER
    shared.OUTPUT_REPO = FAKE_OUTPUT_REPO
    return shared


def _load_module(fake_shared):
    """
    Load (or reload) tool2_tech_docs with the fake shared module injected.
    Returns the module object.
    """
    sys.modules["shared"] = fake_shared
    # Make sure the script directory is on sys.path
    script_dir = os.path.join(os.path.dirname(__file__), ".github", "scripts")
    if script_dir not in sys.path:
        sys.path.insert(0, script_dir)

    # Remove any cached version so we get a fresh import each time
    if "tool2_tech_docs" in sys.modules:
        del sys.modules["tool2_tech_docs"]

    spec_path = os.path.join(os.path.dirname(__file__), ".github", "scripts", "tool2_tech_docs.py")
    import importlib.util
    spec = importlib.util.spec_from_file_location("tool2_tech_docs", spec_path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["tool2_tech_docs"] = mod
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def fake_shared():
    """Inject a fresh fake shared module before every test."""
    shared = _make_fake_shared()
    sys.modules["shared"] = shared
    yield shared
    # Cleanup
    sys.modules.pop("shared", None)
    sys.modules.pop("tool2_tech_docs", None)


@pytest.fixture()
def mod(fake_shared):
    """Return the tool2_tech_docs module loaded with the fake shared module."""
    return _load_module(fake_shared)


SAMPLE_PY_FILES = {
    "main.py": "def main():\n    pass\n",
    "utils.py": "def helper():\n    return 42\n",
}

SAMPLE_IAC_FILES = {
    "main.tf": 'resource "aws_s3_bucket" "bucket" {}\n',
    "vars.yaml": "region: us-east-1\n",
}


# ---------------------------------------------------------------------------
# Tests for generate_docs()
# ---------------------------------------------------------------------------


class TestGenerateDocs:

    def test_returns_three_doc_keys(self, mod, fake_shared):
        """Happy path: generate_docs returns README, ARCHITECTURE, RUNBOOK."""
        fake_shared.get_repo_files.return_value = {}
        fake_shared.call_claude.return_value = "# Doc content"

        docs = mod.generate_docs("myorg", "myrepo", "https://github.com/run/1")

        assert set(docs.keys()) == {"README.md", "ARCHITECTURE.md", "RUNBOOK.md"}

    def test_call_claude_called_three_times(self, mod, fake_shared):
        """Claude should be invoked exactly three times (one per document)."""
        fake_shared.get_repo_files.return_value = {}
        fake_shared.call_claude.return_value = "content"

        mod.generate_docs("org", "repo", "https://example.com")

        assert fake_shared.call_claude.call_count == 3

    def test_get_repo_files_called_for_correct_extensions(self, mod, fake_shared):
        """get_repo_files is called once for source files and once for IaC files."""
        fake_shared.get_repo_files.return_value = {}
        fake_shared.call_claude.return_value = "content"

        mod.generate_docs("org", "repo", "https://example.com")

        calls = fake_shared.get_repo_files.call_args_list
        assert len(calls) == 2

        # First call — source code extensions
        _, kwargs_or_args = calls[0][0], calls[0]
        src_call_args = calls[0][0]  # positional args tuple
        assert "org" in src_call_args
        assert "repo" in src_call_args
        # The extension list should include Python & JS variants
        src_exts = src_call_args[2]
        assert ".py" in src_exts
        assert ".js" in src_exts

        # Second call — IaC extensions
        iac_call_args = calls[1][0]
        iac_exts = iac_call_args[2]
        assert ".tf" in iac_exts or ".yaml" in iac_exts

    def test_docs_contain_claude_return_value(self, mod, fake_shared):
        """Each document value should equal the value returned by call_claude."""
        fake_shared.get_repo_files.return_value = {}
        fake_shared.call_claude.return_value = "## My Document\nSome content."

        docs = mod.generate_docs("org", "repo", "https://example.com")

        for key, value in docs.items():
            assert value == "## My Document\nSome content.", f"{key} has unexpected content"

    def test_owner_and_repo_appear_in_claude_prompts(self, mod, fake_shared):
        """Owner and repo name should appear in the user prompt passed to Claude."""
        fake_shared.get_repo_files.return_value = {}
        fake_shared.call_claude.return_value = "content"

        mod.generate_docs("acme-org", "killer-app", "https://example.com")

        for c in fake_shared.call_claude.call_args_list:
            user_prompt = c[0][1]  # second positional arg
            assert "acme-org" in user_prompt
            assert "killer-app" in user_prompt

    def test_with_real_file_content_appears_in_prompt(self, mod, fake_shared):
        """File content fetched from repo should be embedded in the Claude prompt."""
        fake_shared.get_repo_files.side_effect = [
            SAMPLE_PY_FILES,   # first call (source files)
            SAMPLE_IAC_FILES,  # second call (IaC files)
        ]
        fake_shared.call_claude.return_value = "content"

        mod.generate_docs("org", "repo", "https://example.com")

        # README call: should contain source + iac file names
        readme_call_prompt = fake_shared.call_claude.call_args_list[0][0][1]
        assert "main.py" in readme_call_prompt
        assert "main.tf" in readme_call_prompt

    def test_empty_files_produce_no_files_found_placeholder(self, mod, fake_shared):
        """When no files are found the formatted string should contain the placeholder."""
        fake_shared.get_repo_files.return_value = {}
        captured_prompts = []

        def capture_claude(system, user):
            captured_prompts.append(user)
            return "content"

        fake_shared.call_claude.side_effect = capture_claude

        mod.generate_docs("org", "repo", "https://example.com")

        # README prompt should mention no files
        assert "_No files found_" in captured_prompts[0]

    def test_file_content_truncated_to_4000_chars(self, mod, fake_shared):
        """Content longer than 4000 chars must be truncated in the formatted string."""
        long_content = "x" * 10_000
        fake_shared.get_repo_files.side_effect = [
            {"bigfile.py": long_content},
            {},
        ]
        captured_prompts = []

        def capture(system, user):
            captured_prompts.append(user)
            return "content"

        fake_shared.call_claude.side_effect = capture

        mod.generate_docs("org", "repo", "https://example.com")

        # The full 10 000 chars must NOT appear; only up to 4000
        assert long_content not in captured_prompts[0]
        assert "x" * 4000 in captured_prompts[0]
        assert "x" * 4001 not in captured_prompts[0]

    def test_readme_uses_readme_system_prompt(self, mod, fake_shared):
        """README generation should use the SYSTEM_README system prompt."""
        fake_shared.get_repo_files.return_value = {}
        fake_shared.call_claude.return_value = "content"

        mod.generate_docs("org", "repo", "https://example.com")

        readme_system = fake_shared.call_claude.call_args_list[0][0][0]
        assert "README" in readme_system or "technical writer" in readme_system.lower()

    def test_architecture_uses_arch_system_prompt(self, mod, fake_shared):
        """Architecture doc should use the SYSTEM_ARCH system prompt."""
        fake_shared.get_repo_files.return_value = {}
        fake_shared.call_claude.return_value = "content"

        mod.generate_docs("org", "repo", "https://example.com")

        arch_system = fake_shared.call_claude.call_args_list[1][0][0]
        assert "architect" in arch_system.lower() or "architecture" in arch_system.lower()

    def test_runbook_uses_runbook_system_prompt(self, mod, fake_shared):
        """Runbook should use the SYSTEM_RUNBOOK system prompt."""
        fake_shared.get_repo_files.return_value = {}
        fake_shared.call_claude.return_value = "content"

        mod.generate_docs("org", "repo", "https://example.com")

        runbook_system = fake_shared.call_claude.call_args_list[2][0][0]
        assert "runbook" in runbook_system.lower() or "devops" in runbook_system.lower()

    def test_call_claude_raises_propagates(self, mod, fake_shared):
        """If call_claude raises, generate_docs should propagate the exception."""
        fake_shared.get_repo_files.return_value = {}
        fake_shared.call_claude.side_effect = RuntimeError("Claude API error")

        with pytest.raises(RuntimeError, match="Claude API error"):
            mod.generate_docs("org", "repo", "https://example.com")

    def test_get_repo_files_raises_propagates(self, mod, fake_shared):
        """If get_repo_files raises, generate_docs should propagate the exception."""
        fake_shared.get_repo_files.side_effect = ConnectionError("GitHub unreachable")

        with pytest.raises(ConnectionError, match="GitHub unreachable"):
            mod.generate_docs("org", "repo", "https://example.com")

    def test_max_files_limits_passed_correctly(self, mod, fake_shared):
        """Max file limits (15 for source, 10 for IaC) should be forwarded."""
        fake_shared.get_repo_files.return_value = {}
        fake_shared.call_claude.return_value = "content"

        mod.generate_docs("org", "repo", "https://example.com")

        calls = fake_shared.get_repo_files.call_args_list
        # max_files keyword arg or positional
        def extract_max_files(c):
            if "max_files" in c.kwargs:
                return c.kwargs["max_files"]
            if len(c.args) >= 4:
                return c.args[3]
            return None

        assert extract_max_files(calls[0]) == 15
        assert extract_max_files(calls[1]) == 10


# ---------------------------------------------------------------------------
# Tests for build_index()
# ---------------------------------------------------------------------------


class TestBuildIndex:

    def test_returns_string(self, mod):
        docs = {"README.md": "content", "ARCHITECTURE.md": "content2"}
        result = mod.build_index("org", "repo", docs, "2024-01-15 10:00 UTC")
        assert isinstance(result, str)

    def test_contains_owner_and_repo(self, mod):
        docs = {"README.md": "content"}
        result = mod.build_index("myorg", "myrepo", docs, "2024-01-15 10:00 UTC")
        assert "myorg" in result
        assert "myrepo" in result

    def test_contains_timestamp(self, mod):
        docs = {"README.md": "content"}
        result = mod.build_index("org", "repo", docs, "2024-06-01 12:00 UTC")
        assert "2024-06-01 12:00 UTC" in result

    def test_contains_all_doc_links(self, mod):
        docs = {"README.md": "c1", "ARCHITECTURE.md": "c2", "RUNBOOK.md": "c3"}
        result = mod.build_index("org", "repo", docs, "2024-01-01 00:00 UTC")
        assert "README.md" in result
        assert "ARCHITECTURE.md" in result
        assert "RUNBOOK.md" in result

    def test_links_point_to_output_repo(self, mod):
        """Links must reference the OUTPUT_REPO_OWNER and OUTPUT_REPO constants."""
        docs = {"README.md": "content"}
        result = mod.build_index("org", "repo", docs, "2024-01-01 00:00 UTC")
        assert FAKE_OUTPUT_REPO_OWNER in result
        assert FAKE_OUTPUT_REPO in result

    def test_link_format_is_markdown(self, mod):
        """Links should be markdown hyperlinks."""
        docs = {"README.md": "content"}
        result = mod.build_index("org", "repo", docs, "2024-01-01 00:00 UTC")
        # Markdown link pattern: [text](url)