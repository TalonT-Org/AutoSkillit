"""Every deployed deny-scope exclusion has an explicit coverage disposition."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Literal

import pytest

import autoskillit.hooks  # noqa: F401  (populates the deferred hook registry)
from autoskillit.hook_registry import (
    HOOK_REGISTRY,
    LIFECYCLE_CONTRACTS,
    PROTECTION_WAIVERS,
    RETIRED_SCRIPT_BASENAMES,
    HookDef,
    ProtectionWaiverDef,
    compute_registry_hash,
    generate_hooks_json,
    validate_protection_coverage,
)

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]


def test_protection_waiver_registry_is_exact() -> None:
    expected = {
        (
            script,
            "interactive" if hook.session_scope == "headless_only" else "headless",
            backend,
        )
        for hook in HOOK_REGISTRY
        if hook.mechanism == "deny" and hook.session_scope != "any"
        for script in hook.scripts
        for backend in ("claude_code", "codex")
    }
    expected.update(
        {
            ("guards/background_exec_guard.py", "interactive", "codex"),
            ("guards/join_followup_guard.py", "interactive", "codex"),
            ("guards/join_stop_guard.py", "interactive", "codex"),
            ("guards/write_guard.py", "all", "codex"),
            ("guards/git_ops_guard.py", "interactive", "claude_code"),
            ("guards/git_ops_guard.py", "interactive", "codex"),
        }
    )
    actual = {
        (waiver.guard_script, waiver.excluded_scope, waiver.backend)
        for waiver in PROTECTION_WAIVERS
    }
    assert len(actual) == len(PROTECTION_WAIVERS)
    assert actual == expected


def test_waiver_content_changes_registry_hash() -> None:
    original = compute_registry_hash(
        HOOK_REGISTRY, RETIRED_SCRIPT_BASENAMES, LIFECYCLE_CONTRACTS, PROTECTION_WAIVERS
    )
    changed = (
        replace(PROTECTION_WAIVERS[0], justification="Changed coverage justification."),
        *PROTECTION_WAIVERS[1:],
    )
    assert (
        compute_registry_hash(
            HOOK_REGISTRY, RETIRED_SCRIPT_BASENAMES, LIFECYCLE_CONTRACTS, changed
        )
        != original
    )


def test_every_scope_exclusion_and_internal_branch_is_validated() -> None:
    validate_protection_coverage(HOOK_REGISTRY, PROTECTION_WAIVERS, backend="claude_code")
    validate_protection_coverage(HOOK_REGISTRY, PROTECTION_WAIVERS, backend="codex")
    assert any(
        waiver.guard_script == "guards/write_guard.py"
        and waiver.backend == "codex"
        and waiver.covering_mechanism == "codex-sandbox"
        for waiver in PROTECTION_WAIVERS
    )
    assert all(
        waiver.excluded_scope != "interactive"
        for waiver in PROTECTION_WAIVERS
        if waiver.guard_script == "guards/write_guard.py"
    )


def test_hook_delegate_must_reach_the_excluded_session() -> None:
    guard = HookDef(matcher="Bash", scripts=["guards/guard.py"], session_scope="headless_only")
    delegate = HookDef(matcher="Bash", scripts=["guards/delegate.py"])
    waiver = ProtectionWaiverDef(
        guard_script="guards/guard.py",
        excluded_scope="interactive",
        backend="claude_code",
        risk="interactive command write",
        covering_mechanism="hook",
        covering_guard_script="guards/delegate.py",
        justification="The all-session delegate checks the same write target.",
    )
    validate_protection_coverage((guard, delegate), (waiver,), backend="claude_code")
    unreachable = HookDef(
        matcher="Bash", scripts=["guards/delegate.py"], session_scope="headless_only"
    )
    with pytest.raises(ValueError, match="unreachable protection delegate"):
        validate_protection_coverage((guard, unreachable), (waiver,), backend="claude_code")


def test_missing_waiver_rejects_plugin_and_settings_rendering(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import autoskillit.cli._hooks as settings_hooks
    import autoskillit.hook_registry._rendering as rendering

    missing = tuple(
        waiver
        for waiver in PROTECTION_WAIVERS
        if not (
            waiver.guard_script == "guards/test_runner_guard.py"
            and waiver.backend == "claude_code"
        )
    )
    monkeypatch.setattr(rendering, "PROTECTION_WAIVERS", missing)
    with pytest.raises(ValueError, match="no protection waiver"):
        generate_hooks_json()

    monkeypatch.setattr(settings_hooks, "PROTECTION_WAIVERS", missing)
    monkeypatch.setattr(settings_hooks, "pkg_root", lambda: tmp_path)
    monkeypatch.setattr(settings_hooks, "is_git_worktree", lambda _root: False)
    with pytest.raises(ValueError, match="no protection waiver"):
        settings_hooks.sync_hooks_to_settings(tmp_path / "settings.json", force=True)


@pytest.mark.parametrize("route", ["parent", "leaf"])
def test_missing_managed_route_waiver_rejects_codex_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, route: Literal["parent", "leaf"]
) -> None:
    import autoskillit.execution.backends._codex_hooks as codex_hooks

    missing = tuple(
        waiver
        for waiver in PROTECTION_WAIVERS
        if not (
            waiver.guard_script == "guards/background_exec_guard.py" and waiver.backend == "codex"
        )
    )
    monkeypatch.setattr(codex_hooks, "PROTECTION_WAIVERS", missing)
    monkeypatch.setattr(codex_hooks, "_resolve_codex_hooks_dir", lambda _plugin_dir: tmp_path)
    with pytest.raises(ValueError, match="no protection waiver"):
        codex_hooks.generate_codex_hooks_config(plugin_dir=tmp_path, managed_route=route)
