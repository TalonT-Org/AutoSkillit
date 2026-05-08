"""Structural test: every hook script is registered in HOOK_REGISTRY."""

from pathlib import Path

from autoskillit.hook_registry import HOOK_REGISTRY

HOOKS_DIR = Path(__file__).resolve().parents[2] / "src" / "autoskillit" / "hooks"


def _all_hook_script_relpaths() -> set[str]:
    """Return all non-private, non-init .py files under HOOKS_DIR as HOOKS_DIR-relative paths.

    Uses rglob to find scripts in subdirectories (guards/, formatters/) as well
    as the flat hooks root.  Underscore-prefixed and ``__init__.py`` files are
    excluded because they are private helper modules, not standalone scripts.
    """
    return {
        str(p.relative_to(HOOKS_DIR))
        for p in HOOKS_DIR.rglob("*.py")
        if p.name != "__init__.py" and not p.name.startswith("_")
    }


def test_all_pretooluse_hook_scripts_are_registered() -> None:
    """Every hook .py (excl. PostToolUse, SessionStart, __init__, private helpers)
    is PreToolUse-registered.

    Underscore-prefixed modules (e.g. ``_fmt_primitives.py``) are private helper
    modules imported by hook scripts, not standalone hook scripts themselves, and
    are excluded from registration coverage.

    After the guards/formatters subdirectory reorganization, HOOK_REGISTRY entries
    carry subfolder-prefixed paths (e.g. ``"guards/quota_guard.py"``).  This test
    compares HOOKS_DIR-relative paths so the comparison is consistent.
    """
    post_or_session_registered = {
        script
        for hd in HOOK_REGISTRY
        if hd.event_type in ("PostToolUse", "SessionStart")
        for script in hd.scripts
    }
    hook_files = {
        relpath
        for relpath in _all_hook_script_relpaths()
        if relpath not in post_or_session_registered
    }
    registered_scripts: set[str] = set()
    for hook_def in HOOK_REGISTRY:
        if hook_def.event_type == "PreToolUse":
            registered_scripts.update(hook_def.scripts)

    unregistered = hook_files - registered_scripts
    assert not unregistered, (
        f"Hook scripts exist but are not in HOOK_REGISTRY: {sorted(unregistered)}"
    )


def test_all_posttooluse_hook_scripts_are_registered() -> None:
    """Every .py hook not PreToolUse- or SessionStart-registered is PostToolUse-registered."""
    session_registered = {
        script for hd in HOOK_REGISTRY if hd.event_type == "SessionStart" for script in hd.scripts
    }
    registered_post = {
        script for hd in HOOK_REGISTRY if hd.event_type == "PostToolUse" for script in hd.scripts
    }
    all_scripts = _all_hook_script_relpaths()
    pre_registered = {
        script for hd in HOOK_REGISTRY if hd.event_type == "PreToolUse" for script in hd.scripts
    }
    post_only = all_scripts - pre_registered - session_registered
    unregistered = post_only - registered_post
    assert not unregistered, f"PostToolUse scripts not registered: {unregistered}"


def test_all_session_start_hook_scripts_are_registered() -> None:
    """Every SessionStart-registered script exists on disk."""
    session_scripts = {
        script for hd in HOOK_REGISTRY if hd.event_type == "SessionStart" for script in hd.scripts
    }
    for script in session_scripts:
        assert (HOOKS_DIR / script).exists(), f"SessionStart script not found: {script}"


# T-GUARD-2
def test_generated_file_write_guard_registered() -> None:
    """guards/generated_file_write_guard.py must be registered in HOOK_REGISTRY."""
    all_scripts = {s for h in HOOK_REGISTRY for s in h.scripts}
    assert "guards/generated_file_write_guard.py" in all_scripts, (
        "guards/generated_file_write_guard.py must be registered in HOOK_REGISTRY"
    )


def test_registry_has_ask_user_question_pre_kitchen_gate():
    from autoskillit.hook_registry import HOOK_REGISTRY

    ask_matchers = [
        h for h in HOOK_REGISTRY if h.event_type == "PreToolUse" and h.matcher == "AskUserQuestion"
    ]
    assert len(ask_matchers) == 1
    assert ask_matchers[0].scripts == ["guards/ask_user_question_guard.py"]


def test_retired_script_basenames_exists_and_complete() -> None:
    from autoskillit.hook_registry import RETIRED_SCRIPT_BASENAMES

    required = {
        "quota_check.py",
        "skill_cmd_check.py",
        "quota_post_check.py",
        "pretty_output.py",
        "token_summary_appender.py",
        "session_start_reminder.py",
        "headless_orchestration_guard.py",
    }
    missing = required - set(RETIRED_SCRIPT_BASENAMES)
    assert not missing, f"RETIRED_SCRIPT_BASENAMES is missing: {missing}"


def test_no_retired_name_has_a_live_file() -> None:
    from autoskillit.hook_registry import HOOKS_DIR, RETIRED_SCRIPT_BASENAMES

    for name in RETIRED_SCRIPT_BASENAMES:
        assert not (HOOKS_DIR / name).exists(), (
            f"Retired script name '{name}' has a live file at {HOOKS_DIR / name}. "
            "Remove the file or remove it from RETIRED_SCRIPT_BASENAMES."
        )


def test_is_own_hook_recognizes_retired_basename_orphan() -> None:
    from autoskillit.hook_registry import _is_own_hook

    orphan = "python3 /opt/some/cache/pretty_output.py"
    assert _is_own_hook(orphan), (
        "_is_own_hook must recognize retired script basenames as autoskillit-owned"
    )


def test_hook_registry_hash_is_deterministic() -> None:
    from autoskillit.hook_registry import HOOK_REGISTRY_HASH

    assert isinstance(HOOK_REGISTRY_HASH, str)
    assert len(HOOK_REGISTRY_HASH) == 64


def test_compute_registry_hash_is_stable_across_invocations() -> None:
    from autoskillit.hook_registry import (
        HOOK_REGISTRY,
        RETIRED_SCRIPT_BASENAMES,
        compute_registry_hash,
    )

    a = compute_registry_hash(HOOK_REGISTRY, RETIRED_SCRIPT_BASENAMES)
    b = compute_registry_hash(list(HOOK_REGISTRY), frozenset(RETIRED_SCRIPT_BASENAMES))
    assert a == b


def test_hook_registry_hash_changes_on_registry_mutation() -> None:
    from autoskillit.hook_registry import (
        HOOK_REGISTRY,
        HOOK_REGISTRY_HASH,
        RETIRED_SCRIPT_BASENAMES,
        HookDef,
        compute_registry_hash,
    )

    mutated = list(HOOK_REGISTRY) + [
        HookDef(event_type="PreToolUse", matcher="X", scripts=["x.py"])
    ]
    assert compute_registry_hash(mutated, RETIRED_SCRIPT_BASENAMES) != HOOK_REGISTRY_HASH


def test_hook_registry_hash_changes_on_retired_mutation() -> None:
    from autoskillit.hook_registry import (
        HOOK_REGISTRY,
        HOOK_REGISTRY_HASH,
        RETIRED_SCRIPT_BASENAMES,
        compute_registry_hash,
    )

    mutated_retired = frozenset(RETIRED_SCRIPT_BASENAMES | {"extra_retired.py"})
    assert compute_registry_hash(HOOK_REGISTRY, mutated_retired) != HOOK_REGISTRY_HASH


# T4-1
def test_review_gate_post_hook_registered_in_hook_registry() -> None:
    """review_gate_post_hook.py must be registered as a PostToolUse hook."""
    post_scripts = {
        s for hd in HOOK_REGISTRY if hd.event_type == "PostToolUse" for s in hd.scripts
    }
    assert "review_gate_post_hook.py" in post_scripts, (
        "review_gate_post_hook.py must be registered as a PostToolUse hook in HOOK_REGISTRY"
    )


# T4-2
def test_review_loop_gate_registered_in_hook_registry() -> None:
    """guards/review_loop_gate.py must be registered as a PreToolUse hook."""
    pre_scripts = {s for hd in HOOK_REGISTRY if hd.event_type == "PreToolUse" for s in hd.scripts}
    assert "guards/review_loop_gate.py" in pre_scripts, (
        "guards/review_loop_gate.py must be registered as a PreToolUse hook in HOOK_REGISTRY"
    )


# T4-3
def test_review_loop_gate_matcher_covers_wait_for_ci_and_enqueue_pr() -> None:
    """guards/review_loop_gate.py matcher must cover both wait_for_ci and enqueue_pr."""
    import re

    gate_entries = [
        hd
        for hd in HOOK_REGISTRY
        if hd.event_type == "PreToolUse" and "guards/review_loop_gate.py" in hd.scripts
    ]
    assert gate_entries, "No PreToolUse entry found for guards/review_loop_gate.py"
    matcher = gate_entries[0].matcher
    assert re.search(r"wait_for_ci", matcher), f"Matcher must cover wait_for_ci; got: {matcher!r}"
    assert re.search(r"enqueue_pr", matcher), f"Matcher must cover enqueue_pr; got: {matcher!r}"


def test_skill_load_post_hook_registered_in_hook_registry() -> None:
    """T3-1: skill_load_post_hook.py must be registered as a PostToolUse hook."""
    post_scripts = {
        s for hd in HOOK_REGISTRY if hd.event_type == "PostToolUse" for s in hd.scripts
    }
    assert "skill_load_post_hook.py" in post_scripts, (
        "skill_load_post_hook.py must be registered as a PostToolUse hook in HOOK_REGISTRY"
    )


def test_skill_load_guard_registered_in_hook_registry() -> None:
    """T3-2: guards/skill_load_guard.py must be registered as a PreToolUse hook."""
    pre_scripts = {s for hd in HOOK_REGISTRY if hd.event_type == "PreToolUse" for s in hd.scripts}
    assert "guards/skill_load_guard.py" in pre_scripts, (
        "guards/skill_load_guard.py must be registered as a PreToolUse hook in HOOK_REGISTRY"
    )


def test_skill_load_guard_matcher_covers_native_tools() -> None:
    """T3-3: guards/skill_load_guard.py matcher must cover all native tools."""
    import re

    guard_entries = [
        hd
        for hd in HOOK_REGISTRY
        if hd.event_type == "PreToolUse" and "guards/skill_load_guard.py" in hd.scripts
    ]
    assert guard_entries, "No PreToolUse entry found for guards/skill_load_guard.py"
    matcher = guard_entries[0].matcher
    for tool in ("Read", "Write", "Edit", "Bash", "Grep", "Glob"):
        assert re.search(tool, matcher), f"Matcher must cover {tool}; got: {matcher!r}"


def test_skill_load_post_hook_matcher_is_skill() -> None:
    """T3-4: skill_load_post_hook.py matcher must match Skill."""
    import re

    entries = [
        hd
        for hd in HOOK_REGISTRY
        if hd.event_type == "PostToolUse" and "skill_load_post_hook.py" in hd.scripts
    ]
    assert entries, "No PostToolUse entry found for skill_load_post_hook.py"
    assert re.search("Skill", entries[0].matcher), (
        f"Matcher must match Skill; got: {entries[0].matcher!r}"
    )
