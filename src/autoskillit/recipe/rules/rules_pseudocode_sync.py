"""Semantic rule: pseudocode-callable-divergence

Fires when a run_python callable in autoskillit.smoke_utils is paired (via
phoropter_family) with a run_skill step whose SKILL.md python pseudocode inlines
frozenset constant members without referencing the constant by name. Divergence
risk: if the constant is updated the pseudocode silently becomes stale.
"""

from __future__ import annotations

import ast
import importlib.util
from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING

from autoskillit.core import Severity, get_logger
from autoskillit.recipe._skill_helpers import _resolve_skill_md
from autoskillit.recipe._skill_placeholder_parser import extract_python_blocks
from autoskillit.recipe.contracts import resolve_skill_name
from autoskillit.recipe.registry import RuleFinding, make_finding, semantic_rule

if TYPE_CHECKING:
    from autoskillit.recipe._analysis import ValidationContext

logger = get_logger(__name__)


def _get_package_source_dir(callable_path: str) -> Path | None:
    parts = callable_path.split(".")
    for n in range(len(parts), 1, -1):
        pkg = ".".join(parts[:n])
        try:
            spec = importlib.util.find_spec(pkg)
        except (ModuleNotFoundError, ValueError):
            continue
        if spec is None or not spec.origin:
            continue
        origin = Path(spec.origin)
        return origin.parent
    return None


def _decode_frozenset_constant_statement(
    node: ast.AST,
) -> dict[str, frozenset[str | None]]:
    """Decode directly assigned frozenset literals without descending into children."""
    targets: list[ast.Name] = []
    value: ast.expr | None = None
    if isinstance(node, ast.Assign):
        targets = [target for target in node.targets if isinstance(target, ast.Name)]
        value = node.value
    elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        targets = [node.target]
        value = node.value
    if not targets or value is None:
        return {}
    if not (
        isinstance(value, ast.Call)
        and isinstance(value.func, ast.Name)
        and value.func.id == "frozenset"
        and value.args
    ):
        return {}
    members_node = value.args[0]
    if not isinstance(members_node, (ast.Set, ast.List)):
        return {}
    members: set[str | None] = set()
    for member in members_node.elts:
        if not isinstance(member, ast.Constant) or not isinstance(member.value, (str, type(None))):
            return {}
        members.add(member.value)
    return {target.id: frozenset(members) for target in targets}


def _find_frozenset_constants(source_dir: Path) -> dict[str, frozenset[str | None]]:
    constants: dict[str, frozenset[str | None]] = {}
    for py_file in sorted(source_dir.rglob("*.py")):
        try:
            tree = ast.parse(py_file.read_text(encoding="utf-8"))
        except Exception:
            logger.warning("Failed to parse %s", py_file, exc_info=True)
            continue
        for node in ast.iter_child_nodes(tree):
            constants.update(_decode_frozenset_constant_statement(node))
    return constants


def _member_inlined_in_block(member: str | None, block: str) -> bool:
    if member is None:
        return "None" in block
    return f'"{member}"' in block or f"'{member}'" in block


def _iter_family_skill_python_blocks(
    ctx: ValidationContext,
    family: str,
) -> Iterator[tuple[str, list[str]]]:
    """Yield resolved Python blocks for matching family skills in recipe order."""
    for _, step in ctx.recipe.steps.items():
        if step.tool != "run_skill" or step.phoropter_family != family:
            continue
        skill_cmd = step.with_args.get("skill_command", "")
        if not skill_cmd:
            continue
        skill_name = resolve_skill_name(skill_cmd)
        if skill_name is None:
            continue
        skill_md_path = _resolve_skill_md(
            skill_name,
            project_root=ctx.project_dir,
            resolver=ctx.skill_resolver,
        )
        if skill_md_path is None:
            continue
        try:
            skill_content = skill_md_path.read_text(encoding="utf-8")
        except OSError:
            logger.warning("Failed to read %s", skill_md_path, exc_info=True)
            continue
        python_blocks = extract_python_blocks(skill_content)
        if python_blocks:
            yield skill_name, python_blocks


@semantic_rule(
    name="pseudocode-callable-divergence",
    description=(
        "A run_python callable defines frozenset/set constants whose members are "
        "inlined in the corresponding SKILL.md pseudocode without referencing the "
        "constant by name — divergence risk if the constant is updated."
    ),
    severity=Severity.WARNING,
)
def _check_pseudocode_callable_divergence(ctx: ValidationContext) -> list[RuleFinding]:
    findings: list[RuleFinding] = []

    for step_name, step in ctx.recipe.steps.items():
        if step.tool != "run_python":
            continue
        callable_path = str(step.with_args.get("callable", ""))
        if not callable_path.startswith("autoskillit.smoke_utils"):
            continue
        family = step.phoropter_family
        if not family:
            continue

        source_dir = _get_package_source_dir(callable_path)
        if source_dir is None:
            continue
        constants = _find_frozenset_constants(source_dir)
        if not constants:
            continue

        for skill_name, python_blocks in _iter_family_skill_python_blocks(ctx, family):
            all_blocks = "\n".join(python_blocks)

            for const_name, members in constants.items():
                if const_name in all_blocks:
                    continue
                # Check if ALL members are inlined as literals without the constant name
                if members and all(_member_inlined_in_block(m, all_blocks) for m in members):
                    findings.append(
                        make_finding(
                            rule_name="pseudocode-callable-divergence",
                            step_name=step_name,
                            message=(
                                f"step '{step_name}': SKILL.md for '{skill_name}' inlines "
                                f"all members of {const_name!r} as literals without "
                                f"referencing the constant by name. If {const_name!r} is "
                                f"updated, the pseudocode will silently diverge."
                            ),
                        )
                    )
    return findings
