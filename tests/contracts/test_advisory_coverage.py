"""Contracts: SKILL_FILE_ADVISORY_MAP advisory hook coverage.

Guards:
- recipe_write_advisor.py is registered in HOOK_REGISTRY with Write|Edit matcher.
- Advisory hook emits message payload, never permissionDecision.
- All skill names in SKILL_FILE_ADVISORY_MAP resolve to real skills.
- recipe_write_advisor._ADVISORY_PATTERNS stays in sync with SKILL_FILE_ADVISORY_MAP.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

from autoskillit.core._type_constants import SKILL_FILE_ADVISORY_MAP
from autoskillit.core.paths import pkg_root
from autoskillit.hook_registry import HOOK_REGISTRY
from autoskillit.workspace.skills import DefaultSkillResolver


def _run_advisor(payload: dict, extra_env: dict[str, str] | None = None) -> tuple[int, str]:
    hook_path = pkg_root() / "hooks" / "recipe_write_advisor.py"
    env = {**os.environ, **(extra_env or {})}
    env.pop("AUTOSKILLIT_HEADLESS", None)
    result = subprocess.run(
        [sys.executable, str(hook_path)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=env,
    )
    return result.returncode, result.stdout


def test_every_advisory_pattern_has_a_hook() -> None:
    """recipe_write_advisor.py is registered in HOOK_REGISTRY under Write|Edit."""
    advisory_scripts = {
        script
        for hook in HOOK_REGISTRY
        if "Write" in hook.matcher and hook.session_scope == "interactive_only"
        for script in hook.scripts
    }
    assert "recipe_write_advisor.py" in advisory_scripts, (
        "recipe_write_advisor.py is not registered in HOOK_REGISTRY with "
        "a Write|Edit matcher and session_scope=interactive_only"
    )


def test_advisory_hooks_are_non_blocking() -> None:
    """Advisory hook emits message payload, never permissionDecision."""
    payload = {
        "tool_name": "Write",
        "tool_input": {"file_path": ".autoskillit/recipes/test.yaml"},
    }
    rc, stdout = _run_advisor(payload)
    assert rc == 0
    assert stdout.strip(), "Expected advisory output for a recipe YAML path"
    data = json.loads(stdout.strip())
    hook_out = data["hookSpecificOutput"]
    assert "message" in hook_out, f"Expected 'message' key in hookSpecificOutput, got {hook_out!r}"
    assert "permissionDecision" not in hook_out, "Advisory hook must not emit permissionDecision"


def test_advisory_map_skill_names_resolve() -> None:
    """Every skill name in SKILL_FILE_ADVISORY_MAP must resolve to a real skill."""
    resolver = DefaultSkillResolver()
    unresolvable = [
        skill for skill in SKILL_FILE_ADVISORY_MAP.values() if resolver.resolve(skill) is None
    ]
    assert not unresolvable, (
        f"SKILL_FILE_ADVISORY_MAP references unresolvable skill names: {unresolvable}"
    )


def test_hook_patterns_match_type_constants() -> None:
    """recipe_write_advisor._ADVISORY_PATTERNS must exactly match SKILL_FILE_ADVISORY_MAP."""
    from autoskillit.hooks.recipe_write_advisor import _ADVISORY_PATTERNS

    assert list(SKILL_FILE_ADVISORY_MAP.items()) == _ADVISORY_PATTERNS, (
        "recipe_write_advisor._ADVISORY_PATTERNS is out of sync with "
        "SKILL_FILE_ADVISORY_MAP in _type_constants.py. "
        f"Hook patterns: {_ADVISORY_PATTERNS!r}, "
        f"Constants map: {list(SKILL_FILE_ADVISORY_MAP.items())!r}"
    )
