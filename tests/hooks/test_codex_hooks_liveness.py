"""T-B6: Codex config liveness — detection and sync-time hooks resolution.

Detection: broken config.toml hooks → structured findings; healthy → none.
Resolution: invalid explicit plugin dir → error; missing installed candidates →
dev-checkout fallback; live durable dir → writes as today.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("hooks"), pytest.mark.medium]


class TestFindBrokenCodexHookCommands:
    """Detection: broken Codex hook commands are reported.

    Fixtures are round trips through the production writers — the shape they
    emit (``dict[event, list[{matcher?, hooks: [{command}]}]]``) is not the
    legacy top-level ``[[hooks]]`` list no writer produces.
    """

    def test_broken_dispatcher_reported(self, tmp_path: Path) -> None:
        from autoskillit.execution.backends._codex_hooks import (
            find_broken_codex_hook_commands,
            sync_managed_codex_hooks_to_config,
        )
        from tests.fixtures.hook_topology import projection_shaped_hook_root

        home = tmp_path / "home"
        home.mkdir()
        root = projection_shaped_hook_root(home)
        config_path = tmp_path / "config.toml"
        sync_managed_codex_hooks_to_config(config_path, route="leaf", plugin_dir=root.plugin_dir)

        dispatcher = root.hooks_dir / "_dispatch.py"
        dispatcher.unlink()

        broken = find_broken_codex_hook_commands(config_path)
        assert broken
        assert all(str(dispatcher) in command for command in broken)

    def test_healthy_config_reports_nothing(self, tmp_path: Path) -> None:
        from autoskillit.execution.backends._codex_hooks import (
            find_broken_codex_hook_commands,
            sync_managed_codex_hooks_to_config,
        )
        from tests.fixtures.hook_topology import projection_shaped_hook_root

        home = tmp_path / "home"
        home.mkdir()
        root = projection_shaped_hook_root(home)
        config_path = tmp_path / "config.toml"
        sync_managed_codex_hooks_to_config(config_path, route="leaf", plugin_dir=root.plugin_dir)

        assert find_broken_codex_hook_commands(config_path) == []

    def test_missing_config_reports_nothing(self, tmp_path: Path) -> None:
        from autoskillit.execution.backends._codex_hooks import (
            find_broken_codex_hook_commands,
        )

        broken = find_broken_codex_hook_commands(tmp_path / "nonexistent.toml")
        assert broken == []

    def test_corrupt_config_with_autoskillit_blocks_and_missing_dispatcher_is_reported(
        self, tmp_path: Path
    ) -> None:
        from autoskillit.execution.backends._codex_hooks import (
            _upsert_hooks_text,
            find_broken_codex_hook_commands,
            generate_codex_hooks_config,
        )
        from tests.fixtures.hook_topology import projection_shaped_hook_root

        home = tmp_path / "home"
        home.mkdir()
        root = projection_shaped_hook_root(home)
        config_path = tmp_path / "config.toml"
        fresh_hooks = generate_codex_hooks_config(plugin_dir=root.plugin_dir)
        _upsert_hooks_text(config_path, b"not = [valid toml\n", fresh_hooks)

        dispatcher = root.hooks_dir / "_dispatch.py"
        dispatcher.unlink()

        broken = find_broken_codex_hook_commands(config_path)
        assert broken
        assert all(str(dispatcher) in command for command in broken)


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
