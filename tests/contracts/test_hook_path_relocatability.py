"""Contract: published hook commands are relocatable, not process-local.

Sibling idiom to tests/contracts/test_install_state_consistency.py. Every
hooks.json AutoSkillit publishes is a redistributed plugin artifact — its
validity must be a property of the artifact itself (self-referential via
Claude Code's ``${CLAUDE_PLUGIN_ROOT}`` expansion), never of the venv
interpreter, install path, or continued existence of the process that
generated it (issue #4469: an interpreter-version-dependent absolute path
baked into every published hook command caused a total session lockout when
``uv tool install --force`` rebuilt the venv on a different Python).
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

from autoskillit.core import pkg_root
from autoskillit.hook_registry import PLUGIN_ROOT_TOKEN, generate_hooks_json

pytestmark = [pytest.mark.layer("contracts"), pytest.mark.medium]


# ---------------------------------------------------------------------------
# T-A1 — Architectural guard: no process-local path in any published hook
# artifact.
# ---------------------------------------------------------------------------


def test_generate_hooks_json_commands_are_relocatable() -> None:
    """Every command produced by generate_hooks_json() uses the plugin-root
    token and contains no process-local path segment.

    Fails before A-I1: commands embed the pkg_root()-derived absolute path.
    """
    forbidden_segments = (
        "site-packages",
        "/lib/python",
        "uv/tools",
        sys.prefix,
        str(pkg_root()),
    )
    data = generate_hooks_json()
    expected_prefix = f'python3 "{PLUGIN_ROOT_TOKEN}/hooks/_dispatch.py" '
    for event_type, entries in data["hooks"].items():
        for entry in entries:
            for hook in entry.get("hooks", []):
                cmd = hook["command"]
                assert cmd.startswith(expected_prefix), (
                    f"{event_type} command is not in relocatable form: {cmd}"
                )
                for segment in forbidden_segments:
                    assert segment not in cmd, (
                        f"{event_type} command embeds a process-local path "
                        f"segment {segment!r}: {cmd}"
                    )


def test_compute_registry_hash_is_identical_for_absolute_and_relocatable_renderings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """compute_registry_hash hashes logical registry rows (scripts, event
    type, matcher, timeout) — never the rendered command string — so the
    embedded ``_autoskillit_registry_hash`` must be identical whether the
    hooks are rendered in relocatable (hooks.json) form or absolute
    (settings.json, dev-mode) form. A-I1 states this needs no
    implementation change; this test pins the invariant so a future
    refactor that accidentally threads the rendered command into the hash
    cannot regress silently.
    """
    import autoskillit.cli._hooks as _hooks_mod
    from autoskillit.cli._hooks import sync_hooks_to_settings

    # sync_hooks_to_settings() refuses to run from a git worktree (its own
    # dev-machine safety guard, unrelated to the property under test) and
    # short-circuits to an eviction-only no-op when the plugin is installed
    # — same idiom as tests/cli/conftest.py's autouse fixture.
    monkeypatch.setattr(_hooks_mod, "is_git_worktree", lambda _path: False)
    monkeypatch.setattr(
        "autoskillit.cli._init_helpers._is_plugin_installed", lambda **kwargs: False
    )

    relocatable_hash = generate_hooks_json()["_autoskillit_registry_hash"]

    settings_path = tmp_path / "settings.json"
    sync_hooks_to_settings(settings_path)
    absolute_hash = json.loads(settings_path.read_text())["_autoskillit_registry_hash"]

    assert relocatable_hash == absolute_hash


# ---------------------------------------------------------------------------
# T-A2 — Self-healed bundled hooks.json is canonical and machine-independent.
#
# Adapted from the plan's original wording ("checked-in bundled hooks.json"):
# src/autoskillit/hooks/hooks.json is a generated, gitignored artifact in
# this repo (core.paths.GENERATED_FILES), never checked in — there is no
# committed file to compare against. This pins the equivalent property
# against the real self-heal write path instead (run_startup_drift_check).
# ---------------------------------------------------------------------------


def test_self_healed_bundled_hooks_json_is_relocatable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The dev-checkout hooks.json copy, self-healed at MCP startup, must be
    machine-independent: relocatable-token form only, no process-local path.
    """
    import autoskillit.core.paths as _paths
    from autoskillit.server._lifespan import run_startup_drift_check

    fake_pkg_root = tmp_path / "pkg"
    hooks_dir = fake_pkg_root / "hooks"
    hooks_dir.mkdir(parents=True)
    stale_json = {"_autoskillit_registry_hash": "deadbeef", "hooks": {}}
    (hooks_dir / "hooks.json").write_text(json.dumps(stale_json))

    monkeypatch.setattr(_paths, "pkg_root", lambda: fake_pkg_root)

    run_startup_drift_check()

    regenerated = (hooks_dir / "hooks.json").read_text()
    forbidden_segments = ("site-packages", "/lib/python", "uv/tools", sys.prefix)
    for segment in forbidden_segments:
        assert segment not in regenerated, (
            f"self-healed hooks.json embeds a process-local path segment {segment!r}"
        )
    assert PLUGIN_ROOT_TOKEN in regenerated


# ---------------------------------------------------------------------------
# T-A3 — Token-aware validation.
# ---------------------------------------------------------------------------


def _write_cache_hooks_json(hooks_dir: Path, *, commands: list[str]) -> None:
    hooks_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "hooks": {
            "PreToolUse": [
                {
                    "matcher": ".*",
                    "hooks": [{"type": "command", "command": cmd} for cmd in commands],
                }
            ]
        }
    }
    (hooks_dir / "hooks.json").write_text(json.dumps(payload))


def test_validate_plugin_cache_hooks_token_aware(tmp_path: Path) -> None:
    """Real installed layout (<cache>/<version>/hooks/hooks.json) with a
    relocatable command expanding ${CLAUDE_PLUGIN_ROOT} against
    hooks_json_path.parent.parent (the <version> dir) reports zero broken; a
    venv-absolute command whose target does not exist IS reported broken.
    """
    from autoskillit.hook_registry import validate_plugin_cache_hooks

    good_version_dir = tmp_path / "cache" / "1.2.3"
    good_hooks_dir = good_version_dir / "hooks"
    good_hooks_dir.mkdir(parents=True)
    (good_hooks_dir / "_dispatch.py").write_text("# dispatcher stub")
    _write_cache_hooks_json(
        good_hooks_dir,
        commands=[f'python3 "{PLUGIN_ROOT_TOKEN}/hooks/_dispatch.py" guards/quota_guard'],
    )

    assert validate_plugin_cache_hooks(cache_dir=tmp_path / "cache") == []

    stale_version_dir = tmp_path / "cache" / "9.9.9"
    stale_hooks_dir = stale_version_dir / "hooks"
    _write_cache_hooks_json(
        stale_hooks_dir,
        commands=["python3 /nonexistent/venv/hooks/_dispatch.py guards/quota_guard"],
    )

    broken = validate_plugin_cache_hooks(cache_dir=tmp_path / "cache")
    assert len(broken) == 1
    assert "/nonexistent/venv/hooks/_dispatch.py" in broken[0]


def test_validate_plugin_cache_hooks_token_without_expansion_root_is_broken() -> None:
    """find_broken_hook_scripts fails closed: a token-bearing command with no
    expansion_root supplied is reported broken, never silently skipped.
    """
    from autoskillit.hook_registry import find_broken_hook_scripts

    with tempfile.TemporaryDirectory() as tmp:
        settings_path = Path(tmp) / "hooks.json"
        _write_cache_hooks_json(
            Path(tmp),
            commands=[f'python3 "{PLUGIN_ROOT_TOKEN}/hooks/_dispatch.py" guards/quota_guard'],
        )
        broken = find_broken_hook_scripts(settings_path)
        assert len(broken) == 1
        assert PLUGIN_ROOT_TOKEN in broken[0]


# ---------------------------------------------------------------------------
# T-A4 — Interpreter-pivot immunity.
# ---------------------------------------------------------------------------


def test_token_aware_validation_survives_interpreter_pivot(tmp_path: Path) -> None:
    """Renaming or deleting the venv's lib/pythonX.Y tree must not break
    token-aware validation of a relocatable cache incarnation — the whole
    point of relocation is independence from the interpreter that generated
    the commands.
    """
    from autoskillit.hook_registry import validate_plugin_cache_hooks

    # A fake venv tree, present at generation time — never actually
    # referenced by the relocatable command, but simulates the incident.
    venv_root = tmp_path / "venv"
    (venv_root / "lib" / "python3.13" / "site-packages" / "autoskillit" / "hooks").mkdir(
        parents=True
    )

    version_dir = tmp_path / "cache" / "1.0.0"
    hooks_dir = version_dir / "hooks"
    hooks_dir.mkdir(parents=True)
    (hooks_dir / "_dispatch.py").write_text("# dispatcher stub")
    _write_cache_hooks_json(
        hooks_dir,
        commands=[f'python3 "{PLUGIN_ROOT_TOKEN}/hooks/_dispatch.py" guards/quota_guard'],
    )

    assert validate_plugin_cache_hooks(cache_dir=tmp_path / "cache") == []

    # Pivot: rename lib/python3.13 -> lib/python3.11 (interpreter minor changed).
    (venv_root / "lib" / "python3.13").rename(venv_root / "lib" / "python3.11")
    assert validate_plugin_cache_hooks(cache_dir=tmp_path / "cache") == []

    # Pivot: delete the venv tree entirely.
    shutil.rmtree(venv_root)
    assert validate_plugin_cache_hooks(cache_dir=tmp_path / "cache") == []


# ---------------------------------------------------------------------------
# T-A5 — Live-session-safety contract (hard acceptance requirement).
# ---------------------------------------------------------------------------


def test_retained_incarnation_hooks_resolve_independently_of_newer_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Retire-don't-delete (#1993): an older, retained incarnation's hooks.json
    commands must keep resolving against its OWN hooks/ tree even after a
    newer incarnation is published and the older one is enqueued for
    retirement — hook validity is a property of the artifact directory, not
    of "the currently active version". N's directory must also still exist
    (existing grace-period behavior, now pinned by this contract test).
    """
    from datetime import UTC, datetime, timedelta

    from autoskillit.cli._plugin_artifact import (
        InstalledPluginArtifactRetirementOwner,
        publish_installed_plugin_artifact,
    )
    from autoskillit.hook_registry import validate_plugin_cache_hooks

    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    cache_dir = tmp_path / ".claude" / "plugins" / "cache" / "autoskillit-local" / "autoskillit"

    def _publish_incarnation(version: str):
        version_dir = cache_dir / version
        hooks_dir = version_dir / "hooks"
        hooks_dir.mkdir(parents=True)
        (hooks_dir / "_dispatch.py").write_text("# dispatcher stub")
        _write_cache_hooks_json(
            hooks_dir,
            commands=[f'python3 "{PLUGIN_ROOT_TOKEN}/hooks/_dispatch.py" guards/quota_guard'],
        )
        metadata = version_dir / ".claude-plugin" / "plugin.json"
        metadata.parent.mkdir(parents=True)
        metadata.write_text(json.dumps({"name": "autoskillit", "version": version}))
        return publish_installed_plugin_artifact(
            version_dir,
            semantic_key=f"autoskillit@autoskillit-local:{version}",
        )

    old_identity = _publish_incarnation("1.0.0")
    _publish_incarnation("1.1.0")

    InstalledPluginArtifactRetirementOwner(cache_dir).enqueue_retirement(
        old_identity,
        datetime.now(UTC) + timedelta(hours=6),
    )

    old_version_dir = cache_dir / "1.0.0"
    assert old_version_dir.is_dir(), "retained incarnation must not be deleted immediately"

    broken = validate_plugin_cache_hooks(cache_dir=cache_dir)
    assert broken == [], (
        "retained incarnation's relocatable commands must still resolve against "
        "its own hooks/ tree, independent of the newer published version"
    )


# ---------------------------------------------------------------------------
# T-A6 — Session-skills placeholder provenance.
# ---------------------------------------------------------------------------


def test_catalog_projection_context_accepts_durable_scripts_root(tmp_path: Path) -> None:
    """catalog_projection_context() resolves {{AUTOSKILLIT_SCRIPTS}} against an
    explicitly supplied durable_scripts_root when one is given, instead of
    always hardcoding pkg_root() (the venv tree, deletable mid-session by a
    concurrent autoskillit update). Fails before A-I5: no such parameter
    exists and pkg_root() is unconditionally hardcoded.
    """
    from autoskillit.core import SkillExecutionRole
    from autoskillit.workspace import EffectiveSkillCatalog, SkillsDirectoryProvider

    provider = SkillsDirectoryProvider(
        temp_dir_relpath=".autoskillit/temp",
        default_base_branch="develop",
    )
    catalog = EffectiveSkillCatalog(skills=(), execution_role=SkillExecutionRole.SESSION)
    durable_root = tmp_path / "retained-incarnation"

    context = provider.catalog_projection_context(
        catalog,
        tmp_path,
        durable_scripts_root=durable_root,
    )

    assert context.substitutions is not None
    assert context.substitutions["{{AUTOSKILLIT_SCRIPTS}}"] == str(
        durable_root / "recipes" / "scripts"
    )


def test_catalog_projection_context_defaults_to_pkg_root_when_unspecified(
    tmp_path: Path,
) -> None:
    """Without an explicit durable_scripts_root, pkg_root() (the dev-source
    checkout) remains the fallback — correct for callers with no plugin
    artifact binding, and keeps every pre-existing caller working unchanged.
    """
    from autoskillit.core import SkillExecutionRole, pkg_root
    from autoskillit.workspace import EffectiveSkillCatalog, SkillsDirectoryProvider

    provider = SkillsDirectoryProvider(
        temp_dir_relpath=".autoskillit/temp",
        default_base_branch="develop",
    )
    catalog = EffectiveSkillCatalog(skills=(), execution_role=SkillExecutionRole.SESSION)

    context = provider.catalog_projection_context(catalog, tmp_path)

    assert context.substitutions is not None
    assert context.substitutions["{{AUTOSKILLIT_SCRIPTS}}"] == str(
        pkg_root() / "recipes" / "scripts"
    )


def test_cook_session_resolves_durable_scripts_root_before_projection() -> None:
    """cli/session/_session_cook.py must resolve interactive_plugin_authority()
    — and, for IMPLICIT_INSTALLED sessions, the retained plugin-cache
    incarnation via current_installed_plugin_root() — BEFORE
    catalog_projection_context() runs, so the projected skill document never
    references a tree with a shorter lifetime than the session consuming it.

    Static ordering check (source-text search): cook() has many external
    side effects (TTY prompts, subprocess launch, backend resolution) that
    make a full behavioral test require deep mocking; the durable-root
    resolution contract itself is covered by
    test_catalog_projection_context_accepts_durable_scripts_root above.
    """
    import inspect

    from autoskillit.cli.session import _session_cook

    source = inspect.getsource(_session_cook.cook)
    # Search for the actual call sites, not merely the substring — both names
    # also appear in surrounding prose comments, which would give a false
    # pass/fail independent of where the real calls sit.
    authority_call = "artifact_authority, load_mode = interactive_plugin_authority("
    projection_call = "skills_provider.catalog_projection_context("
    authority_pos = source.index(authority_call)
    projection_pos = source.index(projection_call)
    assert authority_pos < projection_pos, (
        "interactive_plugin_authority() must be resolved before "
        "catalog_projection_context() so its result can supply durable_scripts_root"
    )
    assert "durable_scripts_root=durable_scripts_root" in source
    assert "current_installed_plugin_root" in source
