"""Every Codex-rendered hook command executes in every deployment topology.

Topologies:

- ``selector``: the bindingless global render, rooted at the version-independent
  plugin-level generation selector (a directory symlink).
- ``session-<route>``: the final per-session ``config.toml`` after the prelaunch
  writer and, for managed routes, the managed-route writer, rooted at a leased
  projection-shaped plugin tree.

The inventory of commands comes from the live renderer; each command is run the
way Codex runs it (``shlex.split`` of the baked command string).
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest

from autoskillit.core import (
    CodexRuntimeSpec,
    ManagedCodexRoute,
    SemanticAdaptationContext,
    pkg_root,
)
from autoskillit.execution.backends._codex_hooks import (
    CodexHookCommand,
    generate_codex_hooks_config,
    iter_codex_hook_commands,
)
from autoskillit.hooks._runtime._hook_constants import DENY_TRIGGER_BY_GUARD
from tests.execution.backends._codex_fixtures import managed_source_home, use_bundled_catalog
from tests.fixtures.hook_topology import (
    benign_event_payload,
    build_generation_selector_topology,
    codex_command_invocations,
    hook_run_failures,
    projection_shaped_hook_root,
    run_codex_hook,
)

pytestmark = [pytest.mark.layer("hooks"), pytest.mark.large]

_ROUTES: tuple[ManagedCodexRoute | None, ...] = (None, "parent", "leaf", "interactive-parent")
_SELECTOR = "selector"

HookKey = tuple[str, str | None, str | None, int]


def _session_topology(route: ManagedCodexRoute | None) -> str:
    return f"session-{route or 'unmanaged'}"


_TOPOLOGY_ROUTES: dict[str, ManagedCodexRoute | None] = {
    _SELECTOR: None,
    **{_session_topology(route): route for route in _ROUTES},
}


def _keyed(hooks: Iterator[CodexHookCommand]) -> dict[HookKey, CodexHookCommand]:
    seen: Counter[tuple[str, str | None, str | None]] = Counter()
    keyed: dict[HookKey, CodexHookCommand] = {}
    for hook in hooks:
        base = (hook.event, hook.matcher, hook.logical_name)
        seen[base] += 1
        keyed[(*base, seen[base])] = hook
    return keyed


def _render(topology: str) -> dict[str, list[dict]]:
    if topology == _SELECTOR:
        return generate_codex_hooks_config(plugin_dir=pkg_root())
    return generate_codex_hooks_config(
        plugin_dir=pkg_root(),
        managed_route=_TOPOLOGY_ROUTES[topology],
        include_runtime_only=True,
    )


def _inventory() -> list[object]:
    params: list[object] = []
    for topology in _TOPOLOGY_ROUTES:
        for key in _keyed(iter_codex_hook_commands(_render(topology))):
            event, _matcher, logical, occurrence = key
            suffix = f"-{occurrence}" if occurrence > 1 else ""
            params.append(pytest.param(topology, key, id=f"{topology}-{event}-{logical}{suffix}"))
    return params


def _headless_env(topology: str) -> dict[str, str]:
    if _TOPOLOGY_ROUTES[topology] == "interactive-parent":
        return {"AUTOSKILLIT_SESSION_TYPE": "skill"}
    return {"AUTOSKILLIT_HEADLESS": "1"}


@dataclass(frozen=True)
class _Topologies:
    home: Path
    project: Path
    hooks: dict[str, dict[HookKey, CodexHookCommand]]


def _managed_context(
    backend: object, base: Path, route: ManagedCodexRoute
) -> SemanticAdaptationContext:
    from autoskillit.server.managed_join_prelaunch import prepare_managed_join_context

    context = prepare_managed_join_context(
        backend=backend,  # type: ignore[arg-type]
        configured_model="haiku",
        state_root=base / "state",
        parent_id=f"topology-{route}",
        launch_context="interactive" if route == "interactive-parent" else "direct",
    )
    assert isinstance(context, SemanticAdaptationContext), context
    return context


@pytest.fixture(scope="module")
def topologies(tmp_path_factory: pytest.TempPathFactory) -> _Topologies:
    from autoskillit.execution import codex_prelaunch_transaction
    from autoskillit.execution.backends import CodexBackend

    base = tmp_path_factory.mktemp("codex-topology")
    home = base / "home"
    home.mkdir()
    project = base / "project"
    (project / ".autoskillit").mkdir(parents=True)
    (base / ".autoskillit").mkdir()
    hooks: dict[str, dict[HookKey, CodexHookCommand]] = {}
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(Path, "home", lambda: home)
        build_generation_selector_topology(home)
        hooks[_SELECTOR] = _keyed(iter_codex_hook_commands(generate_codex_hooks_config()))

        root = projection_shaped_hook_root(home)
        source_home, raw_catalog = managed_source_home(base)
        use_bundled_catalog(mp, raw_catalog)
        backend = CodexBackend(source_codex_home=source_home)
        for route in _ROUTES:
            session_home = base / _session_topology(route)
            session_home.mkdir()
            with codex_prelaunch_transaction(
                source_codex_home=source_home,
                destination_home=session_home,
                runtime_spec=CodexRuntimeSpec(),
                plugin_dir=root.plugin_dir,
            ):
                pass
            if route is not None:
                backend.configure_managed_session_dir(
                    session_home,
                    adaptation_context=_managed_context(backend, base, route),
                    route=route,
                    plugin_dir=root.plugin_dir,
                )
            hooks[_session_topology(route)] = _keyed(
                hook
                for hook, _payload in codex_command_invocations(
                    session_home / "config.toml", project
                )
            )
    return _Topologies(home=home, project=project, hooks=hooks)


@pytest.mark.parametrize("topology", list(_TOPOLOGY_ROUTES))
def test_topology_renders_the_live_inventory(topologies: _Topologies, topology: str) -> None:
    assert set(topologies.hooks[topology]) == set(
        _keyed(iter_codex_hook_commands(_render(topology)))
    )


@pytest.mark.parametrize(("topology", "key"), _inventory())
def test_every_codex_hook_executes(
    topologies: _Topologies,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    topology: str,
    key: HookKey,
) -> None:
    monkeypatch.setattr(Path, "home", lambda: topologies.home)
    hook = topologies.hooks[topology][key]
    run = run_codex_hook(
        hook.command,
        benign_event_payload(hook, topologies.project),
        cwd=topologies.project,
        log_dir=tmp_path / "logs",
        env=_headless_env(topology),
    )

    assert not hook_run_failures(run), (hook.command, hook_run_failures(run))


@pytest.mark.parametrize(
    "topology",
    [topology for topology, route in _TOPOLOGY_ROUTES.items() if route != "interactive-parent"],
)
def test_codex_hook_policy_survives_every_topology(
    topologies: _Topologies,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    topology: str,
) -> None:
    monkeypatch.setattr(Path, "home", lambda: topologies.home)
    hook = next(
        hook
        for hook in topologies.hooks[topology].values()
        if hook.logical_name == "guards/test_runner_guard"
    )
    run = run_codex_hook(
        hook.command,
        {"tool_name": "Bash", "tool_input": {"command": "python -m pytest tests/x"}},
        cwd=topologies.project,
        log_dir=tmp_path / "logs",
        env={"AUTOSKILLIT_HEADLESS": "1"},
    )

    assert run.completed.returncode == 0, run.completed.stderr
    decision = json.loads(run.completed.stdout)["hookSpecificOutput"]
    assert decision["permissionDecision"] == "deny"
    assert decision["permissionDecisionReason"].startswith(
        DENY_TRIGGER_BY_GUARD["test_runner_guard"]
    )
