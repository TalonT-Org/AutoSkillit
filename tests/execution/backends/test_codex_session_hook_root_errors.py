"""``codex_session_hook_root_errors`` — the per-session hook-root detector.

Unit half: the three checks (dangling dispatcher, non-canonical dispatcher,
more than one dispatcher root) against hand-built configs. Wiring half:
``CodexBackend.probe_launch_readiness`` and the managed-route write-time
self-check both surface these errors.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from autoskillit.core import SemanticAdaptationContext, resolve_executable_launch_binding
from autoskillit.execution.backends import CodexBackend
from autoskillit.execution.backends._codex_config import _serialize_toml
from autoskillit.execution.backends._codex_hooks import codex_session_hook_root_errors
from tests.execution.backends._codex_fixtures import managed_source_home, use_bundled_catalog
from tests.fixtures.hook_topology import projection_shaped_hook_root

pytestmark = [pytest.mark.layer("execution"), pytest.mark.medium]


def _write_single_command_config(config_path: Path, dispatcher: Path) -> None:
    hooks_table = {
        "PreToolUse": [
            {
                "matcher": "Bash",
                "hooks": [
                    {
                        "type": "command",
                        "command": f"python3 -B {dispatcher} guards/example_guard",
                    }
                ],
            }
        ]
    }
    config_path.write_text(_serialize_toml({"hooks": hooks_table}), encoding="utf-8")


class TestCodexSessionHookRootErrorsUnit:
    """The three checks, against configs written into tmp homes."""

    def test_reports_dangling_dispatcher(self, tmp_path: Path) -> None:
        dispatcher = tmp_path / "hooks" / "_dispatch.py"
        config_path = tmp_path / "config.toml"
        _write_single_command_config(config_path, dispatcher)

        errors = codex_session_hook_root_errors(config_path)

        assert errors
        assert any(str(dispatcher) in error for error in errors)

    def test_reports_noncanonical_dispatcher_path(self, tmp_path: Path) -> None:
        real_root = tmp_path / "real"
        (real_root / "hooks").mkdir(parents=True)
        (real_root / "hooks" / "_dispatch.py").write_text("# dispatcher\n", encoding="utf-8")
        current = tmp_path / "current"
        current.symlink_to(real_root, target_is_directory=True)
        dispatcher = current / "hooks" / "_dispatch.py"
        config_path = tmp_path / "config.toml"
        _write_single_command_config(config_path, dispatcher)

        errors = codex_session_hook_root_errors(config_path)

        assert errors
        assert any(str(dispatcher) in error for error in errors)

    def test_reports_more_than_one_dispatcher_root(self, tmp_path: Path) -> None:
        root_a = tmp_path / "root-a"
        (root_a / "hooks").mkdir(parents=True)
        (root_a / "hooks" / "_dispatch.py").write_text("# a\n", encoding="utf-8")
        root_b = tmp_path / "root-b"
        (root_b / "hooks").mkdir(parents=True)
        (root_b / "hooks" / "_dispatch.py").write_text("# b\n", encoding="utf-8")
        config_path = tmp_path / "config.toml"
        hooks_table = {
            "PreToolUse": [
                {
                    "matcher": "Bash",
                    "hooks": [
                        {
                            "type": "command",
                            "command": (
                                f"python3 -B {root_a / 'hooks' / '_dispatch.py'} "
                                "guards/example_guard"
                            ),
                        }
                    ],
                },
                {
                    "matcher": "Write",
                    "hooks": [
                        {
                            "type": "command",
                            "command": (
                                f"python3 -B {root_b / 'hooks' / '_dispatch.py'} "
                                "guards/other_guard"
                            ),
                        }
                    ],
                },
            ]
        }
        config_path.write_text(_serialize_toml({"hooks": hooks_table}), encoding="utf-8")

        errors = codex_session_hook_root_errors(config_path)

        assert errors

    def test_healthy_single_canonical_root_reports_nothing(self, tmp_path: Path) -> None:
        root = tmp_path / "root"
        (root / "hooks").mkdir(parents=True)
        (root / "hooks" / "_dispatch.py").write_text("# dispatcher\n", encoding="utf-8")
        config_path = tmp_path / "config.toml"
        _write_single_command_config(config_path, root / "hooks" / "_dispatch.py")

        assert codex_session_hook_root_errors(config_path) == []


def _managed_context(
    backend: CodexBackend, tmp_path: Path, route: str
) -> SemanticAdaptationContext:
    from autoskillit.server.managed_join_prelaunch import prepare_managed_join_context

    context = prepare_managed_join_context(
        backend=backend,
        configured_model="haiku",
        state_root=tmp_path / "state",
        parent_id=f"hook-root-errors-{route}",
        launch_context="interactive" if route == "interactive-parent" else "direct",
    )
    assert isinstance(context, SemanticAdaptationContext), context
    return context


class TestCodexSessionHookRootErrorsWiring:
    """The detector is wired into the launch-readiness probe and the managed-route
    write-time self-check."""

    def test_probe_launch_readiness_reports_dangling_dispatcher(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        home = tmp_path / "home"
        home.mkdir()
        monkeypatch.setattr(Path, "home", lambda: home)
        root = projection_shaped_hook_root(home)
        source_home, _raw_catalog = managed_source_home(tmp_path)
        backend = CodexBackend(source_codex_home=source_home)
        session_home = tmp_path / "session"
        session_home.mkdir()

        readiness = backend.ensure_pre_launch(session_dir=session_home, plugin_dir=root.plugin_dir)
        assert not readiness.errors, readiness.errors

        dispatcher = root.hooks_dir / "_dispatch.py"
        dispatcher.unlink()

        binary_dir = tmp_path / "bin"
        binary_dir.mkdir()
        executable_path = binary_dir / "codex"
        executable_path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        executable_path.chmod(0o755)
        executable = resolve_executable_launch_binding(
            binary_name="codex",
            environment={"PATH": str(binary_dir)},
            cwd=tmp_path,
        )

        with patch(
            "autoskillit.execution.backends._codex_probes._validate_mcp_probe",
            lambda *args, **kwargs: [],
        ):
            probed = backend.probe_launch_readiness(
                session_dir=session_home, executable=executable
            )

        assert probed.errors
        assert any(str(dispatcher) in error for error in probed.errors)

    def test_managed_route_configuration_rejects_noncanonical_dispatcher_root(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        home = tmp_path / "home"
        home.mkdir()
        monkeypatch.setattr(Path, "home", lambda: home)
        root = projection_shaped_hook_root(home)
        symlinked_plugin_dir = tmp_path / "current-plugin"
        symlinked_plugin_dir.symlink_to(root.plugin_dir, target_is_directory=True)

        source_home, raw_catalog = managed_source_home(tmp_path)
        use_bundled_catalog(monkeypatch, raw_catalog)
        backend = CodexBackend(source_codex_home=source_home)
        session_home = tmp_path / "session"
        session_home.mkdir()

        readiness = backend.ensure_pre_launch(session_dir=session_home, plugin_dir=root.plugin_dir)
        assert not readiness.errors, readiness.errors

        context = _managed_context(backend, tmp_path, "leaf")

        with pytest.raises(ValueError) as excinfo:
            backend.configure_managed_session_dir(
                session_home,
                adaptation_context=context,
                route="leaf",
                plugin_dir=symlinked_plugin_dir,
            )

        dispatcher = symlinked_plugin_dir / "hooks" / "_dispatch.py"
        assert str(dispatcher) in str(excinfo.value)
