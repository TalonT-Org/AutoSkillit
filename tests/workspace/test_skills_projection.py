"""EffectiveSkillCatalog visibility, projection, and direct-install projection tests."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

import autoskillit.workspace.skills as _skills_mod
from autoskillit.core.types import (
    SkillContractError,
    SkillExecutionRole,
    SkillSource,
    SkillVisibilitySpec,
)

pytestmark = [pytest.mark.layer("workspace"), pytest.mark.small]


def test_effective_catalog_is_path_free(tmp_path: Path) -> None:
    from autoskillit.workspace.skills import DefaultSkillResolver

    catalog = DefaultSkillResolver().list_effective(
        tmp_path,
        SkillExecutionRole.SESSION,
    )

    assert not hasattr(catalog, "project_root")
    assert catalog.skills
    for skill in catalog.skills:
        assert not hasattr(skill, "path")
        assert not hasattr(skill, "source_ref")
        assert all(
            not isinstance(value, Path)
            for value in (
                skill.source_identity.logical_name,
                skill.source_identity.search_dir,
                skill.source_identity.precedence,
            )
        )


def _resolver_with_visibility_skills(tmp_path: Path):
    from autoskillit.workspace.skills import DefaultSkillResolver

    skills_dir = tmp_path / "bundled"
    extended_dir = tmp_path / "extended"
    extended_dir.mkdir()
    for name, category in (
        ("core-skill", "kitchen-core"),
        ("research-skill", "research"),
        ("audit-skill", "audit"),
        ("planner-skill", "planner"),
    ):
        skill_dir = skills_dir / name
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            f"---\nname: {name}\ncategories: [{category}]\n---\n# {name}\n",
            encoding="utf-8",
        )
    resolver = DefaultSkillResolver()
    resolver._dir = skills_dir
    resolver._extended_dir = extended_dir
    return resolver


def test_effective_catalog_applies_pack_and_recipe_visibility(tmp_path: Path) -> None:
    resolver = _resolver_with_visibility_skills(tmp_path)

    default_catalog = resolver.list_effective(tmp_path, SkillExecutionRole.SESSION)
    recipe_catalog = resolver.list_effective(
        tmp_path,
        SkillExecutionRole.SESSION,
        recipe_packs=frozenset({"research"}),
    )

    assert "research-skill" not in {skill.name for skill in default_catalog.skills}
    assert "research-skill" not in default_catalog.namespace_sources
    assert "research-skill" in {skill.name for skill in recipe_catalog.skills}
    assert "research-skill" in recipe_catalog.namespace_sources


def test_invalid_project_override_falls_back_to_bundled_with_exclusion_record(
    tmp_path: Path,
) -> None:
    """A conscious revisit of the old fail-closed pin: an invalid shadowing
    copy no longer poisons the catalog — it falls through to the valid
    bundled twin, recorded as an exclusion rather than raising."""
    resolver = _resolver_with_visibility_skills(tmp_path)
    override_dir = tmp_path / ".claude" / "skills" / "core-skill"
    override_dir.mkdir(parents=True)
    override_path = override_dir / "SKILL.md"
    override_path.write_text(
        '---\nname: core-skill\n---\nSpawn the worker via `Agent(model="sonnet")`.\n',
        encoding="utf-8",
    )

    catalog = resolver.list_effective(tmp_path, SkillExecutionRole.SESSION)

    entry = next(skill for skill in catalog.skills if skill.name == "core-skill")
    assert entry.source is SkillSource.BUNDLED
    assert len(catalog.exclusions) == 1
    exclusion = catalog.exclusions[0]
    assert exclusion.name == "core-skill"
    assert exclusion.path == override_path
    assert exclusion.fallback is SkillSource.BUNDLED


def test_invalid_project_only_skill_is_excluded_without_poisoning_catalog(
    tmp_path: Path,
) -> None:
    resolver = _resolver_with_visibility_skills(tmp_path)
    skill_dir = tmp_path / ".claude" / "skills" / "project-only"
    skill_dir.mkdir(parents=True)
    skill_path = skill_dir / "SKILL.md"
    skill_path.write_text(
        '---\nname: project-only\n---\nSpawn via `Agent(model="sonnet")`.\n',
        encoding="utf-8",
    )

    selected = resolver.resolve_effective("project-only", tmp_path)
    catalog = resolver.list_effective(tmp_path, SkillExecutionRole.SESSION)

    assert selected is not None
    assert selected.source is SkillSource.PROJECT_LOCAL
    assert selected.path == skill_path
    assert selected.invalidities
    assert "project-only" not in {skill.name for skill in catalog.skills}
    assert "project-only" not in catalog.namespace_sources
    assert len(catalog.exclusions) == 1
    exclusion = catalog.exclusions[0]
    assert exclusion.name == "project-only"
    assert exclusion.path == skill_path
    assert exclusion.fallback is None


def test_effective_catalog_applies_subsets_and_recipe_features(tmp_path: Path) -> None:
    resolver = _resolver_with_visibility_skills(tmp_path)
    visibility = SkillVisibilitySpec(
        disabled_categories=frozenset({"audit"}),
    )

    catalog = resolver.list_effective(
        tmp_path,
        SkillExecutionRole.SESSION,
        visibility=visibility,
        recipe_features=frozenset({"planner"}),
    )
    names = {skill.name for skill in catalog.skills}

    assert "audit-skill" not in names
    assert "audit-skill" not in catalog.namespace_sources
    assert "planner-skill" in names
    assert "planner-skill" in catalog.namespace_sources


def test_effective_catalog_keeps_only_available_external_namespace_targets(
    tmp_path: Path,
) -> None:
    resolver = _resolver_with_visibility_skills(tmp_path)

    catalog = resolver.list_effective(
        tmp_path,
        SkillExecutionRole.SESSION,
        allow_only=frozenset({"core-skill"}),
    )

    assert {skill.name for skill in catalog.skills} == {"core-skill"}
    assert "audit-skill" in catalog.namespace_sources
    assert "research-skill" not in catalog.namespace_sources


def test_explicit_invocation_bypasses_feature_but_not_pack_visibility(
    tmp_path: Path,
) -> None:
    resolver = _resolver_with_visibility_skills(tmp_path)
    visibility = SkillVisibilitySpec()

    invocation = resolver.resolve_invocation(
        "planner-skill",
        tmp_path,
        SkillExecutionRole.SESSION,
        visibility=visibility,
    )

    assert invocation.root.name == "planner-skill"
    with pytest.raises(SkillContractError, match="disabled"):
        resolver.resolve_invocation(
            "research-skill",
            tmp_path,
            SkillExecutionRole.SESSION,
            visibility=visibility,
        )


def test_effective_invocation_rejects_inconsistent_direct_construction(
    tmp_path: Path,
) -> None:
    from dataclasses import replace

    resolver = _resolver_with_visibility_skills(tmp_path)
    invocation = resolver.resolve_invocation(
        "core-skill",
        tmp_path,
        SkillExecutionRole.SESSION,
    )

    with pytest.raises(SkillContractError, match="root.*closure"):
        replace(invocation, closure=())
    with pytest.raises(SkillContractError, match="role"):
        replace(invocation, execution_role=SkillExecutionRole.ORCHESTRATOR)
    with pytest.raises(SkillContractError, match="capability union"):
        replace(invocation, capability_union=frozenset({"run_skill"}))


def test_projection_reuses_the_single_frontmatter_parse(tmp_path: Path, monkeypatch) -> None:
    import autoskillit.workspace._projected_artifact.materialization as projection_module
    from autoskillit.workspace import (
        EffectiveSkillCatalog,
        SkillCatalogEntry,
        SkillProjectionContext,
        project_agent_skill_document,
    )
    from autoskillit.workspace.skills import _skill_info_from_frontmatter

    skill_md = tmp_path / "SKILL.md"
    skill_md.write_text(
        "---\nname: parsed-once\ndescription: Parsed once.\n---\nbody\n",
        encoding="utf-8",
    )
    info = _skill_info_from_frontmatter("parsed-once", SkillSource.PROJECT_LOCAL, skill_md)
    entry = SkillCatalogEntry.from_skill_info(info)
    catalog = EffectiveSkillCatalog(
        skills=(entry,),
        execution_role=SkillExecutionRole.SESSION,
    )
    monkeypatch.setattr(
        projection_module,
        "parse_frontmatter_content",
        lambda _content: pytest.fail("projection reparsed canonical frontmatter"),
    )

    document = project_agent_skill_document(
        entry,
        SkillProjectionContext(cwd=tmp_path, catalog=catalog),
    )

    assert document.content.endswith("body\n")
    assert entry.frontmatter is info.frontmatter
    assert not hasattr(_skills_mod, "_read_skill_frontmatter")


def test_projection_context_derives_and_validates_backend_conventions(
    tmp_path: Path,
) -> None:
    from autoskillit.core import BackendConventions
    from autoskillit.workspace import EffectiveSkillCatalog, SkillProjectionContext

    conventions = BackendConventions(skills_subdir=Path("agent-skills"))
    backend = SimpleNamespace(conventions=conventions)
    catalog = EffectiveSkillCatalog(
        skills=(),
        execution_role=SkillExecutionRole.SESSION,
    )

    context = SkillProjectionContext(
        cwd=tmp_path,
        catalog=catalog,
        backend=backend,
    )

    assert context.conventions is conventions
    assert context.parent_sandbox_mode == "workspace-write"
    with pytest.raises(SkillContractError, match="conventions do not match"):
        SkillProjectionContext(
            cwd=tmp_path,
            catalog=catalog,
            backend=backend,
            conventions=BackendConventions(skills_subdir=Path("other-skills")),
        )

    with pytest.raises(SkillContractError, match="parent sandbox"):
        SkillProjectionContext(
            cwd=tmp_path,
            catalog=catalog,
            backend=backend,
            parent_sandbox_mode="danger-full-access",
        )


@pytest.mark.parametrize("invalid_version", [True, 0, -1])
def test_projection_context_requires_exact_positive_version(
    tmp_path: Path,
    invalid_version: object,
) -> None:
    from autoskillit.workspace import EffectiveSkillCatalog, SkillProjectionContext

    catalog = EffectiveSkillCatalog(
        skills=(),
        execution_role=SkillExecutionRole.SESSION,
    )

    with pytest.raises(SkillContractError, match="positive integer"):
        SkillProjectionContext(
            cwd=tmp_path,
            catalog=catalog,
            projection_version=invalid_version,  # type: ignore[arg-type]
        )


def test_direct_install_projection_cache_identity_and_reuse(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from autoskillit.core import (
        BackendConventions,
        DirectInstall,
        PluginArtifactContentionError,
        PluginLoadMode,
        SkillSourceRef,
    )
    from autoskillit.workspace import (
        EffectiveSkillCatalog,
        SkillCatalogEntry,
        project_direct_install_authority,
    )
    from autoskillit.workspace.skills import _skill_info_from_frontmatter

    monkeypatch.setenv("HOME", str(tmp_path))
    source_root = tmp_path / "plugin"
    skill_path = source_root / "canonical" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text(
        "---\n"
        "name: immutable-cache\n"
        "description: Immutable projection fixture.\n"
        "execution_role: session\n"
        "uses_capabilities: []\n"
        "---\n"
        "base branch: {{DEFAULT_BASE_BRANCH}}\n"
        "external skill: /autoskillit:external\n",
        encoding="utf-8",
    )
    info = _skill_info_from_frontmatter(
        "immutable-cache",
        SkillSource.BUNDLED,
        skill_path,
        source_ref=SkillSourceRef(
            origin=SkillSource.BUNDLED,
            logical_name="immutable-cache",
            skill_path=skill_path,
        ),
    )
    catalog = EffectiveSkillCatalog(
        skills=(SkillCatalogEntry.from_skill_info(info),),
        execution_role=SkillExecutionRole.SESSION,
        namespace_sources={"external": SkillSource.BUNDLED},
    )
    backend = SimpleNamespace(
        name="codex",
        conventions=BackendConventions(),
    )
    source = DirectInstall(plugin_dir=source_root)

    authority = project_direct_install_authority(
        source,
        cwd=tmp_path,
        base_branch="develop",
        catalog=catalog,
    )
    first = authority.acquire_launch_binding(
        backend=backend, load_mode=PluginLoadMode.EXPLICIT_PLUGIN_DIR
    )
    assert first.plugin_dir is not None
    first_inode = first.plugin_dir.stat().st_ino
    projected_skill = first.plugin_dir / "skills" / "immutable-cache" / "SKILL.md"
    assert "base branch: develop" in projected_skill.read_text(encoding="utf-8")
    second = authority.acquire_launch_binding(
        backend=backend,
        load_mode=PluginLoadMode.EXPLICIT_PLUGIN_DIR,
    )
    assert second.plugin_dir is not None
    assert second.plugin_dir == first.plugin_dir
    assert second.plugin_dir.stat().st_ino == first_inode
    manifest_path = first.plugin_dir.parent / (
        f".{first.plugin_dir.name}.autoskillit-projection.json"
    )
    manifest_path.unlink()
    with pytest.raises(PluginArtifactContentionError):
        authority.acquire_launch_binding(
            backend=backend,
            load_mode=PluginLoadMode.EXPLICIT_PLUGIN_DIR,
        )
    assert not manifest_path.exists()
    assert first.plugin_dir.stat().st_ino == first_inode
    first.close()
    second.close()
    recovered = authority.acquire_launch_binding(
        backend=backend,
        load_mode=PluginLoadMode.EXPLICIT_PLUGIN_DIR,
    )
    assert recovered.plugin_dir is not None
    assert recovered.plugin_dir == first.plugin_dir
    assert manifest_path.is_file()

    main_authority = project_direct_install_authority(
        source,
        cwd=tmp_path,
        base_branch="main",
        catalog=catalog,
    )
    main_projection = main_authority.acquire_launch_binding(
        backend=backend,
        load_mode=PluginLoadMode.EXPLICIT_PLUGIN_DIR,
    )
    assert main_projection.plugin_dir is not None
    assert main_projection.plugin_dir != first.plugin_dir
    assert "base branch: main" in (
        main_projection.plugin_dir / "skills" / "immutable-cache" / "SKILL.md"
    ).read_text(encoding="utf-8")

    local_namespace_catalog = EffectiveSkillCatalog(
        skills=catalog.skills,
        execution_role=SkillExecutionRole.SESSION,
        namespace_sources={"external": SkillSource.PROJECT_LOCAL},
    )
    local_authority = project_direct_install_authority(
        source,
        cwd=tmp_path,
        base_branch="develop",
        catalog=local_namespace_catalog,
    )
    local_namespace_projection = local_authority.acquire_launch_binding(
        backend=backend,
        load_mode=PluginLoadMode.EXPLICIT_PLUGIN_DIR,
    )
    assert local_namespace_projection.plugin_dir is not None
    assert local_namespace_projection.plugin_dir != first.plugin_dir
    assert "external skill: /external" in (
        local_namespace_projection.plugin_dir / "skills" / "immutable-cache" / "SKILL.md"
    ).read_text(encoding="utf-8")
    for binding in (local_namespace_projection, main_projection, recovered, second, first):
        binding.close()
        assert binding.closed


def test_projection_strips_all_machine_authority_and_preserves_private_deps(
    tmp_path: Path,
) -> None:
    from autoskillit.core import MACHINE_ONLY_SKILL_FRONTMATTER_KEYS
    from autoskillit.workspace import (
        EffectiveSkillCatalog,
        SkillCatalogEntry,
        SkillProjectionContext,
        parse_frontmatter_content,
        project_agent_skill_document,
    )
    from autoskillit.workspace.skills import _skill_info_from_frontmatter

    expected_machine_keys = frozenset(
        {
            "activate_deps",
            "execution_role",
            "exploration_vectors",
            "semantic_requirements",
            "semantic_version",
            "uses_capabilities",
        }
    )
    assert MACHINE_ONLY_SKILL_FRONTMATTER_KEYS == expected_machine_keys
    skill_md = tmp_path / "SKILL.md"
    skill_md.write_text(
        "---\n"
        "name: projected-contract\n"
        "description: Public description.\n"
        "uses_capabilities: []\n"
        "execution_role: session\n"
        "activate_deps: [dependency]\n"
        "---\n"
        "public body\n",
        encoding="utf-8",
    )
    info = _skill_info_from_frontmatter(
        "projected-contract",
        SkillSource.PROJECT_LOCAL,
        skill_md,
    )
    entry = SkillCatalogEntry.from_skill_info(info)
    catalog = EffectiveSkillCatalog(
        skills=(entry,),
        execution_role=SkillExecutionRole.SESSION,
    )

    document = project_agent_skill_document(
        entry,
        SkillProjectionContext(cwd=tmp_path, catalog=catalog),
    )
    projected = parse_frontmatter_content(document.content)

    assert projected.is_valid and projected.data is not None
    assert expected_machine_keys.isdisjoint(projected.data)
    assert projected.data["description"] == "Public description."
    assert document.content.endswith("public body\n")
    assert info.activate_deps == ("dependency",)
    assert entry.activate_deps == ("dependency",)


@pytest.mark.parametrize(
    ("source", "expected_reference"),
    [
        (SkillSource.BUNDLED, "/autoskillit:target"),
        (SkillSource.BUNDLED_EXTENDED, "/target"),
        (SkillSource.PROJECT_LOCAL, "/target"),
        (SkillSource.THIRD_PARTY, "/target"),
    ],
)
def test_projection_namespace_is_exhaustive_for_every_source(
    tmp_path: Path,
    source: SkillSource,
    expected_reference: str,
) -> None:
    from autoskillit.workspace import (
        EffectiveSkillCatalog,
        SkillCatalogEntry,
        SkillProjectionContext,
        project_agent_skill_document,
    )
    from autoskillit.workspace.skills import _skill_info_from_frontmatter

    def write_skill(name: str, body: str, origin: SkillSource) -> SkillCatalogEntry:
        skill_md = tmp_path / origin.value / name / "SKILL.md"
        skill_md.parent.mkdir(parents=True, exist_ok=True)
        skill_md.write_text(
            f"---\nname: {name}\ndescription: Fixture.\n---\n{body}\n",
            encoding="utf-8",
        )
        return SkillCatalogEntry.from_skill_info(
            _skill_info_from_frontmatter(name, origin, skill_md)
        )

    root = write_skill("root", "Call /autoskillit:target now.", SkillSource.BUNDLED)
    target = write_skill("target", "Target.", source)
    catalog = EffectiveSkillCatalog(
        skills=(root, target),
        execution_role=SkillExecutionRole.SESSION,
    )

    projected = project_agent_skill_document(
        root,
        SkillProjectionContext(cwd=tmp_path, catalog=catalog),
    )

    assert f"Call {expected_reference} now." in projected.content
    assert {member.value for member in SkillSource} == {
        "bundled",
        "bundled_extended",
        "project_local",
        "third_party",
    }


@pytest.mark.parametrize(
    "source",
    [SkillSource.PROJECT_LOCAL, SkillSource.THIRD_PARTY],
)
def test_projection_never_mutates_external_canonical_sources(
    tmp_path: Path,
    source: SkillSource,
) -> None:
    from autoskillit.workspace import (
        EffectiveSkillCatalog,
        SkillCatalogEntry,
        SkillProjectionContext,
        project_agent_skill_document,
    )
    from autoskillit.workspace.skills import _skill_info_from_frontmatter

    skill_md = tmp_path / source.value / "external" / "SKILL.md"
    skill_md.parent.mkdir(parents=True)
    skill_md.write_text(
        "---\n"
        "name: external\n"
        "description: External source.\n"
        "uses_capabilities: []\n"
        "execution_role: session\n"
        "---\n"
        "external body\n",
        encoding="utf-8",
    )
    before = skill_md.read_bytes()
    info = _skill_info_from_frontmatter("external", source, skill_md)
    entry = SkillCatalogEntry.from_skill_info(info)
    catalog = EffectiveSkillCatalog(
        skills=(entry,),
        execution_role=SkillExecutionRole.SESSION,
    )

    document = project_agent_skill_document(
        entry,
        SkillProjectionContext(cwd=tmp_path, catalog=catalog),
    )

    assert "uses_capabilities:" not in document.content
    assert "execution_role:" not in document.content
    assert skill_md.read_bytes() == before
