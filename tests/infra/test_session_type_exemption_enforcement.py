"""Structural enforcement tests: session-type exemption metadata on HookDef.

These tests ensure that:
1. HookDef declares an exempt_session_types field
2. Any hook declared with exempt_session_types non-empty has AUTOSKILLIT_SESSION_TYPE in its source
3. Any exempt hook has test coverage for the exempt session-type path
4. _EXEMPT_SESSION_TYPES in guard scripts matches HookDef.exempt_session_types
5. exempt_session_types is included in the canonical registry payload (hash input)

These form a closed loop that makes the "guard blocks legitimate orchestrator session"
bug class structurally impossible without explicit, tested, declared exemptions.
"""

from __future__ import annotations

import dataclasses
import importlib.util
import json
from pathlib import Path

import pytest

from autoskillit.hook_registry import (
    HOOK_REGISTRY,
    HOOKS_DIR,
    RETIRED_SCRIPT_BASENAMES,
    HookDef,
    _canonical_registry_payload,
)

pytestmark = [pytest.mark.layer("infra"), pytest.mark.small]


def test_hookdef_has_exempt_session_types() -> None:
    """HookDef must have an exempt_session_types field of type frozenset[str]."""
    fields = {f.name for f in dataclasses.fields(HookDef)}
    assert "exempt_session_types" in fields, (
        "HookDef is missing the 'exempt_session_types' field. "
        "Add: exempt_session_types: frozenset[str] = field(default_factory=frozenset)"
    )
    field_type = next(
        f.type for f in dataclasses.fields(HookDef) if f.name == "exempt_session_types"
    )
    assert "frozenset" in str(field_type).lower(), (
        f"exempt_session_types field type is {field_type!r}, expected frozenset[str]"
    )


def test_exempt_session_types_default_is_empty() -> None:
    """The default exempt_session_types must be empty so existing entries are unchanged."""
    default_def = HookDef(matcher="test.*", scripts=["test.py"])
    assert default_def.exempt_session_types == frozenset()


def _exempt_session_type_hooks() -> list[tuple[HookDef, str]]:
    """Return (hookdef, script_path) pairs for all hooks with non-empty exempt_session_types."""
    pairs = []
    for hookdef in HOOK_REGISTRY:
        exempt = getattr(hookdef, "exempt_session_types", frozenset())
        if exempt:
            for script in hookdef.scripts:
                pairs.append((hookdef, script))
    return pairs


def test_at_least_one_hookdef_has_exempt_session_types() -> None:
    """Vacuousness guard: parametrized tests must have at least one test case."""
    assert len(_exempt_session_type_hooks()) >= 1, (
        "HOOK_REGISTRY must have at least one HookDef with non-empty exempt_session_types. "
        "test_exempt_session_type_guard_contains_session_type_check would collect zero test cases."
    )


@pytest.mark.parametrize("hookdef,script", _exempt_session_type_hooks())
def test_exempt_session_type_guard_contains_session_type_check(
    hookdef: HookDef, script: str
) -> None:
    """Every guard declared with exempt_session_types must check AUTOSKILLIT_SESSION_TYPE."""
    script_path = HOOKS_DIR / script
    assert script_path.exists(), f"Hook script not found: {script_path}"
    source = script_path.read_text(encoding="utf-8")
    assert "AUTOSKILLIT_SESSION_TYPE" in source, (
        f"{script} is declared with exempt_session_types="
        f"{hookdef.exempt_session_types!r} "  # type: ignore[attr-defined]
        f"but does not contain 'AUTOSKILLIT_SESSION_TYPE'. "
        f"Add the env-var check to allow exempt session types."
    )


def _exempt_session_type_guard_names() -> list[str]:
    """Return unique guard script paths declared with non-empty exempt_session_types."""
    seen: set[str] = set()
    result = []
    for hookdef in HOOK_REGISTRY:
        exempt = getattr(hookdef, "exempt_session_types", frozenset())
        if exempt:
            for script in hookdef.scripts:
                if script not in seen:
                    seen.add(script)
                    result.append(script)
    return result


def test_at_least_one_exempt_session_type_guard_name_exists() -> None:
    """Vacuousness guard: parametrized test must have at least one test case."""
    assert len(_exempt_session_type_guard_names()) >= 1, (
        "HOOK_REGISTRY must have at least one guard script with non-empty exempt_session_types. "
        "test_exempt_session_type_guard_has_test_cases would collect zero test cases."
    )


def _find_test_file(guard_script: str) -> Path | None:
    """Locate the test file for a guard script in tests/infra/ or tests/hooks/."""
    stem = Path(guard_script).stem
    tests_infra = Path(__file__).resolve().parent
    for directory in (tests_infra, tests_infra.parent / "hooks"):
        candidate = directory / f"test_{stem}.py"
        if candidate.exists():
            return candidate
    return None


@pytest.mark.parametrize("guard_script", _exempt_session_type_guard_names())
def test_exempt_session_type_guard_has_test_cases(guard_script: str) -> None:
    """Every exempt-session-type guard's test file must exercise the exempt path."""
    test_file = _find_test_file(guard_script)
    assert test_file is not None, (
        f"No test file found for exempt-session-type guard '{guard_script}'. "
        f"Expected: tests/infra/test_{Path(guard_script).stem}.py"
    )
    source = test_file.read_text(encoding="utf-8")
    code_lines = "\n".join(
        line for line in source.splitlines() if not line.lstrip().startswith("#")
    )
    has_session_type = "session_type=" in code_lines or "AUTOSKILLIT_SESSION_TYPE" in code_lines
    assert has_session_type, (
        f"{test_file.name} must test the exempt session-type path for guard '{guard_script}'. "
        f"Add 'session_type=' to _run_guard/_run_bash_guard calls."
    )
    has_orchestrator = "orchestrator" in code_lines or "exempt" in code_lines.lower()
    assert has_orchestrator, (
        f"{test_file.name} must have at least one test case covering an exempt session type "
        f"(e.g., session_type='orchestrator' or method name containing 'exempt')."
    )


def test_pr_create_guard_exempt_session_types_matches_hookdef() -> None:
    """_EXEMPT_SESSION_TYPES in pr_create_guard.py must equal exempt_session_types on HookDef.

    Catches drift between the two parallel frozensets that the stdlib-only boundary
    prevents from sharing a common import.
    """
    script_path = HOOKS_DIR / "guards/pr_create_guard.py"
    spec = importlib.util.spec_from_file_location("pr_create_guard", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    script_exempt: frozenset[str] | None = getattr(module, "_EXEMPT_SESSION_TYPES", None)
    assert script_exempt is not None, "_EXEMPT_SESSION_TYPES not found in pr_create_guard.py"

    hookdef_exempt: frozenset[str] | None = None
    for hookdef in HOOK_REGISTRY:
        if "guards/pr_create_guard.py" in hookdef.scripts:
            hookdef_exempt = getattr(hookdef, "exempt_session_types", None)
            break
    assert hookdef_exempt is not None, "No HookDef found for guards/pr_create_guard.py"

    assert script_exempt == hookdef_exempt, (
        f"_EXEMPT_SESSION_TYPES in pr_create_guard.py {script_exempt!r} does not match "
        f"exempt_session_types on the HookDef {hookdef_exempt!r}. "
        "Update both frozensets together."
    )


def test_canonical_registry_payload_includes_exempt_session_types() -> None:
    """exempt_session_types must be part of the canonical registry payload used for hashing.

    Any change to exempt_session_types on any HookDef must trigger hook drift detection
    and hooks.json regeneration.
    """
    payload_str = _canonical_registry_payload(HOOK_REGISTRY, RETIRED_SCRIPT_BASENAMES)
    payload = json.loads(payload_str)
    for row in payload["registry"]:
        assert "exempt_session_types" in row, (
            f"Registry row for matcher={row.get('matcher')!r} is missing"
            " 'exempt_session_types'. Add it to _canonical_registry_payload."
        )
