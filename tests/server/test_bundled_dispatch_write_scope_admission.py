"""Bundled recipe dispatches stay inside their skills' declared write scopes.

Every tracked bundled ``run_skill`` dispatch is admitted by the real runtime admission
code (``_resolve_dispatch_paths`` then ``_extend_closure_write_scope``), and the
``skill-write-path-recipe-alignment`` rule flags exactly the dispatches that code would
reject. A templated-skill dispatch such as ``/autoskillit:vis-lens-{slug}`` passes no
``output_dir``: each family member declares its own scope, so no single directory can be
checked against all of them.
"""

from __future__ import annotations

import re
import textwrap
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from autoskillit.core import RecipeSource, SkillExecutionRole, resolve_skill_name
from autoskillit.recipe import load_recipe, run_semantic_rules
from tests._tracked_recipes import tracked_recipe_paths

pytestmark = [pytest.mark.layer("server"), pytest.mark.medium]

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_RULE_NAME = "skill-write-path-recipe-alignment"
_WIDENS_MARKER = "is outside the declared write scope of skill"
_TEMPLATE_RE = re.compile(r"\$\{\{.*?\}\}")

# Each probe mirrors the directory its producer creates: planner.create_run_dir.
REVIEWED_TEMPLATE_ROOTS = {"${{ context.planner_dir }}": ".autoskillit/temp/planner/run-probe"}


def _flagged_steps(recipe) -> frozenset[str]:
    return frozenset(
        finding.step_name
        for finding in run_semantic_rules(recipe)
        if finding.rule == _RULE_NAME and _WIDENS_MARKER in finding.message
    )


def _dry_run(tool_ctx, skill_command: str, output_dir: str, cwd: Path) -> tuple[bool, str]:
    from autoskillit.server.tools.tools_execution._run_skill_session import (
        _extend_closure_write_scope,
        _resolve_dispatch_paths,
    )

    skill_name = resolve_skill_name(skill_command)
    assert skill_name is not None
    invocation = tool_ctx.skill_resolver.resolve_invocation(
        skill_name, tool_ctx.project_dir, SkillExecutionRole.SESSION
    )
    state = SimpleNamespace(
        output_dir=output_dir,
        closure_spec=None,
        skill_command=skill_command,
        cwd=str(cwd),
        invocation=invocation,
    )
    terminal = _resolve_dispatch_paths(state, base_cwd=cwd) or _extend_closure_write_scope(state)
    return terminal is None, str(invocation.root.write_scope.kind)


@dataclass
class _Sweep:
    tool_ctx: Any
    cwd: Path
    violations: list[str] = field(default_factory=list)
    reached: set[str] = field(default_factory=set)
    controls: set[str] = field(default_factory=set)

    def concrete(self, output_dir: str) -> tuple[str | None, bool]:
        for root, probe in REVIEWED_TEMPLATE_ROOTS.items():
            if output_dir.startswith(root):
                self.reached.add(root)
                return probe + _TEMPLATE_RE.sub("probe", output_dir[len(root) :]), True
        if "${{" in output_dir.split("/", 1)[0]:
            return None, False
        return _TEMPLATE_RE.sub("probe", output_dir), False

    def step(self, label: str, with_args: dict[str, Any], flagged: bool) -> None:
        output_dir = with_args.get("output_dir", "") or ""
        skill_command = with_args.get("skill_command", "") or ""
        if resolve_skill_name(skill_command) is None:
            if output_dir:
                self.violations.append(f"{label}: templated skill dispatch passes output_dir")
            return
        concrete, reviewed = self.concrete(output_dir)
        if concrete is None:
            self.violations.append(f"{label}: unreviewed template root in {output_dir!r}")
            return
        admitted, kind = _dry_run(self.tool_ctx, skill_command, concrete, self.cwd)
        if not admitted:
            self.violations.append(f"{label}: runtime rejects output_dir {output_dir!r}")
        if not reviewed and flagged == admitted:
            self.violations.append(f"{label}: rule flagged={flagged}, runtime admitted={admitted}")
        if admitted and kind == "bounded":
            self.controls.add("explicit" if output_dir else "omitted")


def test_bundled_dispatches_are_admitted_and_agree_with_the_rule(tool_ctx, tmp_path: Path) -> None:
    sweep = _Sweep(tool_ctx, tmp_path / "project")
    paths = tracked_recipe_paths(_PROJECT_ROOT, source=RecipeSource.BUILTIN, scan_dirs=(".",))
    assert paths
    for path in paths:
        recipe = load_recipe(path)
        flagged = _flagged_steps(recipe)
        for step_name, step in recipe.steps.items():
            if step.tool == "run_skill":
                sweep.step(f"{path.stem}.{step_name}", step.with_args or {}, step_name in flagged)

    assert not sweep.violations, "\n".join(sweep.violations)
    assert sweep.controls == {"explicit", "omitted"}
    assert sweep.reached == set(REVIEWED_TEMPLATE_ROOTS), "stale REVIEWED_TEMPLATE_ROOTS key"


def _one_step_recipe(tmp_path: Path, skill: str, output_dir: str | None, cwd: str | None) -> Path:
    with_lines = "".join(
        f'\n      {key}: "{value}"'
        for key, value in (("output_dir", output_dir), ("cwd", cwd))
        if value is not None
    )
    path = tmp_path / "control.yaml"
    path.write_text(
        textwrap.dedent(
            """\
            name: control
            kitchen_rules:
              - "test"
            steps:
              dispatch:
                tool: run_skill
                with:
                  skill_command: "/autoskillit:{skill} probe"{with_lines}
                on_success: done
              done:
                action: stop
                message: done
            """
        ).format(skill=skill, with_lines=with_lines),
        encoding="utf-8",
    )
    return path


@pytest.mark.parametrize(
    ("skill", "output_dir", "cwd", "concrete", "rejected"),
    [
        ("make-plan", ".", None, ".", True),
        ("make-plan", ".autoskillit/temp", None, ".autoskillit/temp", True),
        ("make-plan", "/", None, "/", True),
        ("make-plan", "${{ context.work_dir }}", "${{ context.work_dir }}", ".", True),
        (
            "make-plan",
            "${{ context.work_dir }}/.autoskillit/temp",
            "${{ context.work_dir }}",
            ".autoskillit/temp",
            True,
        ),
        ("make-plan", None, None, "", False),
        (
            "make-plan",
            ".autoskillit/temp/make-plan/iter_${{ context.n }}",
            None,
            ".autoskillit/temp/make-plan/iter_probe",
            False,
        ),
        ("resolve-review", ".", None, ".", False),
        ("vis-lens-always-on", None, None, "", False),
        (
            "vis-lens-always-on",
            ".autoskillit/temp/run-vis-lenses",
            None,
            ".autoskillit/temp/run-vis-lenses",
            True,
        ),
    ],
)
def test_rule_and_runtime_agree_on_controls(
    tool_ctx,
    tmp_path: Path,
    skill: str,
    output_dir: str | None,
    cwd: str | None,
    concrete: str,
    rejected: bool,
) -> None:
    recipe = load_recipe(_one_step_recipe(tmp_path, skill, output_dir, cwd))

    admitted, _kind = _dry_run(
        tool_ctx, f"/autoskillit:{skill} probe", concrete, tmp_path / "project"
    )

    assert ("dispatch" in _flagged_steps(recipe)) is rejected
    assert admitted is not rejected
