"""Tests for canonical installed-plugin artifact paths."""

from pathlib import Path

import pytest

from autoskillit.core import (
    installed_plugin_artifact_lease_path,
    installed_plugin_artifact_manifest_path,
    installed_plugin_artifact_root,
    installed_plugin_cache_dir,
)

pytestmark = [pytest.mark.layer("core"), pytest.mark.small]


def test_installed_plugin_artifact_paths_have_one_canonical_shape(tmp_path: Path) -> None:
    cache_dir = installed_plugin_cache_dir(
        tmp_path,
        "autoskillit@autoskillit-local",
    )
    managed_root = installed_plugin_artifact_root(
        tmp_path,
        "autoskillit@autoskillit-local",
        "1.2.3",
    )
    manifest_path = installed_plugin_artifact_manifest_path(managed_root)

    assert cache_dir == (
        tmp_path / ".claude" / "plugins" / "cache" / "autoskillit-local" / "autoskillit"
    )
    assert managed_root == cache_dir / "1.2.3"
    assert manifest_path == cache_dir / ".1.2.3.autoskillit-artifact.json"
    assert installed_plugin_artifact_lease_path(managed_root) == Path(f"{manifest_path}.lock")
