"""A per-session Codex hook table has exactly one typed, leased root."""

from __future__ import annotations

import os
import tomllib
from pathlib import Path

import pytest

from autoskillit.core import (
    ManagedCodexRoute,
    PluginLoadMode,
    SemanticAdaptationContext,
    SessionHookRoot,
    _InstallLock,
    managed_home_for,
    plugin_launch_binding_scope,
)
from autoskillit.execution.backends import CodexBackend
from autoskillit.execution.backends._codex_hooks import (
    _is_autoskillit_hook_entry,
    generate_codex_hooks_config,
    iter_codex_hook_commands,
)
from tests.contracts._projection_helpers import projected_plugin_authority
from tests.execution.backends._codex_fixtures import managed_source_home, use_bundled_catalog
from tests.fixtures.hook_topology import projection_shaped_hook_root

pytestmark = [pytest.mark.layer("execution"), pytest.mark.medium]

_FOREIGN_HOOKS = (
    "[[hooks.PreToolUse]]\n"
    'matcher = "Bash"\n'
    "\n"
    "[[hooks.PreToolUse.hooks]]\n"
    'type = "command"\n'
    'command = "echo foreign"\n'
)


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: fake_home)
    return fake_home


def _hooks(config_path: Path) -> dict[str, list[dict]]:
    return tomllib.loads(config_path.read_text(encoding="utf-8"))["hooks"]


def _autoskillit_entries(hooks: dict[str, list[dict]]) -> dict[str, list[dict]]:
    owned = {
        event: [entry for entry in entries if _is_autoskillit_hook_entry(entry)]
        for event, entries in hooks.items()
    }
    return {event: entries for event, entries in owned.items() if entries}


def _managed_context(
    backend: CodexBackend, tmp_path: Path, route: ManagedCodexRoute
) -> SemanticAdaptationContext:
    from autoskillit.server.managed_join_prelaunch import prepare_managed_join_context

    context = prepare_managed_join_context(
        backend=backend,
        configured_model="haiku",
        state_root=tmp_path / "state",
        parent_id=f"hook-root-{route}",
        launch_context="interactive" if route == "interactive-parent" else "direct",
    )
    assert isinstance(context, SemanticAdaptationContext), context
    return context


def test_prelaunch_without_session_hook_root_fails_closed(tmp_path: Path, home: Path) -> None:
    source_home, _raw_catalog = managed_source_home(tmp_path)
    session_home = tmp_path / "session"
    session_home.mkdir()

    readiness = CodexBackend(source_codex_home=source_home).ensure_pre_launch(
        session_dir=session_home
    )

    assert readiness.errors
    assert any("session hook root" in error for error in readiness.errors)
    assert not (session_home / "config.toml").exists()


@pytest.mark.parametrize("route", [None, "parent", "leaf", "interactive-parent"])
def test_final_session_config_matches_single_render(
    tmp_path: Path,
    home: Path,
    monkeypatch: pytest.MonkeyPatch,
    route: ManagedCodexRoute | None,
) -> None:
    (tmp_path / ".autoskillit").mkdir(exist_ok=True)
    root = projection_shaped_hook_root(home)
    source_home, raw_catalog = managed_source_home(tmp_path)
    (source_home / "config.toml").write_text(_FOREIGN_HOOKS, encoding="utf-8")
    use_bundled_catalog(monkeypatch, raw_catalog)
    backend = CodexBackend(source_codex_home=source_home)
    session_home = tmp_path / "session"
    session_home.mkdir()

    readiness = backend.ensure_pre_launch(session_dir=session_home, plugin_dir=root.plugin_dir)
    assert not readiness.errors, readiness.errors
    if route is not None:
        backend.configure_managed_session_dir(
            session_home,
            adaptation_context=_managed_context(backend, tmp_path, route),
            route=route,
            plugin_dir=root.plugin_dir,
        )

    final = _hooks(session_home / "config.toml")
    expected = generate_codex_hooks_config(
        plugin_dir=root.plugin_dir,
        managed_route=route,
        include_runtime_only=True,
    )
    assert _autoskillit_entries(final) == expected
    assert final["PreToolUse"][0]["hooks"][0]["command"] == "echo foreign"
    dispatchers = {
        hook.dispatcher for hook in iter_codex_hook_commands(expected) if hook.dispatcher
    }
    assert dispatchers == {root.hooks_dir / "_dispatch.py"}
    assert "guards/auto_compact_guard" in {
        hook.logical_name for hook in iter_codex_hook_commands(final)
    }


def _publish_minimal_generation(home: Path, tmp_path: Path, version: str) -> None:
    from autoskillit.workspace import publish_generation

    source_root = tmp_path / f"generation-source-{version}"
    (source_root / "hooks").mkdir(parents=True)
    (source_root / "hooks" / "_dispatch.py").write_text(f"# {version}\n", encoding="utf-8")
    managed = managed_home_for(home)
    with _InstallLock(managed):
        publish_generation(
            home=managed,
            plugin_ref="autoskillit",
            version=version,
            semantic_key=f"autoskillit@autoskillit-local:{version}",
            source_root=source_root,
        )


def test_active_session_hook_root_survives_selector_repoint(tmp_path: Path, home: Path) -> None:
    _publish_minimal_generation(home, tmp_path, "1.0.0")
    source_home, _raw_catalog = managed_source_home(tmp_path)
    backend = CodexBackend(source_codex_home=source_home)
    session_home = tmp_path / "session"
    session_home.mkdir()

    with plugin_launch_binding_scope(
        authority=projected_plugin_authority(tmp_path),
        backend=backend,
        load_mode=PluginLoadMode.PROJECTED_HOME,
    ) as binding:
        assert binding is not None
        root = SessionHookRoot.from_binding(binding)
        readiness = backend.ensure_pre_launch(session_dir=session_home, plugin_dir=root.plugin_dir)
        assert not readiness.errors, readiness.errors
        before = _autoskillit_entries(_hooks(session_home / "config.toml"))

        _publish_minimal_generation(home, tmp_path, "2.0.0")

        after = _autoskillit_entries(_hooks(session_home / "config.toml"))
        assert after == before
        for hook in iter_codex_hook_commands(after):
            assert hook.dispatcher == root.hooks_dir / "_dispatch.py"
            assert os.path.realpath(hook.dispatcher) == str(hook.dispatcher)
