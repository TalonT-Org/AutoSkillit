"""Structural enforcement tests: session-scope metadata on HookDef.

These tests ensure that:
1. HookDef declares a session_scope field (so authors must think about scope)
2. Any hook declared with session_scope != "any" has AUTOSKILLIT_HEADLESS in its source
3. Any scoped hook has test coverage for both headless and non-headless cases

These form a closed loop that makes the "guard designed for headless but applied
globally" bug class structurally impossible without explicit, tested, declared intent.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from autoskillit.hook_registry import HOOK_REGISTRY, HOOKS_DIR, HookDef


def test_hookdef_has_session_scope() -> None:
    """HookDef must have a session_scope field."""
    fields = {f.name for f in dataclasses.fields(HookDef)}
    assert "session_scope" in fields, (
        "HookDef is missing the 'session_scope' field. "
        "Add: session_scope: Literal['any', 'headless_only', 'interactive_only'] = 'any'"
    )


def test_session_scope_default_is_any() -> None:
    """The default session_scope must be 'any' so existing entries remain unchanged."""
    default_def = HookDef(matcher="test.*", scripts=["test.py"])
    assert default_def.session_scope == "any"  # type: ignore[attr-defined]


def _scoped_hooks() -> list[tuple[HookDef, str]]:
    """Return (hookdef, script_path) pairs for all non-'any' scoped hooks."""
    pairs = []
    for hookdef in HOOK_REGISTRY:
        scope = getattr(hookdef, "session_scope", "any")
        if scope != "any":
            for script in hookdef.scripts:
                pairs.append((hookdef, script))
    return pairs


@pytest.mark.parametrize("hookdef,script", _scoped_hooks())
def test_scoped_guard_contains_headless_check(hookdef: HookDef, script: str) -> None:
    """Every guard declared with session_scope != 'any' must check AUTOSKILLIT_HEADLESS."""
    script_path = HOOKS_DIR / script
    assert script_path.exists(), f"Hook script not found: {script_path}"
    source = script_path.read_text(encoding="utf-8")
    assert "AUTOSKILLIT_HEADLESS" in source, (
        f"{script} is declared with session_scope={hookdef.session_scope!r} "  # type: ignore[attr-defined]
        f"but does not contain 'AUTOSKILLIT_HEADLESS'. "
        f"Add the env-var check or change session_scope to 'any'."
    )


def _scoped_guard_names() -> list[str]:
    """Return the base name of each unique guard script declared with non-'any' scope."""
    seen: set[str] = set()
    result = []
    for hookdef in HOOK_REGISTRY:
        scope = getattr(hookdef, "session_scope", "any")
        if scope != "any":
            for script in hookdef.scripts:
                if script not in seen:
                    seen.add(script)
                    result.append(script)
    return result


def _find_test_file(guard_script: str) -> Path | None:
    """Locate the test file for a guard script in tests/infra/ or tests/hooks/."""
    stem = Path(guard_script).stem  # e.g. "ask_user_question_guard"
    tests_infra = Path(__file__).resolve().parent
    for directory in (tests_infra, tests_infra.parent / "hooks"):
        candidate = directory / f"test_{stem}.py"
        if candidate.exists():
            return candidate
    return None


@pytest.mark.parametrize("guard_script", _scoped_guard_names())
def test_scoped_guard_has_both_session_type_test_cases(guard_script: str) -> None:
    """Every scoped guard's test file must exercise both headless and non-headless paths."""
    test_file = _find_test_file(guard_script)
    assert test_file is not None, (
        f"No test file found for scoped guard '{guard_script}'. "
        f"Expected: tests/infra/test_{Path(guard_script).stem}.py"
    )
    source = test_file.read_text(encoding="utf-8")
    code_lines = "\n".join(
        line for line in source.splitlines() if not line.lstrip().startswith("#")
    )
    has_headless_true = "headless=True" in code_lines or "AUTOSKILLIT_HEADLESS" in code_lines
    # Accept explicit headless=False OR the env-strip pattern used by subprocess-style hook tests
    # (k != "AUTOSKILLIT_HEADLESS" strips the var to exercise the non-headless code path)
    has_headless_false = (
        "headless=False" in code_lines or '!= "AUTOSKILLIT_HEADLESS"' in code_lines
    )
    assert has_headless_true, (
        f"{test_file.name} must test the headless=True path for scoped guard '{guard_script}'."
    )
    assert has_headless_false, (
        f"{test_file.name} must test the headless=False path for scoped guard '{guard_script}'."
    )
