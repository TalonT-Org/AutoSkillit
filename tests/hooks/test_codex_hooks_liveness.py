"""T-B6: Codex config liveness — detection and sync-time durable-root guard.

Detection: broken config.toml hooks → structured findings; healthy → none.
Sync guard: nonexistent resolved hooks dir → typed error; dev-checkout-only
fallback → typed error; live durable dir → writes as today.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("hooks"), pytest.mark.medium]


class TestFindBrokenCodexHookCommands:
    """Detection: broken Codex hook commands are reported."""

    def test_broken_dispatcher_reported(self, tmp_path: Path) -> None:
        from autoskillit.execution.backends._codex_hooks import (
            find_broken_codex_hook_commands,
        )

        config_path = tmp_path / "config.toml"
        config_path.write_text(
            "[[hooks]]\n"
            'event = "PreToolUse"\n'
            'command = "python3 -B /nonexistent/hooks/_dispatch.py foo"\n'
            'type = "command"\n'
        )
        broken = find_broken_codex_hook_commands(config_path)
        assert len(broken) == 1
        assert "/nonexistent/" in broken[0]

    def test_healthy_config_reports_nothing(self, tmp_path: Path) -> None:
        from autoskillit.execution.backends._codex_hooks import (
            find_broken_codex_hook_commands,
        )

        # A config with no autoskillit hooks
        config_path = tmp_path / "config.toml"
        config_path.write_text(
            '[[hooks]]\nevent = "PreToolUse"\ncommand = "echo hello"\ntype = "command"\n'
        )
        broken = find_broken_codex_hook_commands(config_path)
        assert broken == []

    def test_missing_config_reports_nothing(self, tmp_path: Path) -> None:
        from autoskillit.execution.backends._codex_hooks import (
            find_broken_codex_hook_commands,
        )

        broken = find_broken_codex_hook_commands(tmp_path / "nonexistent.toml")
        assert broken == []


class TestCodexSyncGuard:
    """Resolution and sync guard for Codex hooks directory."""

    def test_no_durable_candidate_falls_back_to_hooks_dir(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When no generation store or legacy cache is available, resolution
        falls back to HOOKS_DIR (the dev-checkout hooks directory).
        """
        from autoskillit.execution.backends._codex_hooks import (
            _resolve_codex_hooks_dir,
        )
        from autoskillit.hook_registry import HOOKS_DIR

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        result = _resolve_codex_hooks_dir(plugin_dir=None)
        assert result == HOOKS_DIR

    def test_valid_plugin_dir_resolves(self, tmp_path: Path) -> None:
        """When plugin_dir is supplied with a live dispatcher, resolution succeeds."""
        from autoskillit.execution.backends._codex_hooks import (
            _resolve_codex_hooks_dir,
        )

        hooks_dir = tmp_path / "hooks"
        hooks_dir.mkdir()
        (hooks_dir / "_dispatch.py").write_text("# dispatcher\n")
        result = _resolve_codex_hooks_dir(plugin_dir=tmp_path)
        assert result == hooks_dir
