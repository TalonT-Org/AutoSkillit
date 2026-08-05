"""Structural assertions for arch-lens skills."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from autoskillit.core import RepositoryProfileId, SkillExecutionRole, SkillSource
from autoskillit.core.io import load_yaml
from autoskillit.execution.backends import get_backend
from autoskillit.workspace import (
    EffectiveSkillInvocation,
    SkillInfo,
    SkillProjectionContext,
    project_agent_skill_document,
)
from autoskillit.workspace.skills import _skill_info_from_frontmatter

SKILLS_DIR = Path(__file__).parents[2] / "src/autoskillit/skills_extended"

ARCH_LENS_SLUGS = [
    "c4-container",
    "concurrency",
    "data-lineage",
    "deployment",
    "development",
    "error-resilience",
    "module-dependency",
    "operational",
    "process-flow",
    "repository-access",
    "scenarios",
    "security",
    "state-lifecycle",
]

RELATED_SKILLS_EXECUTION_GUARD = (
    "- Treat Related Skills as executable dependencies or invoke any cross-reference from "
    "that section; those entries are documentation-only and do not imply execution. Invoke "
    "only the required `/autoskillit:mermaid` skill; never invoke "
    "`/autoskillit:make-arch-diag`, another architecture lens, or any other cross-reference."
)

FORBIDDEN_PROJECTED_INVOCATION_TARGET = re.compile(
    r"make-arch-diag|arch-lens-[a-z0-9-]+|audit-arch"
)

pytestmark = [pytest.mark.layer("skills"), pytest.mark.medium]


def _read(slug: str) -> str:
    path = SKILLS_DIR / f"arch-lens-{slug}" / "SKILL.md"
    assert path.exists(), f"arch-lens-{slug}/SKILL.md is missing"
    return path.read_text()


def _frontmatter(text: str) -> dict:
    """Parse YAML frontmatter between the first pair of '---' delimiters."""
    lines = text.splitlines()
    if lines[0].strip() != "---":
        return {}
    end = next((i for i, ln in enumerate(lines[1:], 1) if ln.strip() == "---"), None)
    if end is None:
        return {}
    return load_yaml("\n".join(lines[1:end]))


def _skill_info(slug: str) -> SkillInfo:
    name = f"arch-lens-{slug}"
    info = _skill_info_from_frontmatter(
        name,
        SkillSource.BUNDLED_EXTENDED,
        SKILLS_DIR / name / "SKILL.md",
    )
    assert info.invalid_reason is None, info.invalid_reason
    return info


def _projected_contract(slug: str, backend_name: str) -> tuple[str, dict[str, object]]:
    info = _skill_info(slug)
    project_root = SKILLS_DIR.parents[2]
    invocation = EffectiveSkillInvocation(
        root=info,
        closure=(info,),
        capability_union=info.uses_capabilities,
        project_root=project_root,
        execution_role=SkillExecutionRole.SESSION,
    )
    document = project_agent_skill_document(
        info,
        SkillProjectionContext(
            cwd=project_root,
            invocation=invocation,
            backend=get_backend(backend_name),
            resolved_exploration_profile=RepositoryProfileId.AUTOSKILLIT,
            parent_sandbox_mode="read-only",
        ),
    )
    _, contract = document.content.split(
        "## Backend-adapted semantic execution contract\n\n",
        1,
    )
    return contract, dict(document.adaptation_payload)


@pytest.mark.parametrize("slug", ARCH_LENS_SLUGS)
def test_skill_md_exists(slug: str) -> None:
    path = SKILLS_DIR / f"arch-lens-{slug}" / "SKILL.md"
    assert path.exists(), f"arch-lens-{slug}/SKILL.md missing"


@pytest.mark.parametrize("slug", ARCH_LENS_SLUGS)
def test_has_arguments_section(slug: str) -> None:
    assert "## Arguments" in _read(slug), f"arch-lens-{slug} missing ## Arguments section"


@pytest.mark.parametrize("slug", ARCH_LENS_SLUGS)
def test_documents_context_path(slug: str) -> None:
    assert "context_path" in _read(slug), f"arch-lens-{slug} must document context_path"


@pytest.mark.parametrize("slug", ARCH_LENS_SLUGS)
def test_has_step_0(slug: str) -> None:
    assert "Step 0" in _read(slug), f"arch-lens-{slug} must have Step 0 for argument parsing"


@pytest.mark.parametrize("slug", ARCH_LENS_SLUGS)
def test_diagram_path_token(slug: str) -> None:
    assert "diagram_path" in _read(slug), f"arch-lens-{slug} must mention diagram_path"


@pytest.mark.parametrize("slug", ARCH_LENS_SLUGS)
def test_arch_diag_prefix_in_output_path(slug: str) -> None:
    assert "arch_diag_" in _read(slug), f"arch-lens-{slug} output path must use arch_diag_ prefix"


@pytest.mark.parametrize("slug", ARCH_LENS_SLUGS)
def test_frontmatter_categories(slug: str) -> None:
    fm = _frontmatter(_read(slug))
    assert fm.get("categories") == ["arch-lens"], (
        f"arch-lens-{slug} frontmatter must have categories: [arch-lens]"
    )


@pytest.mark.parametrize("slug", ARCH_LENS_SLUGS)
def test_frontmatter_activate_deps(slug: str) -> None:
    fm = _frontmatter(_read(slug))
    assert fm.get("activate_deps") == ["mermaid"], (
        f"arch-lens-{slug} frontmatter must have activate_deps: [mermaid]"
    )


@pytest.mark.parametrize("slug", ARCH_LENS_SLUGS)
def test_semantic_sibling_skills_only_contains_mermaid(slug: str) -> None:
    fm = _frontmatter(_read(slug))
    assert fm.get("semantic_requirements", {}).get("sibling_skills") == [{"name": "mermaid"}], (
        f"arch-lens-{slug} semantic sibling_skills must contain only mermaid"
    )


@pytest.mark.parametrize("slug", ARCH_LENS_SLUGS)
def test_related_skills_execution_guard_occurs_once_in_never_section(slug: str) -> None:
    text = _read(slug)
    never_section = text.split("**NEVER:**", 1)[1].split("**ALWAYS:**", 1)[0]
    assert text.count(RELATED_SKILLS_EXECUTION_GUARD) == 1
    assert never_section.count(RELATED_SKILLS_EXECUTION_GUARD) == 1


@pytest.mark.parametrize(
    ("backend_name", "mermaid_target"),
    [("claude-code", "/autoskillit:mermaid"), ("codex", "$mermaid")],
)
@pytest.mark.parametrize("slug", ARCH_LENS_SLUGS)
def test_projected_semantic_contract_invokes_only_mermaid(
    slug: str,
    backend_name: str,
    mermaid_target: str,
) -> None:
    contract, adaptation_payload = _projected_contract(slug, backend_name)
    assert adaptation_payload["sibling_skill_targets"] == {"mermaid": mermaid_target}
    assert f"Invoke sibling skill {mermaid_target}." in contract
    assert FORBIDDEN_PROJECTED_INVOCATION_TARGET.search(contract) is None


@pytest.mark.parametrize("slug", ARCH_LENS_SLUGS)
def test_mermaid_load_instruction(slug: str) -> None:
    text = _read(slug)
    assert any("LOAD" in ln and "mermaid" in ln for ln in text.splitlines()), (
        f"arch-lens-{slug} must contain mandatory mermaid skill LOAD instruction"
    )


@pytest.mark.parametrize("slug", ARCH_LENS_SLUGS)
def test_autoskillit_temp_write_path(slug: str) -> None:
    assert "{{AUTOSKILLIT_TEMP}}" in _read(slug), (
        f"arch-lens-{slug} must use {{{{AUTOSKILLIT_TEMP}}}} in write path"
    )
