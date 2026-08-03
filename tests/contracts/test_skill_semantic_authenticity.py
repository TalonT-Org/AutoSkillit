"""Bundled skill prose and semantic declaration conformance."""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("contracts"), pytest.mark.small]


def _parsed_body(path: Path) -> str:
    from autoskillit.workspace import read_skill_frontmatter

    parsed = read_skill_frontmatter(path)
    assert parsed.is_valid, path
    first = parsed.content.find("---")
    second = parsed.content.find("---", first + 3)
    assert second >= 0, path
    return parsed.content[second + 3 :]


def test_bundled_skill_bodies_contain_no_backend_native_portable_syntax() -> None:
    from autoskillit.core import CODEX_VALID_MODEL_IDS, pkg_root

    forbidden = {
        "Agent(",
        "Agent/Task",
        "Agent subagent",
        "Explore subagent",
        "Task(",
        'model: "',
        "run_in_background",
        "spawn_agent",
        "send_message",
        "wait_agent",
        "subagent_type=",
        *CODEX_VALID_MODEL_IDS,
    }
    violations: list[str] = []
    for root_name in ("skills", "skills_extended"):
        for path in sorted((pkg_root() / root_name).glob("*/SKILL.md")):
            body = _parsed_body(path)
            for token in sorted(forbidden):
                if token in body:
                    violations.append(f"{path.parent.name}: {token!r}")
    assert not violations, "raw backend-native portable syntax:\n" + "\n".join(violations)


def test_bundled_parallel_child_instructions_have_semantic_plans() -> None:
    from autoskillit.core import pkg_root
    from autoskillit.workspace import read_skill_frontmatter

    markers = (
        "Start all independent child delegations",
        "Start ALL independent child delegations",
        "Launch parallel child delegations",
        "Spawn parallel child delegations",
    )
    violations: list[str] = []
    for root_name in ("skills", "skills_extended"):
        for path in sorted((pkg_root() / root_name).glob("*/SKILL.md")):
            parsed = read_skill_frontmatter(path)
            assert parsed.is_valid, path
            if any(marker in parsed.body for marker in markers):
                data = parsed.data or {}
                if data.get("semantic_version") is None:
                    violations.append(path.parent.name)
    assert not violations, "parallel child instructions without semantic plans:\n" + "\n".join(
        violations
    )


def test_every_migrated_semantic_declaration_participates_in_conformance() -> None:
    from autoskillit.core import SkillSource, pkg_root
    from autoskillit.workspace.skills import _skill_info_from_frontmatter

    migrated = 0
    violations: list[str] = []
    for root_name in ("skills", "skills_extended"):
        for path in sorted((pkg_root() / root_name).glob("*/SKILL.md")):
            content = path.read_text(encoding="utf-8")
            if "semantic_version:" not in content:
                continue
            migrated += 1
            info = _skill_info_from_frontmatter(path.parent.name, SkillSource.BUNDLED, path)
            if info.invalid_reason is not None or info.semantic_plan is None:
                violations.append(f"{path}: {info.invalid_reason or 'missing semantic plan'}")
    assert migrated > 0
    assert not violations, "invalid migrated semantic declarations:\n" + "\n".join(violations)


def test_every_bundled_semantic_plan_adapts_on_every_registered_backend() -> None:
    from autoskillit.execution.backends import BACKEND_REGISTRY
    from autoskillit.workspace import DefaultSkillResolver

    plans = tuple(
        (skill.name, skill.semantic_plan)
        for skill in DefaultSkillResolver().list_all()
        if skill.semantic_plan is not None
    )
    violations: list[str] = []
    for backend_name, backend_factory in BACKEND_REGISTRY.items():
        backend = backend_factory()
        for skill_name, plan in plans:
            assert plan is not None
            adaptation = backend.adapt_skill_semantics(plan)
            if adaptation.unsupported_operation is not None:
                violations.append(
                    f"{skill_name}/{backend_name}: {adaptation.diagnostic or 'unsupported'}"
                )
            elif not adaptation.instruction_fragments:
                violations.append(f"{skill_name}/{backend_name}: empty adaptation")
    assert plans
    assert not violations, "bundled semantic adaptation failures:\n" + "\n".join(violations)
