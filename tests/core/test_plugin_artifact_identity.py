"""Tests for canonical installed-plugin artifact paths."""

from pathlib import Path

import pytest

from autoskillit.core import (
    generation_version_root,
    installed_plugin_artifact_lease_path,
    installed_plugin_artifact_manifest_path,
    installed_plugin_artifact_root,
    installed_plugin_cache_dir,
    resolve_current_generation,
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


def test_current_generation_resolves_direct_child(tmp_path: Path) -> None:
    version_root = generation_version_root(tmp_path, "autoskillit@autoskillit-local", "1.2.3")
    generation = version_root / "generation-a"
    generation.mkdir(parents=True)
    (version_root / "current").symlink_to(generation.name)

    assert (
        resolve_current_generation(tmp_path, "autoskillit@autoskillit-local", "1.2.3")
        == generation
    )


@pytest.mark.parametrize("absolute", [False, True])
def test_current_generation_rejects_store_escape(tmp_path: Path, *, absolute: bool) -> None:
    version_root = generation_version_root(tmp_path, "autoskillit@autoskillit-local", "1.2.3")
    version_root.mkdir(parents=True)
    escaped = tmp_path / "outside-generation"
    escaped.mkdir()
    target = escaped if absolute else Path("../../../../outside-generation")
    (version_root / "current").symlink_to(target)

    assert resolve_current_generation(tmp_path, "autoskillit@autoskillit-local", "1.2.3") is None
