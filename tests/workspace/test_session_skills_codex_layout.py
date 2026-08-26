"""Codex-backend session layout/materialization/projection delegation tests."""

from __future__ import annotations

from pathlib import Path

import pytest

import autoskillit.workspace.session_skills as session_skills
from autoskillit.core import (
    ClaudeDirectoryConventions,
    ManagedSessionHome,
    PreLaunchReadiness,
    RepositoryProfileId,
    SkillContractError,
    SkillExecutionRole,
    ValidatedAddDir,
    load_bundled_agent_definitions,
    pkg_root,
)
from tests.workspace._helpers import (
    _CODEX_CAPABILITIES,
    _catalog_context,
    _make_codex_backend,
    _managed,
    _materialize,
)

pytestmark = [pytest.mark.layer("workspace"), pytest.mark.small]


def _write_profile_skill(
    source_skills: Path,
    name: str,
    *,
    frontmatter: str = "",
    body: str = "Follow the profile contract.\n",
) -> Path:
    skill_path = source_skills / name / "SKILL.md"
    skill_path.parent.mkdir(parents=True, exist_ok=True)
    skill_path.write_text(
        f"---\nname: {name}\ndescription: Profile fixture.\n{frontmatter}---\n{body}",
        encoding="utf-8",
    )
    return skill_path


def _prepare_codex_profile_source(tmp_path: Path) -> Path:
    source_home = tmp_path / "source-codex-home"
    source_home.mkdir()
    (source_home / "auth.json").write_text("{}\n", encoding="utf-8")
    (source_home / "config.toml").write_text(
        'cli_auth_credentials_store = "keyring"\n', encoding="utf-8"
    )
    return source_home


def test_generated_home_skill_removal_rejects_non_child_path(tmp_path: Path) -> None:
    discovery_root = tmp_path / "skills"
    discovery_root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()

    with pytest.raises(SkillContractError, match="one exact child entry"):
        session_skills._remove_generated_home_skill_entry(discovery_root, "../outside")

    assert outside.is_dir()


def test_codex_init_session_creates_skills_subdir(make_session_skill_manager, codex_env) -> None:
    mgr = make_session_skill_manager()
    session_path = _materialize(
        mgr, "sid", backend=codex_env.backend, names=frozenset({"make-arch-diag"})
    )
    skill_files = list(
        (session_path / ClaudeDirectoryConventions.PLUGIN_DIR_SKILLS_SUBDIR).glob("*/SKILL.md")
    )
    assert len(skill_files) > 0
    assert not (session_path / ClaudeDirectoryConventions.ADD_DIR_SKILLS_SUBDIR).exists()


def test_codex_materializes_exact_guarded_investigate_document(
    make_session_skill_manager,
) -> None:
    """Codex must refuse to project the join-bearing 'investigate' skill (rectify-join)."""
    from autoskillit.core import ExplorationVectorApplicabilityId
    from autoskillit.workspace import (
        DefaultSkillResolver,
        SkillProjectionContext,
        project_agent_skill_document,
    )

    manager = make_session_skill_manager()
    backend = _make_codex_backend()
    invocation = DefaultSkillResolver().resolve_invocation(
        "investigate",
        manager._root,
        SkillExecutionRole.SESSION,
    )
    context = SkillProjectionContext(
        cwd=manager._root,
        invocation=invocation,
        backend=backend,
        resolved_exploration_profile=RepositoryProfileId.AUTOSKILLIT,
        active_exploration_applicabilities=frozenset(ExplorationVectorApplicabilityId),
        parent_sandbox_mode="read-only",
    )

    with pytest.raises(SkillContractError, match="wait-any/mailbox-activity"):
        project_agent_skill_document(invocation.root, context)


def test_materialization_forwards_only_server_explorer_binding_env(
    make_session_skill_manager,
    codex_env,
) -> None:
    manager = make_session_skill_manager()
    catalog, context = _catalog_context(
        manager,
        backend=codex_env.backend,
        names=frozenset({"make-arch-diag"}),
    )
    binding_env = {
        "semantic-code-navigator": {
            "AUTOSKILLIT_EXPLORATION_CAPABILITY": "explore_opaque",
            "AUTOSKILLIT_EXPLORATION_ROLE": "semantic-code-navigator",
            "AUTOSKILLIT_EXPLORATION_SESSION_ID": "sid",
        },
        "repository-impact-profiler": {
            "AUTOSKILLIT_EXPLORATION_CAPABILITY": "explore_opaque",
            "AUTOSKILLIT_EXPLORATION_ROLE": "repository-impact-profiler",
            "AUTOSKILLIT_EXPLORATION_SESSION_ID": "sid",
        },
    }

    manager._materialize_bound_records(
        "sid",
        catalog.skills,
        context,
        explorer_binding_env=binding_env,
    )

    assert codex_env.backend.setup_session_dir.call_args.kwargs["explorer_binding_env"] == (
        binding_env
    )
    assert tuple(
        definition.name
        for definition in codex_env.backend.setup_session_dir.call_args.kwargs["agent_defs"]
    ) == (
        "pluginless-explorer",
        "repository-impact-profiler",
        "semantic-code-navigator",
    )


def test_materialization_mints_explorer_binding_between_prelaunch_and_setup(
    make_session_skill_manager,
    codex_env,
) -> None:
    manager = make_session_skill_manager()
    catalog, context = _catalog_context(
        manager,
        backend=codex_env.backend,
        names=frozenset({"make-arch-diag"}),
    )
    events: list[str] = []
    binding_env = {
        "semantic-code-navigator": {
            "AUTOSKILLIT_EXPLORATION_CAPABILITY": "explore_opaque",
        }
    }

    def _prelaunch(**_kwargs: object) -> PreLaunchReadiness:
        events.append("prelaunch")
        return PreLaunchReadiness((), {})

    def _mint(session_home: Path) -> dict[str, dict[str, str]]:
        assert session_home.is_dir()
        events.append("mint")
        return binding_env

    def _setup(_session_home: Path, **kwargs: object) -> None:
        assert kwargs["explorer_binding_env"] == binding_env
        events.append("setup")

    codex_env.backend.ensure_pre_launch.side_effect = _prelaunch
    codex_env.backend.setup_session_dir.side_effect = _setup

    manager._materialize_bound_records(
        "sid",
        catalog.skills,
        context,
        explorer_binding_env_factory=_mint,
    )

    assert events == ["prelaunch", "mint", "setup"]


def test_materialization_rejects_multiple_explorer_binding_authorities_before_setup(
    make_session_skill_manager,
    codex_env,
) -> None:
    manager = make_session_skill_manager()
    catalog, context = _catalog_context(
        manager,
        backend=codex_env.backend,
        names=frozenset({"investigate"}),
    )
    binding_env = {"semantic-code-navigator": {"TOKEN": "opaque"}}

    with pytest.raises(ValueError, match="map or factory, not both"):
        manager._materialize_bound_records(
            "sid",
            catalog.skills,
            context,
            explorer_binding_env=binding_env,
            explorer_binding_env_factory=lambda _home: binding_env,
        )

    codex_env.backend.ensure_pre_launch.assert_not_called()
    assert "sid" not in manager._session_roots


def test_codex_generated_home_links_projected_catalog_into_discovery_root(
    make_session_skill_manager,
    codex_env,
) -> None:
    mgr = make_session_skill_manager()
    add_dir = _materialize(
        mgr,
        "sid",
        backend=codex_env.backend,
        names=frozenset({"make-arch-diag"}),
    )

    add_dir_path = Path(str(add_dir))
    projected = add_dir_path / "skills" / "make-arch-diag"
    discoverable = add_dir_path.parent / "skills" / "make-arch-diag"

    assert discoverable.is_symlink()
    assert not discoverable.readlink().is_absolute()
    assert discoverable.resolve() == projected.resolve()


def test_codex_discovery_root_uses_admitted_profile_union_with_profile_precedence(
    make_session_skill_manager,
    tmp_path: Path,
) -> None:
    from autoskillit.execution.backends.codex import CodexBackend

    source_home = _prepare_codex_profile_source(tmp_path)
    source_skills = source_home / "skills"
    _write_profile_skill(
        source_skills,
        "make-arch-diag",
        body="PROFILE_COPY_SENTINEL\n",
    )
    _write_profile_skill(source_skills, "profile-only")
    backend = CodexBackend(source_codex_home=source_home)
    manager = make_session_skill_manager()

    with _managed(
        manager,
        "profile-union",
        backend=backend,
        names=frozenset({"make-arch-diag"}),
    ) as managed:
        discovery_root = managed.generated_home / "skills"
        staged_skill = Path(managed.skills_dir.path) / "skills" / "make-arch-diag"
        profile_skill = discovery_root / "make-arch-diag"

        assert {entry.name for entry in discovery_root.iterdir()} == {
            "make-arch-diag",
            "profile-only",
        }
        assert not profile_skill.is_symlink()
        assert "PROFILE_COPY_SENTINEL" in (profile_skill / "SKILL.md").read_text(encoding="utf-8")
        assert "PROFILE_COPY_SENTINEL" not in (staged_skill / "SKILL.md").read_text(
            encoding="utf-8"
        )


def test_codex_init_session_delegates_to_setup_session_dir(
    make_session_skill_manager, codex_env
) -> None:
    mgr = make_session_skill_manager()
    session_path = _materialize(
        mgr, "sid", backend=codex_env.backend, names=frozenset({"make-arch-diag"})
    )
    codex_env.backend.setup_session_dir.assert_called_once_with(
        Path(str(session_path)).parent,
        parent_sandbox_mode="workspace-write",
        agent_defs=tuple(
            definition
            for definition in load_bundled_agent_definitions()
            if definition.name == "pluginless-explorer"
        ),
        execution_role=SkillExecutionRole.SESSION,
    )


def test_empty_codex_catalog_materializes_only_unbound_baseline(
    make_session_skill_manager,
    tmp_path: Path,
) -> None:
    from autoskillit.execution.backends.codex import CodexBackend

    source_home = _prepare_codex_profile_source(tmp_path)
    backend = CodexBackend(source_codex_home=source_home)
    manager = make_session_skill_manager()

    with _managed(
        manager,
        "empty-baseline",
        backend=backend,
        names=frozenset(),
    ) as managed:
        assert {path.stem for path in (managed.generated_home / "agents").glob("*.toml")} == {
            "pluginless-explorer"
        }


def test_bundled_codex_catalog_provisions_exact_admitted_role_union(
    make_session_skill_manager,
    tmp_path: Path,
) -> None:
    from autoskillit.execution.backends.codex import (
        CodexBackend,
        _codex_logical_role_mapping,
    )
    from autoskillit.workspace import compile_session_skill_catalog

    source_home = _prepare_codex_profile_source(tmp_path)
    backend = CodexBackend(source_codex_home=source_home)
    manager = make_session_skill_manager()
    catalog, context = _catalog_context(manager, backend=backend)
    compilation = compile_session_skill_catalog(catalog, backend)
    admitted_names = {skill.name for skill in compilation.catalog.skills}
    admitted_roles = {
        role for roles in compilation.required_native_roles.values() for role in roles
    }
    refused_only_roles: set[str] = set()
    for skill in catalog.skills:
        if skill.name in admitted_names or skill.semantic_plan is None:
            continue
        mapping = _codex_logical_role_mapping(skill.semantic_plan)
        refused_only_roles.update(
            mapping[spawn.role] for spawn in skill.semantic_plan.child_spawns
        )
    refused_only_roles -= admitted_roles
    definitions = load_bundled_agent_definitions()
    authored_names = {definition.name for definition in definitions}
    refused_only_roles &= authored_names
    assert refused_only_roles
    expected_names = {
        definition.name
        for definition in definitions
        if not definition.reader_tools
        and definition.name not in {"repository-impact-profiler", "semantic-code-navigator"}
        and (definition.provisioning == "baseline" or definition.name in admitted_roles)
    }

    with manager.managed_session("bundled-role-union", compilation, context) as managed:
        generated_names = {
            path.stem for path in (managed.generated_home / "agents").glob("*.toml")
        }

        assert generated_names == expected_names
        assert generated_names.isdisjoint(refused_only_roles)
        assert not any(
            record["operation"] == "child_spawn"
            for record in managed.unavailability_payload["unavailable"]
        )


def test_invocation_only_roles_are_forwarded_and_reachability_checked(
    make_session_skill_manager,
    tmp_path: Path,
) -> None:
    from autoskillit.workspace import DefaultSkillResolver, SkillProjectionContext

    project_root = tmp_path / "project"
    skill_path = project_root / ".autoskillit" / "skills" / "invocation-reader" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text(
        "---\n"
        "name: invocation-reader\n"
        "description: Delegate to the session log reader.\n"
        "uses_capabilities: []\n"
        "execution_role: session\n"
        "semantic_version: 1\n"
        "semantic_requirements:\n"
        "  logical_roles:\n"
        "  - name: autoskillit:session-log-reader\n"
        "    purpose: inspect one session log\n"
        "  child_spawns:\n"
        "  - role: autoskillit:session-log-reader\n"
        "    count: 1\n"
        "---\n"
        "Delegate the bounded log inspection.\n",
        encoding="utf-8",
    )
    invocation = DefaultSkillResolver().resolve_invocation(
        "invocation-reader",
        project_root,
        SkillExecutionRole.SESSION,
    )
    backend = _make_codex_backend()
    backend.setup_session_dir.return_value = frozenset(
        {"default", "explorer", "worker", "pluginless-explorer", "session-log-reader"}
    )
    manager = make_session_skill_manager()
    context = SkillProjectionContext(
        cwd=project_root,
        invocation=invocation,
        backend=backend,
    )

    manager.materialize_invocation("invocation-role", invocation, context)

    assert tuple(
        definition.name for definition in backend.setup_session_dir.call_args.kwargs["agent_defs"]
    ) == ("pluginless-explorer", "session-log-reader")

    missing_backend = _make_codex_backend()
    missing_backend.setup_session_dir.return_value = frozenset(
        {"default", "explorer", "worker", "pluginless-explorer"}
    )
    missing_context = SkillProjectionContext(
        cwd=project_root,
        invocation=invocation,
        backend=missing_backend,
    )
    with pytest.raises(SkillContractError, match="session-log-reader"):
        manager.materialize_invocation(
            "missing-invocation-role",
            invocation,
            missing_context,
        )


def test_codex_managed_orchestrator_materializes_exact_catalog(
    make_session_skill_manager,
    codex_env,
) -> None:
    mgr = make_session_skill_manager()
    catalog, _ = _catalog_context(
        mgr,
        backend=codex_env.backend,
        names=frozenset({"sous-chef"}),
        role=SkillExecutionRole.ORCHESTRATOR,
    )

    with _managed(
        mgr,
        "orchestrator",
        backend=codex_env.backend,
        names=frozenset({"sous-chef"}),
        role=SkillExecutionRole.ORCHESTRATOR,
    ) as managed:
        projected_root = Path(managed.skills_dir.path) / "skills"
        discovery_root = managed.generated_home / "skills"
        assert {entry.name for entry in projected_root.iterdir()} == {
            member.name for member in catalog.skills
        }
        for member in catalog.skills:
            discovery = discovery_root / member.name
            assert discovery.is_symlink()
            assert (discovery.resolve() / "SKILL.md").is_file()
            assert not (discovery.resolve() / "SKILL.md").is_symlink()


def test_codex_managed_orchestrator_rejects_discovery_collision(
    make_session_skill_manager,
    codex_env,
) -> None:
    mgr = make_session_skill_manager()
    catalog, _ = _catalog_context(
        mgr,
        backend=codex_env.backend,
        names=frozenset({"sous-chef"}),
        role=SkillExecutionRole.ORCHESTRATOR,
    )
    collision_name = catalog.skills[0].name

    def setup_session_dir(
        session_dir: Path,
        *,
        parent_sandbox_mode: str = "workspace-write",
        agent_defs: tuple[object, ...] | None = None,
        execution_role: SkillExecutionRole = SkillExecutionRole.SESSION,
    ) -> None:
        del agent_defs, parent_sandbox_mode, execution_role
        collision = session_dir / "skills" / collision_name
        collision.mkdir(parents=True)
        (collision / "SKILL.md").write_text("profile collision")

    codex_env.backend.setup_session_dir.side_effect = setup_session_dir
    with pytest.raises(SkillContractError, match="orchestrator skill discovery collision"):
        with _managed(
            mgr,
            "orchestrator",
            backend=codex_env.backend,
            names=frozenset({"sous-chef"}),
            role=SkillExecutionRole.ORCHESTRATOR,
        ):
            pass


def test_no_backend_skips_setup_session_dir(make_session_skill_manager) -> None:
    mgr = make_session_skill_manager()
    result = _materialize(mgr, "sid")
    assert isinstance(result, ValidatedAddDir)


def test_codex_init_session_returns_validated_add_dir(
    make_session_skill_manager, codex_env
) -> None:
    mgr = make_session_skill_manager()
    result = _materialize(
        mgr, "sid", backend=codex_env.backend, names=frozenset({"make-arch-diag"})
    )
    assert isinstance(result, ValidatedAddDir)
    assert str(result).endswith("/sid/add-dir")


def test_claude_backend_still_uses_dot_claude_layout(make_session_skill_manager) -> None:
    mgr = make_session_skill_manager()
    session_path = _materialize(mgr, "sid")
    skill_files = list(session_path.glob("*/SKILL.md"))
    assert skill_files == []
    returned = Path(str(session_path))
    assert returned.name == "add-dir"
    assert list(returned.glob(".claude/skills/*/SKILL.md"))
    assert not list(returned.glob("*/SKILL.md"))


def test_codex_init_session_calls_ensure_pre_launch(make_session_skill_manager, codex_env) -> None:
    """init_session() must call backend.ensure_pre_launch() when mcp_config_capable is True."""
    codex_env.backend.ensure_pre_launch.return_value = PreLaunchReadiness((), {})

    mgr = make_session_skill_manager()
    skills_dir = _materialize(
        mgr, "sid", backend=codex_env.backend, names=frozenset({"make-arch-diag"})
    )
    codex_env.backend.ensure_pre_launch.assert_called_once_with(
        session_dir=Path(str(skills_dir)).parent
    )


def test_codex_init_session_raises_when_pre_launch_fails(
    make_session_skill_manager, codex_env
) -> None:
    """init_session() must raise RuntimeError when ensure_pre_launch() returns errors."""
    codex_env.backend.ensure_pre_launch.return_value = PreLaunchReadiness(
        ("Failed to ensure MCP registration: err",), {}
    )

    mgr = make_session_skill_manager()
    with pytest.raises(RuntimeError, match="Pre-launch check failed"):
        _materialize(mgr, "sid", backend=codex_env.backend, names=frozenset({"make-arch-diag"}))


def test_profile_skills_are_projected_from_the_declared_source(tmp_path: Path) -> None:
    """Profile documents are safely projected, rather than linking their source tree."""
    from autoskillit.core.io import load_yaml
    from autoskillit.execution.backends.codex import CodexBackend
    from autoskillit.workspace import (
        EffectiveSkillCatalog,
        SkillsDirectoryProvider,
        materialize_profile_skills,
    )

    source_home = tmp_path / "source-codex-home"
    source_skills = source_home / "skills"
    _write_profile_skill(
        source_skills,
        "my-skill",
        frontmatter=(
            "uses_capabilities: []\n"
            "execution_role: session\n"
            "semantic_version: 1\n"
            "semantic_requirements:\n"
            "  logical_roles:\n"
            "  - name: helper\n"
            "    purpose: perform the delegated task\n"
            "  child_model_policies:\n"
            "  - role: helper\n"
            "    model_class: sonnet\n"
        ),
        body="Delegate the work to the helper role.\n# MY SKILL\n",
    )
    _write_profile_skill(
        source_skills,
        "orchestrator-only",
        frontmatter="execution_role: orchestrator\n",
    )
    backend = CodexBackend(source_codex_home=source_home)
    catalog = EffectiveSkillCatalog((), execution_role=SkillExecutionRole.SESSION)
    context = SkillsDirectoryProvider().catalog_projection_context(
        catalog,
        tmp_path,
        backend=backend,
        durable_scripts_root=pkg_root(),
    )

    generated_home = tmp_path / "generated-home"
    compilation = materialize_profile_skills(
        generated_home,
        source_skills,
        backend,
        context,
        finalized_native_roles=None,
    )

    target = generated_home / "skills" / "my-skill"
    assert target.is_dir()
    assert not target.is_symlink()
    content = (target / "SKILL.md").read_text()
    frontmatter = load_yaml(content.split("---\n", 2)[1])
    assert {
        "activate_deps",
        "uses_capabilities",
        "execution_role",
    }.isdisjoint(frontmatter)
    assert frontmatter["description"] == "Profile fixture."
    assert "# MY SKILL\n" in content
    assert "## Backend-adapted semantic execution contract" in content
    assert [skill.name for skill in compilation.catalog.skills] == ["my-skill"]
    assert compilation.unavailable == ()
    assert not (generated_home / "skills" / "orchestrator-only").exists()


def test_profile_materialization_applies_semantic_and_finalized_native_role_admission(
    tmp_path: Path,
) -> None:
    from autoskillit.execution.backends.codex import CodexBackend
    from autoskillit.workspace import (
        EffectiveSkillCatalog,
        SkillsDirectoryProvider,
        materialize_profile_skills,
    )

    source_home = tmp_path / "source-codex-home"
    source_skills = source_home / "skills"
    _write_profile_skill(
        source_skills,
        "join-required",
        frontmatter=("semantic_version: 1\nsemantic_requirements:\n  join:\n    required: true\n"),
    )
    _write_profile_skill(
        source_skills,
        "profile-helper",
        frontmatter=(
            "semantic_version: 1\n"
            "semantic_requirements:\n"
            "  logical_roles:\n"
            "  - name: helper\n"
            "    purpose: perform delegated work\n"
            "  child_spawns:\n"
            "  - role: helper\n"
            "    count: 1\n"
        ),
    )
    backend = CodexBackend(source_codex_home=source_home)
    catalog = EffectiveSkillCatalog((), execution_role=SkillExecutionRole.SESSION)
    context = SkillsDirectoryProvider().catalog_projection_context(
        catalog,
        tmp_path,
        backend=backend,
        durable_scripts_root=pkg_root(),
    )

    compilation = materialize_profile_skills(
        tmp_path / "generated-home",
        source_skills,
        backend,
        context,
        finalized_native_roles=frozenset(),
    )

    refusals = {refusal.skill: refusal.operation.value for refusal in compilation.unavailable}
    assert refusals == {
        "join-required": "required_join",
        "profile-helper": "child_spawn",
    }
    assert not (tmp_path / "generated-home" / "skills" / "join-required").exists()
    assert not (tmp_path / "generated-home" / "skills" / "profile-helper").exists()


def test_profile_native_role_is_provisioned_before_setup_and_remains_projected(
    make_session_skill_manager,
    tmp_path: Path,
) -> None:
    from autoskillit.execution.backends.codex import CodexBackend

    source_home = _prepare_codex_profile_source(tmp_path)
    _write_profile_skill(
        source_home / "skills",
        "profile-log-reader",
        frontmatter=(
            "semantic_version: 1\n"
            "semantic_requirements:\n"
            "  logical_roles:\n"
            "  - name: autoskillit:session-log-reader\n"
            "    purpose: inspect one session log\n"
            "  child_spawns:\n"
            "  - role: autoskillit:session-log-reader\n"
            "    count: 1\n"
        ),
    )
    backend = CodexBackend(source_codex_home=source_home)
    manager = make_session_skill_manager()

    with _managed(
        manager,
        "profile-native-role",
        backend=backend,
        names=frozenset(),
    ) as managed:
        assert (managed.generated_home / "agents" / "session-log-reader.toml").is_file()
        assert (managed.generated_home / "skills" / "profile-log-reader" / "SKILL.md").is_file()
        assert not any(
            record["skill"] == "profile-log-reader"
            for record in managed.unavailability_payload["unavailable"]
        )


def test_managed_codex_session_surfaces_profile_refusals_after_materialization(
    make_session_skill_manager,
    tmp_path: Path,
) -> None:
    import json

    from autoskillit.execution.backends.codex import CodexBackend

    source_home = _prepare_codex_profile_source(tmp_path)
    source_skills = source_home / "skills"
    _write_profile_skill(source_skills, "profile-admitted")
    _write_profile_skill(
        source_skills,
        "profile-refused",
        frontmatter=("semantic_version: 1\nsemantic_requirements:\n  join:\n    required: true\n"),
    )
    backend = CodexBackend(source_codex_home=source_home)
    manager = make_session_skill_manager()

    with _managed(
        manager,
        "profile-refusal",
        backend=backend,
        names=frozenset({"make-arch-diag"}),
    ) as managed:
        generated_skills = managed.generated_home / "skills"
        payload = managed.unavailability_payload
        persisted = json.loads(
            (Path(managed.skills_dir.path) / "skill-unavailability.json").read_text(
                encoding="utf-8"
            )
        )

        assert (generated_skills / "profile-admitted" / "SKILL.md").is_file()
        assert not (generated_skills / "profile-refused").exists()
        assert payload["backend"] == "codex"
        assert {record["skill"]: record["operation"] for record in payload["unavailable"]} == {
            "profile-refused": "required_join"
        }
        assert persisted["backend"] == payload["backend"]
        assert persisted["unavailable"] == list(payload["unavailable"])


def test_refused_profile_collision_leaves_the_admitted_ordinary_skill_discoverable(
    make_session_skill_manager,
    tmp_path: Path,
) -> None:
    from autoskillit.execution.backends.codex import CodexBackend

    source_home = _prepare_codex_profile_source(tmp_path)
    _write_profile_skill(
        source_home / "skills",
        "make-arch-diag",
        frontmatter=("semantic_version: 1\nsemantic_requirements:\n  join:\n    required: true\n"),
    )
    backend = CodexBackend(source_codex_home=source_home)
    manager = make_session_skill_manager()

    with _managed(
        manager,
        "refused-profile-collision",
        backend=backend,
        names=frozenset({"make-arch-diag"}),
    ) as managed:
        discovery = managed.generated_home / "skills" / "make-arch-diag"

        assert discovery.is_symlink()
        assert (discovery.resolve() / "SKILL.md").is_file()
        assert {
            record["skill"]: record["operation"]
            for record in managed.unavailability_payload["unavailable"]
        } == {"make-arch-diag": "required_join"}


def test_profile_only_managed_codex_session_has_a_valid_discovery_root(
    make_session_skill_manager,
    tmp_path: Path,
) -> None:
    from autoskillit.execution.backends.codex import CodexBackend

    source_home = _prepare_codex_profile_source(tmp_path)
    _write_profile_skill(source_home / "skills", "profile-only")
    backend = CodexBackend(source_codex_home=source_home)
    manager = make_session_skill_manager()

    with _managed(
        manager,
        "profile-only",
        backend=backend,
        names=frozenset(),
    ) as managed:
        assert list((Path(managed.skills_dir.path) / "skills").iterdir()) == []
        assert (managed.generated_home / "skills" / "profile-only" / "SKILL.md").is_file()


@pytest.mark.parametrize(
    ("ambient_state", "helper_available"),
    (("absent", False), ("invalid", False), ("valid", True)),
)
def test_manager_filters_child_spawn_skill_by_finalized_ambient_role(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    ambient_state: str,
    helper_available: bool,
) -> None:
    import json

    from autoskillit.core import SkillSource
    from autoskillit.execution.backends.codex import CodexBackend
    from autoskillit.workspace import (
        DefaultSessionSkillManager,
        EffectiveSkillCatalog,
        SkillCatalogEntry,
        SkillInfo,
        SkillsDirectoryProvider,
        compile_session_skill_catalog,
    )
    from autoskillit.workspace.skills import _skill_info_from_frontmatter

    fake_home = tmp_path / "home"
    source_home = fake_home / ".codex"
    source_home.mkdir(parents=True)
    (source_home / "auth.json").write_text("{}\n", encoding="utf-8")
    config = 'cli_auth_credentials_store = "keyring"\n'
    if ambient_state == "invalid":
        source_target = source_home / "agents" / "helper.toml"
        source_target.parent.mkdir()
        source_target.write_text('name = "helper"\n', encoding="utf-8")
        config += (
            "\n[agents.helper]\n"
            'description = "source-relative helper"\n'
            'config_file = "agents/helper.toml"\n'
        )
    elif ambient_state == "valid":
        valid_target = tmp_path / "ambient" / "helper.toml"
        valid_target.parent.mkdir()
        valid_target.write_text('name = "helper"\n', encoding="utf-8")
        config += (
            f'\n[agents.helper]\ndescription = "absolute helper"\nconfig_file = "{valid_target}"\n'
        )
    (source_home / "config.toml").write_text(config, encoding="utf-8")
    _write_profile_skill(
        source_home / "skills",
        "helper-skill",
        body="PROFILE_HELPER_SENTINEL\n",
    )
    _write_profile_skill(
        source_home / "skills",
        "profile-refused",
        frontmatter=("semantic_version: 1\nsemantic_requirements:\n  join:\n    required: true\n"),
    )

    project_root = tmp_path / "project"
    semantic_path = project_root / "skills" / "helper-skill" / "SKILL.md"
    semantic_path.parent.mkdir(parents=True)
    semantic_path.write_text(
        "---\n"
        "name: helper-skill\n"
        "description: Delegate to an ambient helper.\n"
        "semantic_version: 1\n"
        "semantic_requirements:\n"
        "  logical_roles:\n"
        "  - name: helper\n"
        "    purpose: perform delegated work\n"
        "  child_spawns:\n"
        "  - role: helper\n"
        "    count: 1\n"
        "---\n"
        "Delegate the work.\n",
        encoding="utf-8",
    )
    helper_skill = _skill_info_from_frontmatter(
        "helper-skill",
        SkillSource.PROJECT_LOCAL,
        semantic_path,
    )
    unrelated = SkillInfo(
        name="unrelated-skill",
        source=SkillSource.PROJECT_LOCAL,
        path=project_root / "skills" / "unrelated-skill" / "SKILL.md",
        canonical_content=(
            "---\n"
            "name: unrelated-skill\n"
            "description: Supported without child delegation.\n"
            "---\n"
            "Run directly.\n"
        ),
    )
    catalog = EffectiveSkillCatalog(
        skills=tuple(
            SkillCatalogEntry.from_skill_info(skill) for skill in (helper_skill, unrelated)
        ),
        execution_role=SkillExecutionRole.SESSION,
    )
    backend = CodexBackend(source_codex_home=source_home)
    provider = SkillsDirectoryProvider()
    context = provider.catalog_projection_context(
        catalog,
        project_root,
        backend=backend,
        durable_scripts_root=pkg_root(),
    )
    manager = DefaultSessionSkillManager(
        provider,
        ephemeral_root=tmp_path / "ephemeral",
        persistent_roots={"codex": tmp_path / "persistent" / "codex-sessions"},
    )
    session_id = f"ambient-{ambient_state}"
    error_events: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setattr(
        session_skills.logger,
        "error",
        lambda event, **kwargs: error_events.append((event, kwargs)),
    )
    expected_names = {"unrelated-skill"}
    if helper_available:
        expected_names.add("helper-skill")

    monkeypatch.setenv("MCP_CLIENT_BACKEND", "pre-test-backend")
    with manager.managed_session(
        session_id,
        compile_session_skill_catalog(catalog, backend),
        context,
    ) as managed:
        projected_root = Path(managed.skills_dir.path) / "skills"
        projected_names = {entry.name for entry in projected_root.iterdir()}
        metadata = json.loads(
            (Path(managed.skills_dir.path) / "skill-unavailability.json").read_text(
                encoding="utf-8"
            )
        )
        unavailable = metadata["unavailable"]
        unavailable_by_skill = {record["skill"]: record for record in unavailable}

        assert projected_names == expected_names
        assert set(manager._session_skill_infos[session_id]) == expected_names
        assert (managed.generated_home / "skills" / "unrelated-skill").is_symlink()
        assert unavailable == list(managed.unavailability_payload["unavailable"])
        assert unavailable_by_skill["profile-refused"]["operation"] == "required_join"
        assert not (managed.generated_home / "skills" / "profile-refused").exists()
        profile_helper = managed.generated_home / "skills" / "helper-skill"
        assert not profile_helper.is_symlink()
        assert "PROFILE_HELPER_SENTINEL" in (profile_helper / "SKILL.md").read_text(
            encoding="utf-8"
        )
        if helper_available:
            assert set(unavailable_by_skill) == {"profile-refused"}
            assert not any(
                event == "session_skill_native_role_unavailable" for event, _kwargs in error_events
            )
        else:
            assert unavailable_by_skill["helper-skill"] == {
                "backend": "codex",
                "diagnostic": "native child-spawn targets are unavailable: ['helper']",
                "operation": "child_spawn",
                "skill": "helper-skill",
            }
            assert set(unavailable_by_skill) == {"helper-skill", "profile-refused"}
            assert (
                "session_skill_native_role_unavailable",
                {
                    "backend": "codex",
                    "skills": ("helper-skill",),
                    "diagnostics": ("native child-spawn targets are unavailable: ['helper']",),
                    "count": 1,
                },
            ) in error_events

    assert session_id not in manager._session_skill_infos
    assert not (tmp_path / "persistent" / "codex-sessions" / session_id).exists()


def test_missing_declared_profile_skills_dir_returns_an_empty_compilation(tmp_path: Path) -> None:
    """Profile materialization treats an absent declared source as an empty catalog."""
    from autoskillit.execution.backends.codex import CodexBackend
    from autoskillit.workspace import (
        EffectiveSkillCatalog,
        SkillsDirectoryProvider,
        materialize_profile_skills,
    )

    source_home = tmp_path / "source-codex-home"
    source_home.mkdir()
    source_skills = source_home / "skills"
    backend = CodexBackend(source_codex_home=source_home)
    catalog = EffectiveSkillCatalog((), execution_role=SkillExecutionRole.SESSION)
    context = SkillsDirectoryProvider().catalog_projection_context(
        catalog,
        tmp_path,
        backend=backend,
        durable_scripts_root=pkg_root(),
    )

    compilation = materialize_profile_skills(
        tmp_path / "generated-home",
        source_skills,
        backend,
        context,
        finalized_native_roles=None,
    )

    assert compilation.catalog.skills == ()
    assert compilation.unavailable == ()


def test_managed_codex_home_uses_private_empty_inert_rollout_links(
    make_session_skill_manager, codex_env, tmp_path: Path
) -> None:
    codex_root = tmp_path / "persistent" / "codex-sessions"
    mgr = make_session_skill_manager(
        ephemeral_root=tmp_path / "ephemeral",
        codex_root=codex_root,
    )
    with _managed(
        mgr, "0123456789abcdef", backend=codex_env.backend, names=frozenset({"make-arch-diag"})
    ) as managed:
        assert isinstance(managed, ManagedSessionHome)
        assert managed.generated_home == codex_root / "0123456789abcdef"
        assert managed.skills_dir == ValidatedAddDir(path=str(managed.generated_home / "add-dir"))

        targets: list[Path] = []
        for public_name in ("sessions", "archived_sessions"):
            public_path = managed.generated_home / public_name
            assert public_path.is_symlink()
            target = public_path.resolve(strict=True)
            assert target.is_dir()
            assert target.is_relative_to(managed.generated_home)
            assert list(target.iterdir()) == []
            targets.append(target)

        assert targets[0] != targets[1]
        assert all(target != codex_root.resolve() for target in targets)

    assert not (codex_root / "0123456789abcdef").exists()
    assert "0123456789abcdef" not in mgr._session_roots
    assert "0123456789abcdef" not in mgr._session_leases
    assert "0123456789abcdef" not in mgr._session_skills_subdirs


def test_persistent_backend_declares_its_own_inert_paths(
    make_session_skill_manager,
    tmp_path: Path,
) -> None:
    from dataclasses import replace

    # backend.name stays "codex" (from _make_codex_backend()) because persistent
    # roots are now resolved per backend name (#4391) — the fixture's codex_root
    # kwarg only populates a "codex" entry in the manager's persistent_roots map.
    # The point under test is that inert-path declaration follows
    # capabilities.session_dir_symlinks generically, not a hardcoded convention.
    backend = _make_codex_backend()
    backend.capabilities = replace(
        _CODEX_CAPABILITIES,
        session_dir_symlinks=frozenset({"records"}),
    )
    persistent_root = tmp_path / "persistent" / "custom-sessions"
    mgr = make_session_skill_manager(codex_root=persistent_root)
    with _managed(
        mgr, "0123456789abcdef", backend=backend, names=frozenset({"make-arch-diag"})
    ) as managed:
        records = managed.generated_home / "records"
        assert records.is_symlink()
        assert records.resolve(strict=True).is_dir()
        assert not (managed.generated_home / "sessions").exists()
        assert not (managed.generated_home / "archived_sessions").exists()


@pytest.mark.parametrize("backend_kind", ["claude-code", "codex"])
def test_session_projection_is_agent_safe_for_each_backend(
    make_session_skill_manager,
    codex_env,
    backend_kind: str,
) -> None:
    from autoskillit.core.io import load_yaml

    backend = codex_env.backend if backend_kind == "codex" else None
    manager = make_session_skill_manager()
    session_path = _materialize(
        manager,
        f"projection-{backend_kind}",
        backend=backend,
        names=frozenset({"make-arch-diag"}),
    )
    skills_subdir = (
        ClaudeDirectoryConventions.PLUGIN_DIR_SKILLS_SUBDIR
        if backend is not None
        else ClaudeDirectoryConventions.ADD_DIR_SKILLS_SUBDIR
    )
    content = (session_path / skills_subdir / "make-arch-diag" / "SKILL.md").read_text()
    frontmatter = load_yaml(content.split("---\n", 2)[1])
    assert {
        "activate_deps",
        "uses_capabilities",
        "execution_role",
    }.isdisjoint(frontmatter)
    assert frontmatter["name"] == "make-arch-diag"
