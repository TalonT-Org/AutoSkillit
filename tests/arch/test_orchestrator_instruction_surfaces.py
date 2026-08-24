"""Every orchestrator-facing instruction surface is registered (#4707).

#4707's root cause was a defense (the prose-forwarding sweep) aimed at the
wrong directory: the offending text lived in ``tools_recipe.py``, but the
sweep only scanned ``skills/*/SKILL.md``. This module guards the registry
that closes that scope gap — ``ORCHESTRATOR_FACING_INSTRUCTION_SURFACES``
(core/types/_type_orchestrator_instruction_surfaces.py) — against silently drifting the
same way: a stale entry, an entry that resolves but extracts nothing, an
unregistered bootstrap skill, or an unregistered kitchen-tagged tool module
would all reproduce the #4707 failure mode inside its own fix.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from autoskillit.core import ORCHESTRATOR_FACING_INSTRUCTION_SURFACES
from tests._helpers import extract_orchestrator_surface_texts, resolve_orchestrator_surface_paths

pytestmark = [pytest.mark.small]

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SRC_ROOT = _REPO_ROOT / "src" / "autoskillit"
_SKILLS_ROOT = _SRC_ROOT / "skills"
_TOOLS_ROOT = _SRC_ROOT / "server" / "tools"

# Kitchen-tagged tool modules with no registered surface AND no coverage from
# another registered glob. Empty today — every kitchen-tagged tool module
# either resolves under ORCHESTRATOR_FACING_INSTRUCTION_SURFACES directly or
# is covered incidentally; a genuine future exemption (e.g. a module with no
# meaningful docstring content) belongs here with a citation, not a silent
# drop from the enumeration below.
_KITCHEN_MODULE_EXEMPTIONS: dict[str, str] = {}


def _kitchen_tagged_tool_modules() -> dict[str, Path]:
    """AST-scan server/tools/ for every module exporting an @mcp.tool handler
    whose tags set contains a tag equal to "kitchen" or starting with "kitchen-".

    Matches on the ``kitchen`` prefix, not the bare literal — verified: the
    exact surface #4707 is about (``load_recipe``) carries no bare "kitchen"
    tag, only ``kitchen-core``/``fleet-dispatch``.
    """
    modules: dict[str, Path] = {}
    for py_path in sorted(_TOOLS_ROOT.rglob("*.py")):
        if py_path.name == "__init__.py":
            continue
        tree = ast.parse(py_path.read_text(encoding="utf-8"), filename=str(py_path))
        is_kitchen_tagged = False
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for dec in node.decorator_list:
                if not isinstance(dec, ast.Call):
                    continue
                func = dec.func
                if not (isinstance(func, ast.Attribute) and func.attr == "tool"):
                    continue
                for kw in dec.keywords:
                    if kw.arg != "tags" or not isinstance(kw.value, ast.Set):
                        continue
                    tag_values = {
                        elt.value
                        for elt in kw.value.elts
                        if isinstance(elt, ast.Constant) and isinstance(elt.value, str)
                    }
                    if any(t == "kitchen" or t.startswith("kitchen-") for t in tag_values):
                        is_kitchen_tagged = True
        if is_kitchen_tagged:
            rel = py_path.relative_to(_SRC_ROOT)
            dotted = ".".join(rel.with_suffix("").parts)
            modules[dotted] = py_path
    return modules


def test_every_registered_surface_resolves_to_at_least_one_existing_file() -> None:
    """No stale entry silently scanning nothing — the #4707 failure mode."""
    empty: list[str] = []
    for name, surface in ORCHESTRATOR_FACING_INSTRUCTION_SURFACES.items():
        if surface.path_glob is None:
            continue  # GENERATED_OUTPUT — checked by the non-empty-text test below
        paths = resolve_orchestrator_surface_paths(surface, _SRC_ROOT)
        if not paths:
            empty.append(f"{name} ({surface.path_glob!r})")
    assert not empty, f"registered surface(s) resolve to zero files: {empty}"


def test_every_registered_surface_extracts_non_empty_text() -> None:
    """Path existence is not enough — a surface whose extraction mode returns
    "" is registered, resolves, and scans nothing, which reads as coverage."""
    blank: list[str] = []
    for name, surface in ORCHESTRATOR_FACING_INSTRUCTION_SURFACES.items():
        texts = extract_orchestrator_surface_texts(surface, _SRC_ROOT)
        assert texts, f"{name}: extraction produced no entries at all"
        for identifier, text in texts.items():
            if not text.strip():
                blank.append(f"{name} :: {identifier}")
    assert not blank, f"registered surface source(s) extract empty text: {blank}"


def test_every_open_kitchen_injected_skill_is_registered() -> None:
    """The skill(s) open_kitchen injects into every orchestrator session must
    fall under a registered MARKDOWN_FULL glob — this is the exact surface
    issue #4707 is about."""
    injected_skills = [
        skill_md
        for skill_md in sorted(_SKILLS_ROOT.glob("*/SKILL.md"))
        if "execution_role: orchestrator" in skill_md.read_text(encoding="utf-8")
    ]
    assert injected_skills, (
        "no skill frontmatter declares execution_role: orchestrator — "
        "has the bootstrap-skill marker moved?"
    )

    covered_paths: set[Path] = set()
    for surface in ORCHESTRATOR_FACING_INSTRUCTION_SURFACES.values():
        if surface.path_glob is None:
            continue
        covered_paths.update(resolve_orchestrator_surface_paths(surface, _SRC_ROOT))

    uncovered = [str(p.relative_to(_REPO_ROOT)) for p in injected_skills if p not in covered_paths]
    assert not uncovered, (
        f"open_kitchen-injected skill(s) not covered by any registered surface: {uncovered}"
    )


def test_every_kitchen_tagged_tool_module_is_registered_or_exempt() -> None:
    kitchen_modules = _kitchen_tagged_tool_modules()
    assert kitchen_modules, (
        "no kitchen-tagged tool modules found — has tagging convention changed?"
    )

    covered_paths: set[Path] = set()
    for surface in ORCHESTRATOR_FACING_INSTRUCTION_SURFACES.values():
        if surface.path_glob is None:
            continue
        covered_paths.update(resolve_orchestrator_surface_paths(surface, _SRC_ROOT))

    unaccounted = sorted(
        dotted
        for dotted, path in kitchen_modules.items()
        if path not in covered_paths and dotted not in _KITCHEN_MODULE_EXEMPTIONS
    )
    assert not unaccounted, (
        "kitchen-tagged tool module(s) neither registered under "
        "ORCHESTRATOR_FACING_INSTRUCTION_SURFACES nor exempted with a reason "
        f"in _KITCHEN_MODULE_EXEMPTIONS: {unaccounted}"
    )
