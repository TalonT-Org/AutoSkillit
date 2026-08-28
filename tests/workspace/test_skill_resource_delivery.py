"""Projection and session-delivery contracts for static skill resources."""

from __future__ import annotations

import hashlib
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from autoskillit.core import (
    BackendConventions,
    DirectInstall,
    PluginLoadMode,
    SkillExecutionRole,
    SkillInvalidityKind,
    SkillSource,
    pkg_root,
)
from autoskillit.workspace import (
    DefaultSessionSkillManager,
    EffectiveSkillCatalog,
    ProjectionCacheKey,
    SkillCatalogEntry,
    SkillProjectionContext,
    SkillsDirectoryProvider,
    project_agent_skill_document,
    project_direct_install_authority,
)
from autoskillit.workspace._projection_cache import public_plugin_asset_digest
from autoskillit.workspace.skill_resources import SkillResourceDef, load_skill_resource
from autoskillit.workspace.skills import (
    DefaultSkillResolver,
    SkillInfo,
    _skill_info_from_frontmatter,
)

pytestmark = [pytest.mark.layer("workspace"), pytest.mark.medium]


def _resource_entries() -> tuple[SkillCatalogEntry, ...]:
    entries = tuple(
        SkillCatalogEntry.from_skill_info(skill)
        for skill in DefaultSkillResolver().list_all()
        if skill.required_resources
    )
    assert entries, "expected at least one bundled skill declaring requires_resources"
    return entries


def _catalog_for(entry: SkillCatalogEntry) -> EffectiveSkillCatalog:
    return EffectiveSkillCatalog(skills=(entry,), execution_role=entry.execution_role)


def _resource_marker(resource: SkillResourceDef) -> str:
    rows = "" if resource.table_row_count is None else str(resource.table_row_count)
    return (
        f'<!-- autoskillit:skill-resource id="{resource.id}" '
        f'digest="{resource.digest}" rows="{rows}" -->'
    )


def _resource_section(content: str, resource: SkillResourceDef) -> str:
    heading = f"## Provided resource: {resource.title}"
    assert heading in content, f"missing projected heading for resource {resource.id!r}"
    start = content.index(heading)
    marker = _resource_marker(resource)
    assert marker in content[start:], f"missing projected marker for resource {resource.id!r}"
    end = content.index(marker, start) + len(marker)
    return content[start:end]


def test_catalog_bound_projection_delivers_every_declared_resource(tmp_path: Path) -> None:
    """The production catalog path carries resource identity into projection."""
    for entry in _resource_entries():
        document = project_agent_skill_document(
            entry,
            SkillProjectionContext(cwd=tmp_path, catalog=_catalog_for(entry)),
        )
        for resource_id in entry.required_resources:
            resource = load_skill_resource(resource_id)
            section = _resource_section(document.content, resource)
            assert resource.body in section
            assert _resource_marker(resource) in section


def test_resource_sections_are_backend_identical(tmp_path: Path) -> None:
    """Backend adaptation may change skill prose, never provided resource bytes."""
    for entry in _resource_entries():
        backend_entry = replace(entry, semantic_plan=None)
        catalog = _catalog_for(backend_entry)
        projected = [
            project_agent_skill_document(
                backend_entry,
                SkillProjectionContext(
                    cwd=tmp_path,
                    catalog=catalog,
                    backend=SimpleNamespace(
                        name=name,
                        conventions=BackendConventions(),
                    ),
                ),
            )
            for name in ("claude-code", "codex")
        ]
        for resource_id in backend_entry.required_resources:
            resource = load_skill_resource(resource_id)
            assert _resource_section(projected[0].content, resource) == _resource_section(
                projected[1].content, resource
            )


def test_resource_body_is_delivered_verbatim_after_skill_transforms(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Substitution and exploration-looking text in a resource remains literal."""
    import autoskillit.workspace._projected_artifact._documents as documents

    resource = SkillResourceDef(
        id="verbatim-fixture",
        title="Verbatim fixture",
        summary="A literal resource fixture.",
        body=(
            "Keep {{AUTOSKILLIT_TEMP}} unchanged.\n"
            '<!-- autoskillit:exploration-vector id="not-a-skill-marker" -->\n'
        ),
        digest="a" * 64,
        table_row_count=None,
    )
    monkeypatch.setattr(
        documents,
        "load_skill_resource",
        lambda resource_id: (
            resource
            if resource_id == resource.id
            else pytest.fail(f"unexpected resource id: {resource_id}")
        ),
    )
    info = SkillInfo(
        name="verbatim-resource-consumer",
        source=SkillSource.PROJECT_LOCAL,
        path=tmp_path / "SKILL.md",
        canonical_content=(
            "---\n"
            "name: verbatim-resource-consumer\n"
            "description: Fixture.\n"
            "---\n"
            "Authored {{AUTOSKILLIT_TEMP}} text is transformed.\n"
        ),
        required_resources=(resource.id,),
        resource_digests={resource.id: resource.digest},
    )
    entry = SkillCatalogEntry.from_skill_info(info)
    document = project_agent_skill_document(
        entry,
        SkillProjectionContext(
            cwd=tmp_path,
            catalog=_catalog_for(entry),
            substitutions={"{{AUTOSKILLIT_TEMP}}": ".autoskillit/temp"},
        ),
    )

    assert resource.body in document.content
    assert "Keep {{AUTOSKILLIT_TEMP}} unchanged." in document.content
    assert '<!-- autoskillit:exploration-vector id="not-a-skill-marker" -->' in document.content


def _write_resource(path: Path, body: str) -> None:
    path.write_text(
        "---\n"
        "id: digest-fixture\n"
        "title: Digest fixture\n"
        "summary: Digest fixture resource.\n"
        "---\n"
        f"{body}",
        encoding="utf-8",
    )


def _local_resource_entry(skill_path: Path) -> SkillCatalogEntry:
    return SkillCatalogEntry.from_skill_info(
        _skill_info_from_frontmatter(
            "digest-resource-consumer",
            SkillSource.PROJECT_LOCAL,
            skill_path,
        )
    )


def test_editing_resource_bytes_changes_the_projected_digest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fresh catalog binding must not reuse a projection after resource changes."""
    import autoskillit.workspace.skill_resources as resource_module

    resource_root = tmp_path / "resource-root"
    resource_path = resource_root / "skill_resources" / "digest-fixture.md"
    resource_path.parent.mkdir(parents=True)
    _write_resource(resource_path, "first resource body\n")
    monkeypatch.setattr(resource_module, "pkg_root", lambda: resource_root)
    load_skill_resource.cache_clear()

    skill_path = (
        tmp_path / "project" / ".claude" / "skills" / "digest-resource-consumer" / "SKILL.md"
    )
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text(
        "---\n"
        "name: digest-resource-consumer\n"
        "description: Fixture.\n"
        "requires_resources: [digest-fixture]\n"
        "---\n"
        "consumer body\n",
        encoding="utf-8",
    )
    try:
        first_entry = _local_resource_entry(skill_path)
        first = project_agent_skill_document(
            first_entry,
            SkillProjectionContext(cwd=tmp_path, catalog=_catalog_for(first_entry)),
        )

        _write_resource(resource_path, "changed resource body\n")
        load_skill_resource.cache_clear()
        second_entry = _local_resource_entry(skill_path)
        second = project_agent_skill_document(
            second_entry,
            SkillProjectionContext(cwd=tmp_path, catalog=_catalog_for(second_entry)),
        )
    finally:
        load_skill_resource.cache_clear()

    assert first.projected_digest != second.projected_digest
    assert first_entry.resource_digests != second_entry.resource_digests


def test_unknown_resource_is_an_admission_invalidity_before_session_creation(
    tmp_path: Path,
) -> None:
    """Unknown ids are excluded by catalog admission rather than a child-session denial."""
    project_root = tmp_path / "project"
    skill_path = project_root / ".claude" / "skills" / "unknown-resource" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text(
        "---\n"
        "name: unknown-resource\n"
        "description: Fixture.\n"
        "requires_resources: [not-registered]\n"
        "---\n"
        "consumer body\n",
        encoding="utf-8",
    )

    resolver = DefaultSkillResolver()
    _effective, exclusions = resolver.scan_effective(project_root)
    exclusion = next(item for item in exclusions if item.name == "unknown-resource")

    assert {item.kind for item in exclusion.invalidities} == {
        SkillInvalidityKind.RESOURCE_CONTRACT_INVALID
    }
    catalog = resolver.list_effective(project_root, SkillExecutionRole.SESSION)
    assert all(entry.name != "unknown-resource" for entry in catalog.skills)
    assert exclusion in catalog.exclusions
    assert not (tmp_path / "sessions").exists()


def test_session_resource_delivery_writes_only_skill_md(tmp_path: Path) -> None:
    """Provided resources are embedded, preserving one-file session-skill replay."""
    entry = next(
        entry
        for entry in _resource_entries()
        if entry.execution_role is SkillExecutionRole.SESSION
    )
    provider = SkillsDirectoryProvider()
    catalog = _catalog_for(entry)
    manager = DefaultSessionSkillManager(provider, ephemeral_root=tmp_path / "sessions")
    session_root = manager.init_session(
        "resource-delivery",
        catalog,
        provider.catalog_projection_context(
            catalog,
            tmp_path,
            durable_scripts_root=pkg_root(),
        ),
    )

    delivered_dir = Path(session_root.path) / ".claude" / "skills" / entry.name
    assert {path.name for path in delivered_dir.iterdir()} == {"SKILL.md"}


def test_resource_digest_order_has_one_sorted_projection_cache_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sorting resource ids is load-bearing: mapping insertion order is not identity."""
    import autoskillit.workspace._projected_artifact._documents as documents
    import autoskillit.workspace._projected_artifact.authority as projected_artifact_authority

    monkeypatch.setenv("HOME", str(tmp_path))
    resources = {
        "a-resource": SkillResourceDef(
            id="a-resource",
            title="A resource",
            summary="Fixture.",
            body="A body.\n",
            digest="a" * 64,
            table_row_count=None,
        ),
        "z-resource": SkillResourceDef(
            id="z-resource",
            title="Z resource",
            summary="Fixture.",
            body="Z body.\n",
            digest="z" * 64,
            table_row_count=None,
        ),
    }
    monkeypatch.setattr(documents, "load_skill_resource", resources.__getitem__)
    source_root = tmp_path / "plugin"
    skill_path = source_root / "canonical" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    canonical_content = (
        "---\n"
        "name: resource-cache\n"
        "description: Fixture.\n"
        "execution_role: session\n"
        "---\n"
        "cache fixture\n"
    )
    skill_path.write_text(canonical_content, encoding="utf-8")

    def catalog_for(digests: dict[str, str]) -> EffectiveSkillCatalog:
        info = SkillInfo(
            name="resource-cache",
            source=SkillSource.BUNDLED,
            path=skill_path,
            canonical_content=canonical_content,
            required_resources=tuple(digests),
            resource_digests=digests,
        )
        return EffectiveSkillCatalog(
            skills=(SkillCatalogEntry.from_skill_info(info),),
            execution_role=SkillExecutionRole.SESSION,
        )

    first_catalog = catalog_for({"z-resource": "z" * 64, "a-resource": "a" * 64})
    second_catalog = catalog_for({"a-resource": "a" * 64, "z-resource": "z" * 64})
    backend = SimpleNamespace(name="codex", conventions=BackendConventions())
    first_authority = project_direct_install_authority(
        DirectInstall(plugin_dir=source_root),
        cwd=tmp_path,
        base_branch="main",
        catalog=first_catalog,
    )
    second_authority = project_direct_install_authority(
        DirectInstall(plugin_dir=source_root),
        cwd=tmp_path,
        base_branch="main",
        catalog=second_catalog,
    )
    first = first_authority.acquire_launch_binding(
        backend=backend,
        load_mode=PluginLoadMode.EXPLICIT_PLUGIN_DIR,
    )
    second = second_authority.acquire_launch_binding(
        backend=backend,
        load_mode=PluginLoadMode.EXPLICIT_PLUGIN_DIR,
    )
    try:
        info = first_catalog.skills[0]
        skill_identity = (
            f"{info.name}:{info.canonical_digest}:{info.exploration_sidecar_digest}:"
            "a-resource=" + "a" * 64 + ",z-resource=" + "z" * 64
        )
        expected_key = ProjectionCacheKey(
            source_root=str(source_root),
            backend_name="codex",
            projection_version=first_authority.projection_version,
            default_base_branch="main",
            skill_identity=skill_identity,
            adaptation_identity="resource-cache:",
            namespace_identity="",
            asset_digest=public_plugin_asset_digest(source_root),
            rendered_hooks_digest=hashlib.sha256(
                projected_artifact_authority.render_hooks_json_text().encode()
            ).hexdigest(),
        ).digest()

        assert first.identity.semantic_key == second.identity.semantic_key == expected_key
        assert first.plugin_dir is not None
        assert first.plugin_dir.name == expected_key
    finally:
        second.close()
        first.close()
