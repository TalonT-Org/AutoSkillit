from __future__ import annotations

import ast
from pathlib import Path

import pytest

from autoskillit.cli._hooks_codex import (
    _is_autoskillit_hook_entry,
    generate_codex_hooks_config,
    sync_hooks_to_codex_config,
)
from autoskillit.execution.backends._codex_config import _read_codex_config

pytestmark = [pytest.mark.layer("hooks"), pytest.mark.medium]


class TestNoThirdPartyToml:
    @pytest.mark.parametrize(
        "rel_path",
        [
            "src/autoskillit/cli/_hooks_codex.py",
            "src/autoskillit/execution/backends/_codex_hooks.py",
        ],
    )
    def test_no_third_party_toml_in_hooks_codex(self, rel_path):
        source = Path(__file__).resolve().parents[2] / rel_path
        tree = ast.parse(source.read_text())
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = []
                if isinstance(node, ast.Import):
                    names = [a.name for a in node.names]
                else:
                    names = [node.module or ""]
                for name in names:
                    assert name not in ("toml", "tomli", "tomlkit"), (
                        f"Third-party TOML import found in {rel_path}: {name}"
                    )


class TestGenerateCodexHooksConfig:
    def test_generate_excludes_interactive_only(self):
        result = generate_codex_hooks_config()
        for entries in result.values():
            for entry in entries:
                for hook in entry.get("hooks", []):
                    assert hook.get("session_scope") != "interactive_only"

    def test_generate_consolidates_by_event_matcher(self):
        result = generate_codex_hooks_config()
        event_matcher_counts: dict[tuple, int] = {}
        for event_type, entries in result.items():
            for entry in entries:
                key = (event_type, entry.get("matcher"))
                event_matcher_counts[key] = event_matcher_counts.get(key, 0) + 1
        for count in event_matcher_counts.values():
            assert count == 1, "Duplicate (event, matcher) consolidation failed"

    def test_generate_session_start_omits_matcher(self):
        result = generate_codex_hooks_config()
        for entry in result.get("SessionStart", []):
            assert "matcher" not in entry

    def test_generate_includes_timeout(self):
        result = generate_codex_hooks_config()
        has_timeout = False
        for entries in result.values():
            for entry in entries:
                for hook in entry.get("hooks", []):
                    if "timeout" in hook:
                        has_timeout = True
                        break
        assert has_timeout

    def test_generate_returns_dict_keyed_by_event_type(self):
        result = generate_codex_hooks_config()
        assert isinstance(result, dict)
        for key in result:
            assert key in ("PreToolUse", "PostToolUse", "SessionStart")


class TestIsAutoskillitHookEntry:
    def test_is_autoskillit_hook_entry_true_autoskillit_path(self):
        entry = {"hooks": [{"command": "/autoskillit/hooks/guard.py"}]}
        assert _is_autoskillit_hook_entry(entry) is True

    def test_is_autoskillit_hook_entry_true_hooks_dir(self):
        entry = {"hooks": [{"command": "python3 /some/path/hooks/_dispatch.py guard"}]}
        assert _is_autoskillit_hook_entry(entry) is True

    def test_is_autoskillit_hook_entry_false_foreign(self):
        entry = {"hooks": [{"command": "python3 /usr/local/bin/foreign.py"}]}
        assert _is_autoskillit_hook_entry(entry) is False


class TestSyncHooksToCodexConfig:
    def test_sync_creates_from_missing(self, tmp_path):
        p = tmp_path / "config.toml"
        result = sync_hooks_to_codex_config(config_path=p)
        assert result is True
        config = _read_codex_config(p).data
        assert "hooks" in config

    def test_sync_idempotent(self, tmp_path):
        p = tmp_path / "config.toml"
        result1 = sync_hooks_to_codex_config(config_path=p)
        mtime_before = p.stat().st_mtime_ns
        result2 = sync_hooks_to_codex_config(config_path=p)
        assert result1 is True
        assert result2 is False
        assert p.stat().st_mtime_ns == mtime_before

    def test_sync_preserves_foreign_hooks(self, tmp_path):
        p = tmp_path / "config.toml"
        p.write_text(
            '[[hooks.PreToolUse]]\nmatcher = "ForeignTool"\n'
            '[[hooks.PreToolUse.hooks]]\ncommand = "python3 /usr/local/guard.py"\n'
        )
        result = sync_hooks_to_codex_config(config_path=p)
        assert result is True
        config = _read_codex_config(p).data
        hooks = config.get("hooks", {})
        pre_entries = hooks.get("PreToolUse", [])
        foreign = [
            e
            for e in pre_entries
            if any("/usr/local/" in h.get("command", "") for h in e.get("hooks", []))
        ]
        assert len(foreign) > 0

    def test_sync_replaces_stale(self, tmp_path):
        p = tmp_path / "config.toml"
        p.write_text(
            '[[hooks.PreToolUse]]\nmatcher = "StaleMatch"\n'
            '[[hooks.PreToolUse.hooks]]\ncommand = "/autoskillit/hooks/_dispatch.py old"\n'
        )
        result = sync_hooks_to_codex_config(config_path=p)
        assert result is True
        config = _read_codex_config(p).data
        hooks = config.get("hooks", {})
        all_cmds = []
        for entries in hooks.values():
            for entry in entries:
                for h in entry.get("hooks", []):
                    all_cmds.append(h.get("command", ""))
        autoskillit_cmds = [c for c in all_cmds if "/autoskillit/" in c or "_dispatch.py" in c]
        assert len(autoskillit_cmds) > 0
        stale = [c for c in all_cmds if "old" in c]
        assert len(stale) == 0

    def test_sync_empty_config(self, tmp_path):
        p = tmp_path / "config.toml"
        p.write_text("")
        result = sync_hooks_to_codex_config(config_path=p)
        assert result is True

    def test_sync_config_path_override(self, tmp_path):
        p = tmp_path / "codex.toml"
        result = sync_hooks_to_codex_config(config_path=p)
        assert result is True
        assert p.exists()

    def test_sync_creates_nested_format(self, tmp_path):
        p = tmp_path / "config.toml"
        sync_hooks_to_codex_config(config_path=p)
        content = p.read_text(encoding="utf-8")
        assert "[[hooks.PreToolUse]]" in content
        assert "[[hooks]]\n" not in content


class TestHookSyncCorruptFilePreservation:
    def test_sync_hooks_preserves_corrupt_file_content(self, tmp_path):
        p = tmp_path / "config.toml"
        original = "[projects./home/user/repo]\ntrust = true\n"
        p.write_text(original, encoding="utf-8")
        sync_hooks_to_codex_config(config_path=p)
        content = p.read_text(encoding="utf-8")
        assert "[projects./home/user/repo]" in content
        assert "trust = true" in content

    def test_sync_hooks_appends_to_corrupt_file(self, tmp_path):
        p = tmp_path / "config.toml"
        original = "[projects./home/user/repo]\ntrust = true\n"
        p.write_text(original, encoding="utf-8")
        sync_hooks_to_codex_config(config_path=p)
        content = p.read_text(encoding="utf-8")
        assert "[projects./home/user/repo]" in content
        assert "[[hooks.PreToolUse]]" in content
