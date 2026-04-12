"""Tests for hook_registry.py — L0 hook identity model."""

from __future__ import annotations

from pathlib import Path

from autoskillit.hook_registry import (
    HOOK_REGISTRY,
    _extract_script_basenames,
    _is_own_hook,
    canonical_script_basenames,
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

_HOOKS_DIR = Path(__file__).parent.parent / "src" / "autoskillit" / "hooks"


def test_renamed_hook_files_exist() -> None:
    """New hook filenames must exist on disk after rename."""
    for expected in [
        "quota_guard.py",
        "quota_post_hook.py",
        "skill_cmd_guard.py",
        "pretty_output_hook.py",
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
