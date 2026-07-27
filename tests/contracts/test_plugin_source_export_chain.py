"""The `.pyi` stub is the runtime source of truth for `autoskillit.core.__all__`.

`core/__init__.py` is one line: `lazy.attach_stub(__name__, __file__)`. So the
stub is not merely a type-checker artifact — deleting a symbol from its leaf
module without removing the stub line leaves it in `__all__` and breaks
`getattr`, and *adding* one without a stub line leaves it absent from `__all__`
and un-importable from the gateway. That second failure is silent, and easy to
mistake for a working export.
"""

from __future__ import annotations

import pytest

import autoskillit.core as core

pytestmark = [pytest.mark.layer("contracts"), pytest.mark.small]

#: Symbols this change removed. Both halves must hold: gone from `__all__` and
#: gone from `getattr`.
REMOVED = (
    "MarketplaceInstall",
    "PluginSource",
    "ProjectedPluginRoot",
    "_get_autoskillit_install_path",
    "_retire_old_versions",
    "append_retiring_entry",
    "drop_retiring_entries",
    "retiring_cache_entries",
    "sweep_retiring_cache",
    "MAX_DEFER_HOURS",
)

#: Symbols this change added. Each must be importable from the gateway.
ADDED = (
    "ArtifactLease",
    "ArtifactLeaseContention",
    "PluginArtifactAuthority",
    "PluginArtifactContentionError",
    "PluginArtifactKind",
    "PluginArtifactIdentity",
    "PluginArtifactPublicationError",
    "PluginArtifactRetirementEngine",
    "PluginArtifactRetirementOwner",
    "PluginArtifactValidationError",
    "PluginLaunchBinding",
    "PluginLoadMode",
    "PluginRetirementCoordinator",
    "RETIRED_INSTALL_ARTIFACT_SHAPES",
    "RetiringAppendResult",
    "RetiringArtifactRecord",
    "RetiringCacheReadResult",
    "RetiringCacheState",
    "RetirementOutcome",
    "RetiredArtifactShape",
    "append_retiring_record",
    "destination_location",
    "directory_tree_digest",
    "due_retiring_records",
    "is_canonical_plugin_artifact_digest",
    "is_canonical_plugin_artifact_incarnation_id",
    "migrate_retiring_cache_v1",
    "new_plugin_artifact_incarnation_id",
    "read_retiring_cache",
    "registered_install_paths",
    "remove_retiring_records",
    "resolve_project_dir",
)


@pytest.mark.parametrize("name", REMOVED)
def test_removed_symbols_are_gone_from_both_halves(name: str) -> None:
    assert name not in core.__all__, f"{name} is still in core.__all__ — stale .pyi line"
    assert not hasattr(core, name), f"{name} is still gateway-importable"


@pytest.mark.parametrize("name", ADDED)
def test_added_symbols_are_gateway_importable(name: str) -> None:
    assert hasattr(core, name), (
        f"{name} is not importable from autoskillit.core — add its line to "
        "core/__init__.pyi, which is what populates __all__ at runtime"
    )


@pytest.mark.parametrize("name", [n for n in ADDED if not n.startswith("_")])
def test_public_added_symbols_are_in_all(name: str) -> None:
    assert name in core.__all__, f"{name} is importable but missing from core.__all__"


def test_workspace_gateway_exports_the_install_state_authority() -> None:
    """workspace/ has no stub; its `__all__` is explicit and must list these."""
    import autoskillit.workspace as workspace

    for name in (
        "verify_install_state",
        "reconcile_install_artifacts",
        "InstallStateFinding",
        "ProjectionCacheKey",
        "PROJECTION_CACHE_KEY_EXCLUSIONS",
        "public_plugin_asset_digest",
        "iter_public_plugin_asset_files",
        "prune_stale_projections",
    ):
        assert hasattr(workspace, name), f"workspace does not export {name}"
        assert name in workspace.__all__, f"{name} missing from workspace.__all__"
