import pytest

pytestmark = [pytest.mark.layer("core"), pytest.mark.small]


def test_logging_shard_all():
    from autoskillit.core.types._type_protocols_logging import __all__

    assert set(__all__) == {
        "AuditLog",
        "TokenLog",
        "TimingLog",
        "McpResponseLog",
        "GitHubApiLog",
        "SupportsDebug",
        "SupportsLogger",
    }


def test_execution_shard_all():
    from autoskillit.core.types._type_protocols_execution import __all__

    assert set(__all__) == {
        "CompletionRequiredResolver",
        "SkillProjectionPreparation",
        "InputContractResolver",
        "TestRunner",
        "HeadlessExecutor",
        "LaunchAdapter",
        "LaunchResolver",
        "OutputPatternResolver",
        "RunSkillCompletionAuthority",
        "SkillContractView",
        "SkillSessionContractStore",
        "WriteExpectedResolver",
    }


def test_skill_projection_preparation_runtime_protocol(tmp_path):
    from autoskillit.core.types._type_protocols_execution import (
        SkillProjectionPreparation,
    )

    class _Preparation:
        cwd = tmp_path
        project_root = tmp_path
        catalog = object()
        invocation = None
        default_base_branch = "main"

        def finalize(self, *, backend, binding):
            del backend, binding
            return object()

    assert isinstance(_Preparation(), SkillProjectionPreparation)


def test_headless_skill_dispatch_preparation_finalize_contract():
    import inspect
    from typing import get_type_hints

    from autoskillit.core import CodingAgentBackend, PluginLaunchBinding, SkillProjectionBinding
    from autoskillit.core.types._type_protocols_execution import (
        SkillProjectionPreparation,
    )

    signature = inspect.signature(SkillProjectionPreparation.finalize)
    assert signature.parameters["backend"].kind is inspect.Parameter.KEYWORD_ONLY
    assert signature.parameters["binding"].kind is inspect.Parameter.KEYWORD_ONLY
    assert get_type_hints(SkillProjectionPreparation.finalize) == {
        "backend": CodingAgentBackend,
        "binding": PluginLaunchBinding,
        "return": SkillProjectionBinding,
    }


def test_github_shard_all():
    from autoskillit.core.types._type_protocols_github import __all__

    assert set(__all__) == {
        "GitHubFetcher",
        "GitHubReviewPosterProtocol",
        "CIWatcher",
        "MergeQueueWatcher",
    }


def test_workspace_shard_all():
    from autoskillit.core.types._type_protocols_workspace import __all__

    assert set(__all__) == {
        "WorkspaceManager",
        "PluginArtifactAuthority",
        "PluginArtifactRetirementOwner",
        "PluginRetirementCoordinator",
        "CloneManager",
        "EffectiveSkillCatalogAuthority",
        "EffectiveSkillInvocationAuthority",
        "ResolvedSkillAuthority",
        "SessionSkillManager",
        "SkillAuthority",
        "SkillExclusionAuthority",
        "SkillFrontmatterAuthority",
        "SkillInvalidityAuthority",
        "SkillLister",
        "SkillProjectionContextAuthority",
        "SkillResolver",
    }


def test_session_skill_manager_managed_session_signature():
    import inspect
    import typing
    from contextlib import AbstractContextManager

    from autoskillit.core import (
        EffectiveSkillCatalogAuthority,
        ManagedSessionHome,
        SessionSkillManager,
        SkillProjectionContextAuthority,
    )

    signature = inspect.signature(SessionSkillManager.managed_session)
    assert tuple(signature.parameters) == (
        "self",
        "session_id",
        "catalog",
        "projection_context",
    )
    for name in ("session_id", "catalog", "projection_context"):
        assert signature.parameters[name].kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
        assert signature.parameters[name].default is inspect.Parameter.empty
    assert typing.get_type_hints(SessionSkillManager.managed_session) == {
        "session_id": str,
        "catalog": EffectiveSkillCatalogAuthority,
        "projection_context": SkillProjectionContextAuthority,
        "return": AbstractContextManager[ManagedSessionHome],
    }


def test_recipe_shard_all():
    from autoskillit.core.types._type_protocols_recipe import __all__

    assert set(__all__) == {
        "RecipeRepository",
        "MigrationService",
        "DatabaseReader",
        "ReadOnlyResolver",
        "SkillContractResolver",
        "ServeOverridesSnapshot",
    }


def test_infra_shard_all():
    from autoskillit.core.types._type_protocols_infra import __all__

    assert set(__all__) == {
        "GateState",
        "BackgroundSupervisor",
        "FleetLock",
        "KitchenTransitionLock",
        "QuotaRefreshTask",
        "TokenFactory",
        "CampaignProtector",
        "QuotaPolicy",
    }


def test_backend_shard_all():
    from autoskillit.core.types._type_protocols_backend import __all__

    assert set(__all__) == {
        "StreamParser",
        "ResultParser",
        "EnvPolicy",
        "ReadinessProbe",
        "SessionLocator",
        "ExplorationDispatchConventions",
        "ExplorationDispatchMaterialization",
        "ExplorationDispatchRenderer",
        "CodingAgentBackend",
    }


def test_all_protocols_reachable_via_types():
    from autoskillit.core import types

    for name in [
        "GateState",
        "AuditLog",
        "HeadlessExecutor",
        "SkillProjectionPreparation",
        "GitHubFetcher",
        "RecipeRepository",
        "WorkspaceManager",
        "EffectiveSkillCatalogAuthority",
        "EffectiveSkillInvocationAuthority",
        "ResolvedSkillAuthority",
        "SkillAuthority",
        "SkillFrontmatterAuthority",
        "SkillProjectionContextAuthority",
        "CampaignProtector",
        "CodingAgentBackend",
        "StreamParser",
        "SessionSkillManager",
    ]:
        assert hasattr(types, name), f"Missing from types: {name}"


def test_workspace_skill_authority_boundary_has_structural_types():
    from collections.abc import Mapping
    from typing import get_type_hints

    from autoskillit.core import (
        EffectiveSkillCatalogAuthority,
        EffectiveSkillInvocationAuthority,
        ExplorationVectorDef,
        ResolvedSkillAuthority,
        SessionSkillManager,
        SkillAuthority,
        SkillProjectionContextAuthority,
        SkillResolver,
    )

    materialize_hints = get_type_hints(SessionSkillManager.materialize_invocation)
    init_hints = get_type_hints(SessionSkillManager.init_session)
    assert materialize_hints["invocation"] is EffectiveSkillInvocationAuthority
    assert materialize_hints["projection_context"] is SkillProjectionContextAuthority
    assert init_hints["catalog"] is EffectiveSkillCatalogAuthority
    assert init_hints["projection_context"] is SkillProjectionContextAuthority

    resolve_hints = get_type_hints(SkillResolver.resolve)
    effective_hints = get_type_hints(SkillResolver.resolve_effective)
    list_hints = get_type_hints(SkillResolver.list_effective)
    invocation_hints = get_type_hints(SkillResolver.resolve_invocation)
    assert resolve_hints["return"] == ResolvedSkillAuthority | None
    assert effective_hints["return"] == ResolvedSkillAuthority | None
    assert list_hints["return"] is EffectiveSkillCatalogAuthority
    assert invocation_hints["return"] is EffectiveSkillInvocationAuthority

    vector_hints = get_type_hints(SkillAuthority.exploration_vectors.fget)
    projection_vector_hints = get_type_hints(
        SkillProjectionContextAuthority.exploration_vectors.fget
    )
    assert vector_hints["return"] == tuple[ExplorationVectorDef, ...]
    assert projection_vector_hints["return"] == Mapping[str, tuple[ExplorationVectorDef, ...]]


def test_plugin_artifact_authority_signature_and_runtime_protocol():
    import inspect
    from typing import get_type_hints

    from autoskillit.core import (
        CodingAgentBackend,
        PluginArtifactAuthority,
        PluginLaunchBinding,
        PluginLoadMode,
    )

    signature = inspect.signature(PluginArtifactAuthority.acquire_launch_binding)
    assert tuple(signature.parameters) == ("self", "backend", "load_mode")
    assert signature.parameters["backend"].kind is inspect.Parameter.KEYWORD_ONLY
    assert signature.parameters["load_mode"].kind is inspect.Parameter.KEYWORD_ONLY
    assert get_type_hints(PluginArtifactAuthority.acquire_launch_binding) == {
        "backend": CodingAgentBackend,
        "load_mode": PluginLoadMode,
        "return": PluginLaunchBinding,
    }

    class ConformingAuthority:
        def acquire_launch_binding(self, *, backend, load_mode):
            raise NotImplementedError

    assert isinstance(ConformingAuthority(), PluginArtifactAuthority)


def test_pyi_stub_exports_skill_constants():
    import autoskillit.core as core

    assert hasattr(core, "SKILL_FILE_ADVISORY_MAP"), (
        "SKILL_FILE_ADVISORY_MAP must be exported from autoskillit.core"
    )
    assert hasattr(core, "SKILL_ACTIVATE_DEPS_REQUIRED"), (
        "SKILL_ACTIVATE_DEPS_REQUIRED must be exported from autoskillit.core"
    )


def test_runtime_checkable_flags():
    from autoskillit.core.types._type_protocols_logging import SupportsDebug, SupportsLogger

    for proto in (SupportsDebug, SupportsLogger):
        assert not getattr(proto, "_is_runtime_protocol", False), (
            f"{proto.__name__} must not be @runtime_checkable"
        )
