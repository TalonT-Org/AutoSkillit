"""Tests for hook_registry.py — L0 hook identity model."""

from __future__ import annotations

import json
from pathlib import Path

from autoskillit.hook_registry import (
    HOOK_REGISTRY,
    HOOKS_DIR,
    _extract_script_basenames,
    _is_own_hook,
    canonical_script_basenames,
    find_broken_hook_scripts,
    generate_hooks_json,
)


# HR-FILTER-1: command with "autoskillit" substring -> True
def test_is_own_hook_autoskillit_substring() -> None:
    assert _is_own_hook("python3 /path/to/autoskillit/hooks/quota_guard.py") is True


# HR-FILTER-2: command ending with known script basename -> True
def test_is_own_hook_known_basename() -> None:
    assert _is_own_hook("python3 /some/other/path/quota_guard.py") is True


# HR-FILTER-3: unrelated command -> False
def test_is_own_hook_unrelated_command() -> None:
    assert _is_own_hook('wsl-notify-send.exe "Done!"') is False
    assert _is_own_hook("python3 /home/user/my_guard.py") is False


# HR-FILTER-4: known basename with different path prefix -> True
def test_is_own_hook_different_prefix() -> None:
    assert (
        _is_own_hook("python3 /home/user/.local/share/uv/tools/lib/hooks/skill_cmd_guard.py")
        is True
    )


# HR-BASENAME-1: canonical hooks dict -> returns set of bare filenames
def test_extract_script_basenames_canonical() -> None:
    canonical = generate_hooks_json()
    result = _extract_script_basenames(canonical.get("hooks", {}))
    expected = canonical_script_basenames()
    assert result == expected


# HR-BASENAME-2: different path prefixes -> returns same basenames
def test_extract_script_basenames_different_prefix() -> None:
    foreign_dir = (
        "/home/user/.local/share/uv/tools/autoskillit/lib/python3.13"
        "/site-packages/autoskillit/hooks"
    )
    by_event: dict[str, list[dict]] = {}
    for hdef in HOOK_REGISTRY:
        hook_commands = [
            {"type": "command", "command": f"python3 {foreign_dir}/{script}"}
            for script in hdef.scripts
        ]
        entry: dict = {"hooks": hook_commands}
        if hdef.event_type != "SessionStart":
            entry["matcher"] = hdef.matcher
        by_event.setdefault(hdef.event_type, []).append(entry)

    result = _extract_script_basenames(by_event)
    assert result == canonical_script_basenames()


# HR-BASENAME-3: mixed autoskillit + user hooks -> returns only autoskillit basenames
def test_extract_script_basenames_filters_user_hooks() -> None:
    canonical = generate_hooks_json()
    hooks = canonical["hooks"]
    hooks.setdefault("PreToolUse", []).append(
        {
            "matcher": ".*",
            "hooks": [
                {"type": "command", "command": "python3 /home/user/my_guard.py"},
                {"type": "command", "command": 'wsl-notify-send.exe "Done!"'},
            ],
        }
    )
    result = _extract_script_basenames(hooks)
    assert result == canonical_script_basenames()
    assert "my_guard.py" not in result


# ---------------------------------------------------------------------------
# T7 — Hook file renames: new names exist, old names removed
# ---------------------------------------------------------------------------

_HOOKS_DIR = Path(__file__).parent.parent.parent / "src" / "autoskillit" / "hooks"


def test_renamed_hook_files_exist() -> None:
    """New hook filenames must exist on disk after rename."""
    for expected in [
        "guards/quota_guard.py",
        "quota_post_hook.py",
        "guards/skill_cmd_guard.py",
        "formatters/pretty_output_hook.py",
        "session_start_hook.py",
        "token_summary_hook.py",
    ]:
        assert (_HOOKS_DIR / expected).exists(), f"Missing renamed hook: {expected}"


def test_old_hook_names_removed() -> None:
    """Old hook filenames must NOT exist on disk after rename."""
    for old in [
        "quota_check.py",
        "quota_post_check.py",
        "skill_cmd_check.py",
        "pretty_output.py",
        "session_start_reminder.py",
        "token_summary_appender.py",
    ]:
        assert not (_HOOKS_DIR / old).exists(), f"Old hook file still present: {old}"


def test_hook_registry_uses_new_filenames() -> None:
    """HOOK_REGISTRY must reference only the new filenames, not the old ones."""
    all_scripts = {s for h in HOOK_REGISTRY for s in h.scripts}
    old_names = {
        "quota_check.py",
        "quota_post_check.py",
        "skill_cmd_check.py",
        "pretty_output.py",
        "session_start_reminder.py",
        "token_summary_appender.py",
    }
    for old in old_names:
        assert old not in all_scripts, f"HOOK_REGISTRY still references old name: {old}"


def test_find_broken_hook_scripts_ignores_user_inline_hooks(tmp_path: Path) -> None:
    """User inline hooks like 'wsl-notify-send.exe Done' must not be flagged as broken."""
    settings = tmp_path / "settings.json"
    settings.write_text(
        json.dumps(
            {
                "hooks": {
                    "PostToolUse": [{"hooks": [{"command": 'wsl-notify-send.exe "Build done"'}]}]
                }
            }
        )
    )
    broken = find_broken_hook_scripts(settings)
    assert broken == []


def test_find_broken_hook_scripts_flags_missing_autoskillit_script(tmp_path: Path) -> None:
    """A python3 /abs/path/autoskillit/hooks/script.py that doesn't exist must be flagged."""
    missing = str(HOOKS_DIR / "nonexistent_guard.py")
    settings = tmp_path / "settings.json"
    settings.write_text(
        json.dumps(
            {
                "hooks": {
                    "PreToolUse": [{"matcher": ".*", "hooks": [{"command": f"python3 {missing}"}]}]
                }
            }
        )
    )
    broken = find_broken_hook_scripts(settings)
    assert any("nonexistent_guard.py" in b for b in broken)


# T-WT-5: find_broken_hook_scripts detects deleted worktree directory (regression gate)
def test_find_broken_hook_scripts_detects_deleted_worktree_dir(tmp_path: Path) -> None:
    """find_broken_hook_scripts must flag hooks where the entire directory tree is gone."""
    deleted_worktree = "/tmp/deleted_worktree_12345/src/autoskillit/hooks"
    settings = {
        "hooks": {
            "PreToolUse": [
                {
                    "matcher": ".*",
                    "hooks": [
                        {
                            "type": "command",
                            "command": f"python3 {deleted_worktree}/quota_guard.py",
                        },
                        {
                            "type": "command",
                            "command": f"python3 {deleted_worktree}/branch_protection_guard.py",
                        },
                    ],
                }
            ]
        }
    }
    settings_path = tmp_path / "settings.json"
    settings_path.write_text(json.dumps(settings))

    broken = find_broken_hook_scripts(settings_path)
    assert len(broken) == 2
    assert all(deleted_worktree in b for b in broken)


def test_find_broken_hook_scripts_does_not_flag_user_python_scripts(tmp_path: Path) -> None:
    """A user's python3 script outside the autoskillit hooks dir must not be flagged."""
    user_script = "/home/user/my_custom_guard.py"
    settings = tmp_path / "settings.json"
    settings.write_text(
        json.dumps(
            {
                "hooks": {
                    "PreToolUse": [
                        {"matcher": ".*", "hooks": [{"command": f"python3 {user_script}"}]}
                    ]
                }
            }
        )
    )
    broken = find_broken_hook_scripts(settings)
    assert broken == []


# Hooks excluded from the deny-trigger requirement: purely advisory (non-blocking)
# or auto-correcting (not a hard deny).
_ADVISORY_HOOKS: frozenset[str] = frozenset(
    {
        "guards/recipe_write_advisor.py",
        "guards/grep_pattern_lint_guard.py",
        "guards/mcp_health_guard.py",
    }
)


def test_deny_path_pretooluse_hooks_export_deny_trigger() -> None:
    """Every non-advisory PreToolUse hook with a deny path must export a *_DENY_TRIGGER constant.

    This structural test ensures that prompt builders can reference deny triggers
    programmatically rather than hardcoding strings. New hooks added to HOOK_REGISTRY
    must include a DENY_TRIGGER constant or be added to _ADVISORY_HOOKS above.
    """
    import importlib

    deny_path_scripts: set[str] = set()
    for hook_def in HOOK_REGISTRY:
        if hook_def.event_type != "PreToolUse":
            continue
        for script in hook_def.scripts:
            if script not in _ADVISORY_HOOKS:
                deny_path_scripts.add(script)

    missing: list[str] = []
    for script in sorted(deny_path_scripts):
        module_name = script.removesuffix(".py").replace("/", ".")
        module = importlib.import_module(f"autoskillit.hooks.{module_name}")
        has_trigger = any(name.endswith("_DENY_TRIGGER") for name in dir(module))
        if not has_trigger:
            missing.append(script)

    assert not missing, (
        f"These PreToolUse hooks have no *_DENY_TRIGGER constant: {missing}. "
        "Add a DENY_TRIGGER constant or add the hook to _ADVISORY_HOOKS if it is non-blocking."
    )
