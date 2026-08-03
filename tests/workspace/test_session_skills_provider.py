"""Tests for exact-catalog session skill projection and manager ownership."""

from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import autoskillit.workspace.session_skills as session_skills
from autoskillit.core import (
    SESSION_ADD_DIR_SUBDIR,
    ClaudeDirectoryConventions,
    SessionSkillManager,
    SkillExecutionRole,
    SkillSource,
)
from autoskillit.core.io import load_yaml
from autoskillit.workspace import (
    AgentSkillDocument,
    DefaultSessionSkillManager,
    EffectiveSkillCatalog,
    SkillCatalogEntry,
    SkillInfo,
    SkillProjectionContext,
    SkillsDirectoryProvider,
    project_agent_skill_document,
    resolve_ephemeral_root,
)
from tests.fakes import adapt_test_skill_semantics
from tests.workspace._helpers import _CODEX_CAPABILITIES

pytestmark = [pytest.mark.layer("workspace"), pytest.mark.small]

_MACHINE_ONLY_FRONTMATTER_KEYS = {
    "uses_capabilities",
    "execution_role",
}


def _frontmatter(content: str) -> dict[str, object]:
    match = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
    assert match, "Content must have YAML frontmatter"
    return load_yaml(match.group(1))


def _assert_agent_safe(content: str) -> None:
    assert _MACHINE_ONLY_FRONTMATTER_KEYS.isdisjoint(_frontmatter(content))


def _catalog_context(
    provider: SkillsDirectoryProvider,
    project_root: Path,
    *,
    backend=None,
):
    catalog = provider.resolver.list_effective(
        project_root,
        SkillExecutionRole.SESSION,
    )
    context = provider.catalog_projection_context(
        catalog,
        project_root,
        backend=backend,
    )
    return catalog, context


def _codex_backend() -> MagicMock:
    backend = MagicMock()
    backend.name = "codex"
    backend.capabilities = _CODEX_CAPABILITIES
    backend.conventions.skills_subdir = ClaudeDirectoryConventions.PLUGIN_DIR_SKILLS_SUBDIR
    backend.ensure_pre_launch.return_value = []
    backend.validate_session_layout.return_value = []
    backend.adapt_skill_semantics.side_effect = adapt_test_skill_semantics
    return backend


def test_resolve_ephemeral_root_returns_writable_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(session_skills, "_CANDIDATE_ROOTS", [tmp_path])
    root = resolve_ephemeral_root()
    assert root.is_dir()
    probe = root / "write_test.tmp"
    probe.write_text("ok")
    probe.unlink()


def test_skills_directory_provider_lists_public_skills() -> None:
    names = {skill.name for skill in SkillsDirectoryProvider().list_skills()}
    assert {"open-kitchen", "close-kitchen", "implement-worktree"} <= names
    assert "sous-chef" not in names


def test_provider_gating_is_agent_safe() -> None:
    provider = SkillsDirectoryProvider()
    skill = provider.resolver.resolve_effective("open-kitchen", Path.cwd())
    assert skill is not None

    content = provider.get_skill_content(skill, cwd=Path.cwd(), gated=True)

    assert _frontmatter(content)["disable-model-invocation"] is True
    _assert_agent_safe(content)


def test_agent_skill_projector_preserves_public_document_and_stable_digest(
    tmp_path: Path,
) -> None:
    canonical = (
        "---\n"
        "name: projected-skill\n"
        "description: Public description.\n"
        "uses_capabilities: []\n"
        "execution_role: session\n"
        "metadata:\n"
        "  public-key: public-value\n"
        "---\n"
        "# Public body\n\n"
        "Keep this body byte-for-byte.\n"
    )
    skill_info = SkillInfo(
        name="projected-skill",
        source=SkillSource.BUNDLED_EXTENDED,
        path=tmp_path / "SKILL.md",
        canonical_content=canonical,
    )
    entry = SkillCatalogEntry.from_skill_info(skill_info)
    catalog = EffectiveSkillCatalog(
        skills=(entry,),
        execution_role=SkillExecutionRole.SESSION,
    )
    context = SkillProjectionContext(cwd=tmp_path, catalog=catalog)

    first = project_agent_skill_document(entry, context)
    second = project_agent_skill_document(entry, context)

    assert isinstance(first, AgentSkillDocument)
    _assert_agent_safe(first.content)
    assert _frontmatter(first.content)["metadata"] == {"public-key": "public-value"}
    assert first.content.endswith("# Public body\n\nKeep this body byte-for-byte.\n")
    assert first.projected_digest == second.projected_digest
    assert first.content == second.content


def test_session_manager_materializes_exact_catalog(tmp_path: Path) -> None:
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


def test_skill_write_failure_rolls_back_unpublished_codex_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    codex_root = tmp_path / "persistent" / "codex-sessions"
    provider = SkillsDirectoryProvider()
    manager = DefaultSessionSkillManager(
        provider,
        ephemeral_root=tmp_path / "ephemeral",
        persistent_root=codex_root,
    )
    backend = _codex_backend()
    catalog, context = _catalog_context(provider, tmp_path, backend=backend)

    def fail_write(*args, **kwargs) -> None:
        del args, kwargs
        raise RuntimeError("skill write failed")

    monkeypatch.setattr(session_skills, "materialize_agent_skill_tree", fail_write)

    with pytest.raises(RuntimeError, match="skill write failed"):
        manager.init_session("0123456789abcdef", catalog, context)

    assert not (codex_root / "0123456789abcdef").exists()
    assert manager._session_roots == {}
    assert manager._session_leases == {}
    assert manager._session_skills_subdirs == {}


def test_skills_subdirectory_is_owned_per_session_not_manager_wide(tmp_path: Path) -> None:
    provider = SkillsDirectoryProvider()
    manager = DefaultSessionSkillManager(
        provider,
        ephemeral_root=tmp_path / "ephemeral",
        persistent_root=tmp_path / "persistent" / "codex-sessions",
    )
    codex_backend = _codex_backend()
    claude_catalog, claude_context = _catalog_context(provider, tmp_path)
    codex_catalog, codex_context = _catalog_context(
        provider,
        tmp_path,
        backend=codex_backend,
    )

    claude_home = manager.init_session(
        "claude-session",
        claude_catalog,
        claude_context,
    )
    codex_home = manager.init_session(
        "0123456789abcdef",
        codex_catalog,
        codex_context,
    )

    assert manager._session_skills_subdirs == {
        "claude-session": Path(SESSION_ADD_DIR_SUBDIR)
        / ClaudeDirectoryConventions.ADD_DIR_SKILLS_SUBDIR,
        "0123456789abcdef": Path(SESSION_ADD_DIR_SUBDIR)
        / ClaudeDirectoryConventions.PLUGIN_DIR_SKILLS_SUBDIR,
    }
    assert (Path(str(claude_home)) / ".claude" / "skills").is_dir()
    assert (Path(str(codex_home)) / "skills").is_dir()
