"""Structural test: every hook script is registered in HOOK_REGISTRY."""

from pathlib import Path

from autoskillit.hook_registry import HOOK_REGISTRY

HOOKS_DIR = Path(__file__).resolve().parents[2] / "src" / "autoskillit" / "hooks"


def test_all_pretooluse_hook_scripts_are_registered() -> None:
    """Every hook .py (excl. PostToolUse, SessionStart, __init__) is PreToolUse-registered."""
    post_or_session_registered = {
        script
        for hd in HOOK_REGISTRY
        if hd.event_type in ("PostToolUse", "SessionStart")
        for script in hd.scripts
    }
    hook_files = {
        f.name
        for f in HOOKS_DIR.glob("*.py")
        if f.name != "__init__.py" and f.name not in post_or_session_registered
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
    all_scripts = {p.name for p in HOOKS_DIR.glob("*.py") if p.name != "__init__.py"}
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
