"""Phase 2 tests: session_skills module — provider and core manager."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from autoskillit.core import ClaudeDirectoryConventions
from autoskillit.core.io import load_yaml
from autoskillit.workspace.session_skills import (
    DefaultSessionSkillManager,
    SkillsDirectoryProvider,
    resolve_ephemeral_root,
)

pytestmark = [pytest.mark.layer("workspace"), pytest.mark.small]

_MACHINE_ONLY_FRONTMATTER_KEYS = {
    "uses_capabilities",
    "execution_role",
    "backend_requirements",
}


def _frontmatter(content: str) -> dict[str, object]:
    match = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
    assert match, "Content must have YAML frontmatter"
    return load_yaml(match.group(1))


def _assert_agent_safe(content: str) -> None:
    assert _MACHINE_ONLY_FRONTMATTER_KEYS.isdisjoint(_frontmatter(content))


def test_resolve_ephemeral_root_returns_writable_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import autoskillit.workspace.session_skills as ss

    monkeypatch.setattr(ss, "_CANDIDATE_ROOTS", [tmp_path])
    root = resolve_ephemeral_root()
    assert root.exists()
    assert root.is_dir()
    test_file = root / "write_test.tmp"
    test_file.write_text("ok")
    test_file.unlink()


def test_resolve_ephemeral_root_fallback(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import autoskillit.workspace.session_skills as ss

    monkeypatch.setattr(ss, "_CANDIDATE_ROOTS", [Path("/nonexistent"), tmp_path])
    root = ss.resolve_ephemeral_root()
    assert root.exists()


def test_skills_directory_provider_lists_all_skills() -> None:
    provider = SkillsDirectoryProvider()
    skills = provider.list_skills()
    names = {s.name for s in skills}
    assert "open-kitchen" in names
    assert "close-kitchen" in names
    assert "implement-worktree" in names
    assert "sous-chef" not in names  # internal, excluded


def test_provider_injects_disable_model_invocation_for_tier2() -> None:
    provider = SkillsDirectoryProvider()
    skill = provider.resolver.resolve_effective("open-kitchen", Path.cwd())
    assert skill is not None
    content = provider.get_skill_content(skill, cwd=Path.cwd(), gated=True)
    fm_match = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
    assert fm_match, "Content must have YAML frontmatter"
    fm = load_yaml(fm_match.group(1))
    assert fm.get("disable-model-invocation") is True


def test_provider_does_not_inject_for_cook_session() -> None:
    # Use mermaid (skills_extended/, no flag at rest) to verify that gated=False
    # returns unmodified content without injecting disable-model-invocation.
    # open-kitchen and close-kitchen carry disable-model-invocation: true in their source
    # (human-only skills), so they cannot be used to assert "flag not present".
    provider = SkillsDirectoryProvider()
    skill = provider.resolver.resolve_effective("mermaid", Path.cwd())
    assert skill is not None
    content = provider.get_skill_content(skill, cwd=Path.cwd(), gated=False)
    fm_match = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
    assert fm_match, "Content must have YAML frontmatter"
    fm = load_yaml(fm_match.group(1))
    assert fm.get("disable-model-invocation") is not True


def test_agent_skill_projector_preserves_public_document_and_stable_digest(
    tmp_path: Path,
) -> None:
    from autoskillit.core import SkillExecutionRole, SkillSource
    from autoskillit.workspace import (
        AgentSkillDocument,
        SkillCatalogEntry,
        SkillProjectionContext,
        project_agent_skill_document,
    )
    from autoskillit.workspace.skills import EffectiveSkillCatalog, SkillInfo

    skill_md = tmp_path / "SKILL.md"
    skill_md.write_text(
        "---\n"
        "name: projected-skill\n"
        "description: Public description.\n"
        "uses_capabilities: [agent_model]\n"
        "execution_role: session\n"
        "backend_requirements: [claude-code]\n"
        "metadata:\n"
        "  public-key: public-value\n"
        "---\n"
        "# Public body\n\n"
        "Keep this body byte-for-byte.\n"
    )
    skill_info = SkillInfo(
        name="projected-skill",
        source=SkillSource.BUNDLED_EXTENDED,
        path=skill_md,
    )
    catalog_entry = SkillCatalogEntry.from_skill_info(skill_info)
    context = SkillProjectionContext(
        cwd=tmp_path,
        catalog=EffectiveSkillCatalog(
            skills=(catalog_entry,),
            execution_role=SkillExecutionRole.SESSION,
        ),
    )

    first = project_agent_skill_document(catalog_entry, context)
    second = project_agent_skill_document(catalog_entry, context)

    assert isinstance(first, AgentSkillDocument)
    _assert_agent_safe(first.content)
    frontmatter = _frontmatter(first.content)
    assert frontmatter["name"] == "projected-skill"
    assert frontmatter["description"] == "Public description."
    assert frontmatter["metadata"] == {"public-key": "public-value"}
    assert first.content.endswith("# Public body\n\nKeep this body byte-for-byte.\n")
    assert first.projected_digest == second.projected_digest
    assert first.content == second.content


def test_provider_string_api_returns_unified_agent_safe_projection() -> None:
    provider = SkillsDirectoryProvider()
    raw = provider.resolver.resolve("make-arch-diag")
    assert raw is not None
    assert "uses_capabilities:" in raw.path.read_text()

    content = provider.get_skill_content(raw, cwd=Path.cwd(), gated=False)

    _assert_agent_safe(content)
    assert _frontmatter(content)["name"] == "make-arch-diag"
    assert "# Make-Arch-Diag: Architecture Diagram Generation" in content


def test_materialization_rejects_wrong_role_before_filesystem_work(tmp_path: Path) -> None:
    from autoskillit.core import SkillContractError, SkillExecutionRole, SkillSource
    from autoskillit.workspace import (
        EffectiveSkillInvocation,
        SkillInfo,
        SkillProjectionContext,
    )

    ephemeral_root = tmp_path / "ephemeral"
    manager = DefaultSessionSkillManager(SkillsDirectoryProvider(), ephemeral_root)
    skill = SkillInfo(
        name="orchestrator",
        source=SkillSource.PROJECT_LOCAL,
        path=tmp_path / "source" / "SKILL.md",
        execution_role=SkillExecutionRole.ORCHESTRATOR,
        canonical_content=(
            "---\nname: orchestrator\ndescription: Wrong role.\n"
            "execution_role: orchestrator\n---\nbody\n"
        ),
    )
    invocation = EffectiveSkillInvocation(
        root=skill,
        closure=(skill,),
        capability_union=frozenset(),
        project_root=tmp_path,
        execution_role=SkillExecutionRole.SESSION,
    )
    context = SkillProjectionContext(
        cwd=tmp_path,
        invocation=invocation,
    )

    with pytest.raises(SkillContractError, match="SESSION"):
        manager.materialize_invocation("wrong-role", invocation, context)

    assert not ephemeral_root.exists()


def test_materialization_revalidates_capability_role_before_filesystem_work(
    tmp_path: Path,
) -> None:
    from autoskillit.core import SkillContractError, SkillExecutionRole, SkillSource
    from autoskillit.workspace import (
        EffectiveSkillInvocation,
        SkillInfo,
        SkillProjectionContext,
    )

    ephemeral_root = tmp_path / "ephemeral"
    manager = DefaultSessionSkillManager(SkillsDirectoryProvider(), ephemeral_root)
    skill = SkillInfo(
        name="forged-session",
        source=SkillSource.PROJECT_LOCAL,
        path=tmp_path / "source" / "SKILL.md",
        execution_role=SkillExecutionRole.SESSION,
        uses_capabilities=frozenset({"run_skill"}),
        canonical_content=(
            "---\nname: forged-session\ndescription: Forged contract.\n"
            "execution_role: session\nuses_capabilities: [run_skill]\n---\n"
            'Call run_skill("child").\n'
        ),
    )
    invocation = EffectiveSkillInvocation(
        root=skill,
        closure=(skill,),
        capability_union=frozenset({"run_skill"}),
        project_root=tmp_path,
        execution_role=SkillExecutionRole.SESSION,
    )
    context = SkillProjectionContext(
        cwd=tmp_path,
        invocation=invocation,
    )

    with pytest.raises(SkillContractError, match="run_skill.*session|session.*run_skill"):
        manager.materialize_invocation("forged-capability", invocation, context)

    assert not ephemeral_root.exists()


def test_review_pr_four_way_metadata_transport_projection_matrix(tmp_path: Path) -> None:
    """Metadata removal is byte-inert; transport prose remains an independent input."""
    from autoskillit.core import SkillExecutionRole, SkillSource
    from autoskillit.workspace import (
        EffectiveSkillCatalog,
        SkillCatalogEntry,
        SkillInfo,
        SkillProjectionContext,
        bundled_skills_extended_dir,
        project_agent_skill_document,
    )

    canonical = (bundled_skills_extended_dir() / "review-pr" / "SKILL.md").read_text()
    with_metadata = canonical.replace(
        "uses_capabilities: [agent_model, github_api_write]",
        "uses_capabilities: [agent_model, github_api_write, run_skill]",
    )
    transport_line = "- Called by the recipe orchestrator via `run_skill` after `open_pr_step`\n"
    variants = {
        "metadata_plus_transport": with_metadata,
        "metadata_removed": canonical,
        "transport_removed": with_metadata.replace(transport_line, ""),
        "both_removed": canonical.replace(transport_line, ""),
    }
    projected: dict[str, str] = {}
    for name, content in variants.items():
        capabilities = (
            frozenset({"agent_model", "github_api_write", "run_skill"})
            if name in {"metadata_plus_transport", "transport_removed"}
            else frozenset({"agent_model", "github_api_write"})
        )
        info = SkillInfo(
            name="review-pr",
            source=SkillSource.BUNDLED_EXTENDED,
            path=tmp_path / name / "SKILL.md",
            execution_role=SkillExecutionRole.SESSION,
            uses_capabilities=capabilities,
            canonical_content=content,
        )
        context = SkillProjectionContext(
            cwd=tmp_path,
            catalog=EffectiveSkillCatalog(
                skills=(SkillCatalogEntry.from_skill_info(info),),
                execution_role=SkillExecutionRole.SESSION,
            ),
        )
        projected[name] = project_agent_skill_document(context.catalog.skills[0], context).content
        _assert_agent_safe(projected[name])

    assert projected["metadata_plus_transport"] == projected["metadata_removed"]
    assert projected["transport_removed"] == projected["both_removed"]
    assert projected["metadata_plus_transport"] != projected["transport_removed"]


def test_session_manager_materializes_exact_catalog(tmp_path: Path) -> None:
    from autoskillit.core import SessionSkillManager, SkillExecutionRole, SkillSource
    from autoskillit.workspace import (
        EffectiveSkillCatalog,
        SkillCatalogEntry,
        SkillInfo,
        SkillProjectionContext,
    )

    canonical = (
        "---\n"
        "name: exact-skill\n"
        "description: Exact catalog member.\n"
        "execution_role: session\n"
        "---\n"
        "# Exact skill\n"
    )
    entry = SkillCatalogEntry.from_skill_info(
        SkillInfo(
            name="exact-skill",
            source=SkillSource.PROJECT_LOCAL,
            path=tmp_path / "source" / "SKILL.md",
            canonical_content=canonical,
        )
    )
    catalog = EffectiveSkillCatalog(
        skills=(entry,),
        execution_role=SkillExecutionRole.SESSION,
    )
    context = SkillProjectionContext(cwd=tmp_path, catalog=catalog)
    manager = DefaultSessionSkillManager(
        SkillsDirectoryProvider(),
        ephemeral_root=tmp_path / "sessions",
    )

    result = manager.init_session("exact", catalog, context)

    assert isinstance(manager, SessionSkillManager)
    projected = (
        Path(result.path)
        / ClaudeDirectoryConventions.ADD_DIR_SKILLS_SUBDIR
        / "exact-skill"
        / "SKILL.md"
    )
    assert projected.read_text().endswith("# Exact skill\n")
    _assert_agent_safe(projected.read_text())
