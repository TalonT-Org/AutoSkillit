"""Structural enforcement tests: skill-exemption metadata on HookDef.

These tests ensure that:
1. HookDef declares an exempt_skills field (so authors must think about exemptions)
2. Any hook declared with exempt_skills non-empty has AUTOSKILLIT_SKILL_NAME in its source
3. Any exempt hook has test coverage for the exempt path

These form a closed loop that makes the "guard blocks legitimate pipeline skill" bug
class structurally impossible without explicit, tested, declared exemptions.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from autoskillit.hook_registry import HOOK_REGISTRY, HOOKS_DIR, HookDef


def test_hookdef_has_exempt_skills() -> None:
    """HookDef must have an exempt_skills field of type frozenset[str]."""
    fields = {f.name for f in dataclasses.fields(HookDef)}
    assert "exempt_skills" in fields, (
        "HookDef is missing the 'exempt_skills' field. "
        "Add: exempt_skills: frozenset[str] = field(default_factory=frozenset)"
    )
    # Verify it's a frozenset[str]
    field_type = next(f.type for f in dataclasses.fields(HookDef) if f.name == "exempt_skills")
    assert "frozenset" in str(field_type).lower(), (
        f"exempt_skills field type is {field_type!r}, expected frozenset[str]"
    )


def test_exempt_skills_default_is_empty() -> None:
    """The default exempt_skills must be empty (frozenset()) so existing entries unchanged."""
    default_def = HookDef(matcher="test.*", scripts=["test.py"])
    assert default_def.exempt_skills == frozenset()  # type: ignore[attr-defined]


def _exempt_hooks() -> list[tuple[HookDef, str]]:
    """Return (hookdef, script_path) pairs for all hooks with non-empty exempt_skills."""
    pairs = []
    for hookdef in HOOK_REGISTRY:
        exempt = getattr(hookdef, "exempt_skills", frozenset())
        if exempt:
            for script in hookdef.scripts:
                pairs.append((hookdef, script))
    return pairs


@pytest.mark.parametrize("hookdef,script", _exempt_hooks())
def test_exempt_guard_contains_skill_name_check(hookdef: HookDef, script: str) -> None:
    """Every guard declared with exempt_skills must check AUTOSKILLIT_SKILL_NAME."""
    script_path = HOOKS_DIR / script
    assert script_path.exists(), f"Hook script not found: {script_path}"
    source = script_path.read_text(encoding="utf-8")
    assert "AUTOSKILLIT_SKILL_NAME" in source, (
        f"{script} is declared with exempt_skills={hookdef.exempt_skills!r} "  # type: ignore[attr-defined]
        f"but does not contain 'AUTOSKILLIT_SKILL_NAME'. "
        f"Add the env-var check to allow exempt skills."
    )


def _exempt_guard_names() -> list[str]:
    """Return the base name of each unique guard script declared with non-empty exempt_skills."""
    seen: set[str] = set()
    result = []
    for hookdef in HOOK_REGISTRY:
        exempt = getattr(hookdef, "exempt_skills", frozenset())
        if exempt:
            for script in hookdef.scripts:
                if script not in seen:
                    seen.add(script)
                    result.append(script)
    return result


def _find_test_file(guard_script: str) -> Path | None:
    """Locate the test file for a guard script in tests/infra/ or tests/hooks/."""
    stem = Path(guard_script).stem  # e.g. "pr_create_guard"
    tests_infra = Path(__file__).resolve().parent
    for directory in (tests_infra, tests_infra.parent / "hooks"):
        candidate = directory / f"test_{stem}.py"
        if candidate.exists():
            return candidate
    return None


@pytest.mark.parametrize("guard_script", _exempt_guard_names())
def test_exempt_guard_has_exempt_path_test_cases(guard_script: str) -> None:
    """Every exempt guard's test file must exercise the exempt skill path."""
    test_file = _find_test_file(guard_script)
    assert test_file is not None, (
        f"No test file found for exempt guard '{guard_script}'. "
        f"Expected: tests/infra/test_{Path(guard_script).stem}.py"
    )
    source = test_file.read_text(encoding="utf-8")
    code_lines = "\n".join(
        line for line in source.splitlines() if not line.lstrip().startswith("#")
    )
    has_skill_name = "skill_name=" in code_lines or "AUTOSKILLIT_SKILL_NAME" in code_lines
    assert has_skill_name, (
        f"{test_file.name} must test the exempt skill path for guard '{guard_script}'. "
        f"Add 'skill_name=' to _run_guard/_run_bash_guard calls."
    )
    has_exempt_test = "exempt" in code_lines.lower() or "allowed_skill" in code_lines.lower()
    assert has_exempt_test, (
        f"{test_file.name} must have at least one test method covering the exempt path "
        f"(method name should contain 'exempt' or 'allowed_skill')."
    )
