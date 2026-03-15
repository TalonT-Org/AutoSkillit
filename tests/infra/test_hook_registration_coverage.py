"""Structural test: every hook script is registered in HOOK_REGISTRY."""

from pathlib import Path

from autoskillit.hook_registry import HOOK_REGISTRY

HOOKS_DIR = Path(__file__).resolve().parents[2] / "src" / "autoskillit" / "hooks"
EXEMPT = {"__init__.py", "pretty_output.py"}  # PostToolUse, handled separately


def test_all_pretooluse_hook_scripts_are_registered() -> None:
    """Every .py file in hooks/ (except exemptions) must appear in at
    least one HOOK_REGISTRY entry's scripts list."""
    hook_files = {f.name for f in HOOKS_DIR.glob("*.py") if f.name not in EXEMPT}
    registered_scripts: set[str] = set()
    for hook_def in HOOK_REGISTRY:
        if hook_def.event_type == "PreToolUse":
            registered_scripts.update(hook_def.scripts)

    unregistered = hook_files - registered_scripts
    assert not unregistered, (
        f"Hook scripts exist but are not in HOOK_REGISTRY: {sorted(unregistered)}"
    )


def test_posttooluse_hooks_are_registered() -> None:
    """PostToolUse hooks must also be registered in HOOK_REGISTRY."""
    registered_post: set[str] = set()
    for hook_def in HOOK_REGISTRY:
        if hook_def.event_type == "PostToolUse":
            registered_post.update(hook_def.scripts)
    assert "pretty_output.py" in registered_post
