"""Keep each hook's registry scope identical to its shared prologue."""

from __future__ import annotations

import pytest

from autoskillit.hook_registry import HOOK_REGISTRY
from tests.fixtures.hook_script_inventory import _hook_scripts, _scope_prologue

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]


def test_script_scope_prologue_matches_registry() -> None:
    """Registry scope and first-statement hook prologue have one source of truth."""
    declared_by_script: dict[str, set[tuple[str, frozenset[str]]]] = {}
    for hookdef in HOOK_REGISTRY:
        declaration = (hookdef.session_scope, hookdef.exempt_session_types)
        for script in hookdef.scripts:
            declared_by_script.setdefault(script, set()).add(declaration)

    for script, path in _hook_scripts():
        prologue = _scope_prologue(path)
        declarations = declared_by_script.get(script, set())
        if prologue is not None:
            assert declarations, f"{script} declares a scope prologue but has no HookDef"
        if declarations:
            requires_prologue = any(
                scope != "any" or exempt_tiers for scope, exempt_tiers in declarations
            )
            assert not requires_prologue or prologue is not None, (
                f"{script} has a non-default HookDef scope but main() does not begin with "
                "enforce_session_scope(...) or enforce_script_session_scope(__file__)"
            )
            assert len(declarations) == 1, (
                f"{script} is listed by HookDefs with incompatible scope declarations: "
                f"{sorted(map(repr, declarations))}"
            )
            acceptable = (
                prologue is None
                or prologue == next(iter(declarations))
                or prologue[0] == "<identity>"
            )
            assert acceptable, (
                f"{script} decl {next(iter(declarations))!r} != prologue {prologue!r}"
            )

    known_scripts = {script for script, _path in _hook_scripts()}
    unknown = set(declared_by_script) - known_scripts
    assert not unknown, f"HookDef scripts must be public hook scripts: {sorted(unknown)}"
