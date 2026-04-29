import pytest

pytestmark = [pytest.mark.layer("core"), pytest.mark.small]


def test_logging_shard_all():
    from autoskillit.core._type_protocols_logging import __all__

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
    from autoskillit.core._type_protocols_execution import __all__

    assert set(__all__) == {
        "TestRunner",
        "HeadlessExecutor",
        "OutputPatternResolver",
        "WriteExpectedResolver",
    }


def test_github_shard_all():
    from autoskillit.core._type_protocols_github import __all__

    assert set(__all__) == {"GitHubFetcher", "CIWatcher", "MergeQueueWatcher"}


def test_workspace_shard_all():
    from autoskillit.core._type_protocols_workspace import __all__

    assert set(__all__) == {
        "WorkspaceManager",
        "CloneManager",
        "SessionSkillManager",
        "SkillLister",
        "SkillResolver",
    }


def test_recipe_shard_all():
    from autoskillit.core._type_protocols_recipe import __all__

    assert set(__all__) == {
        "RecipeRepository",
        "MigrationService",
        "DatabaseReader",
        "ReadOnlyResolver",
    }


def test_infra_shard_all():
    from autoskillit.core._type_protocols_infra import __all__

    assert set(__all__) == {
        "GateState",
        "BackgroundSupervisor",
        "FleetLock",
        "QuotaRefreshTask",
        "TokenFactory",
        "CampaignProtector",
    }


def test_all_protocols_reachable_via_types():
    from autoskillit.core import types

    for name in [
        "GateState",
        "AuditLog",
        "HeadlessExecutor",
        "GitHubFetcher",
        "RecipeRepository",
        "WorkspaceManager",
        "CampaignProtector",
    ]:
        assert hasattr(types, name), f"Missing from types: {name}"


def test_pyi_stub_exports_skill_constants():
    import autoskillit.core as core

    assert hasattr(core, "SKILL_FILE_ADVISORY_MAP"), (
        "SKILL_FILE_ADVISORY_MAP must be exported from autoskillit.core"
    )
    assert hasattr(core, "SKILL_ACTIVATE_DEPS_REQUIRED"), (
        "SKILL_ACTIVATE_DEPS_REQUIRED must be exported from autoskillit.core"
    )


def test_runtime_checkable_flags():
    from autoskillit.core._type_protocols_logging import SupportsDebug, SupportsLogger
    from autoskillit.core._type_protocols_infra import CampaignProtector

    for proto in (SupportsDebug, SupportsLogger, CampaignProtector):
        assert not getattr(proto, "_is_runtime_protocol", False), (
            f"{proto.__name__} must not be @runtime_checkable"
        )
