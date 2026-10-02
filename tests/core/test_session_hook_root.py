"""SessionHookRoot is derived only from a live, leased plugin launch binding."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from autoskillit.core import (
    PluginArtifactIdentity,
    PluginLaunchBinding,
    PluginLoadMode,
    SessionHookRoot,
    plugin_launch_binding_scope,
)
from autoskillit.execution.backends import CodexBackend
from tests.contracts._projection_helpers import projected_plugin_authority
from tests.fakes import _FakePluginLease

pytestmark = [pytest.mark.layer("core"), pytest.mark.medium]


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    fake_home = tmp_path.resolve() / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: fake_home)
    return fake_home


def test_root_is_the_leased_projection(home: Path, tmp_path: Path) -> None:
    with plugin_launch_binding_scope(
        authority=projected_plugin_authority(tmp_path),
        backend=CodexBackend(),
        load_mode=PluginLoadMode.PROJECTED_HOME,
    ) as binding:
        assert binding is not None
        root = SessionHookRoot.from_binding(binding)

        assert root.artifact_path == binding.identity.managed_path
        assert root.plugin_dir == Path(os.path.realpath(binding.identity.managed_path))
        assert root.hooks_dir == root.plugin_dir / "hooks"
        assert root.semantic_key == binding.identity.semantic_key
        assert (root.hooks_dir / "_dispatch.py").is_file()


def test_root_canonicalizes_an_artifact_path_reached_through_a_symlink(tmp_path: Path) -> None:
    real_root = tmp_path / "real" / ".autoskillit" / "plugin-projections" / "artifact"
    real_root.mkdir(parents=True)
    (tmp_path / "linked").symlink_to(tmp_path / "real", target_is_directory=True)
    linked_root = tmp_path / "linked" / ".autoskillit" / "plugin-projections" / "artifact"
    binding = PluginLaunchBinding(
        load_mode=PluginLoadMode.PROJECTED_HOME,
        plugin_dir=linked_root,
        identity=PluginArtifactIdentity(
            semantic_key="test-plugin-artifact",
            incarnation_id="00000000000040008000000000000001",
            manifest_schema_version=1,
            artifact_digest="a" * 64,
            managed_path=linked_root,
            manifest_path=linked_root.parent / ".artifact.json",
        ),
        inherited_fds=(),
        _lease=_FakePluginLease(),
    )

    root = SessionHookRoot.from_binding(binding)

    assert root.artifact_path == linked_root
    assert root.plugin_dir == Path(os.path.realpath(real_root))
    assert os.path.realpath(root.hooks_dir) == str(root.hooks_dir)


def test_closed_binding_is_rejected(home: Path, tmp_path: Path) -> None:
    binding = projected_plugin_authority(tmp_path).acquire_launch_binding(
        backend=CodexBackend(),
        load_mode=PluginLoadMode.PROJECTED_HOME,
    )
    binding.close()

    with pytest.raises(ValueError, match="open plugin launch binding"):
        SessionHookRoot.from_binding(binding)
