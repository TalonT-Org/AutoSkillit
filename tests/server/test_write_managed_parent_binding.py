"""Direct coverage for ``_write_managed_parent_binding`` binding invariants."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.server._managed_join_fixtures import isolated_state_dir, sample_attestation_only

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


class _StubBackend:
    """Minimal ``CodingAgentBackend`` stub that projects a managed catalog."""

    def __init__(self, manifest_name: str = ".autoskillit-projection.json") -> None:
        self.name = "codex-stub"
        self._manifest_name = manifest_name

    def projected_manifest_path(self, generated_home: Path) -> Path:
        catalog_dir = generated_home / "catalog"
        catalog_dir.mkdir(parents=True, exist_ok=True)
        return catalog_dir.parent / f".{catalog_dir.name}.autoskillit-projection.json"


class _NoProjectionBackend:
    """Backend that exposes no ``projected_manifest_path`` callable."""

    name = "codex-no-projection"


def _seed_projection(home: Path, skill_name: str) -> None:
    """Write a single managed-skill projection manifest for one generated home."""
    home.mkdir(parents=True, exist_ok=True)
    catalog_dir = home / "catalog"
    catalog_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = catalog_dir.parent / f".{catalog_dir.name}.autoskillit-projection.json"
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "artifact_digest": "d" * 64,
                "incarnation_id": "test-incarnation",
                "skills": {
                    skill_name: {
                        "join_required": True,
                        "source_artifact_digest": "d" * 64,
                    }
                },
            }
        ),
        encoding="utf-8",
    )


def test_write_managed_parent_binding_creates_binding_with_route_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from autoskillit.core import CODEX_HOME_ENV_VAR
    from autoskillit.execution.backends._codex_hooks import managed_codex_guard_set
    from autoskillit.hooks._session_binding import (
        read_binding,
        resolve_binding_path,
    )
    from autoskillit.server._managed_join_attestation import (
        _write_managed_parent_binding,
    )

    project_root = isolated_state_dir(tmp_path)
    home = project_root / "home"
    _seed_projection(home, skill_name="my-skill")
    monkeypatch.setenv(CODEX_HOME_ENV_VAR, str(home))

    binding_path = resolve_binding_path(str(project_root), "parent-1")
    _write_managed_parent_binding(
        binding_path=binding_path,
        binding_session_id="parent-1",
        normalized_skill_name="my-skill",
        backend=_StubBackend(),
        attestation=sample_attestation_only("parent-1"),
    )

    binding = read_binding(binding_path)
    assert binding is not None
    assert binding.session_id == "parent-1"
    assert binding.managed_parent_id == "parent-1"
    assert binding.managed_leaf_id == ""
    assert binding.managed_route == "interactive-parent"
    assert binding.managed_guard_set == tuple(
        sorted(managed_codex_guard_set("interactive-parent"))
    )
    assert binding.managed_config_digest == "c" * 64
    assert binding.artifact_digest == "d" * 64


def test_write_managed_parent_binding_merges_into_existing_binding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from autoskillit.core import CODEX_HOME_ENV_VAR
    from autoskillit.execution.backends._codex_hooks import managed_codex_guard_set
    from autoskillit.hooks._session_binding import (
        read_binding,
        resolve_binding_path,
    )
    from autoskillit.server._managed_join_attestation import (
        _write_managed_parent_binding,
    )

    project_root = isolated_state_dir(tmp_path)
    home = project_root / "home"
    _seed_projection(home, skill_name="my-skill")
    monkeypatch.setenv(CODEX_HOME_ENV_VAR, str(home))

    binding_path = resolve_binding_path(str(project_root), "parent-1")
    _write_managed_parent_binding(
        binding_path=binding_path,
        binding_session_id="parent-1",
        normalized_skill_name="my-skill",
        backend=_StubBackend(),
        attestation=sample_attestation_only("parent-1"),
    )
    _write_managed_parent_binding(
        binding_path=binding_path,
        binding_session_id="parent-1",
        normalized_skill_name="my-skill",
        backend=_StubBackend(),
        attestation=sample_attestation_only("parent-1"),
    )

    binding = read_binding(binding_path)
    assert binding is not None
    assert binding.managed_route == "interactive-parent"
    assert binding.managed_parent_id == "parent-1"
    assert binding.managed_leaf_id == ""
    assert binding.managed_guard_set == tuple(
        sorted(managed_codex_guard_set("interactive-parent"))
    )
    assert binding.managed_config_digest == "c" * 64


def test_write_managed_parent_binding_rejects_route_mismatch_on_merge(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A pre-existing binding with a mismatched managed_route must surface SkillContractError."""
    from autoskillit.core import CODEX_HOME_ENV_VAR, SkillContractError
    from autoskillit.execution.backends._codex_hooks import managed_codex_guard_set
    from autoskillit.hooks._session_binding import (
        SESSION_BINDING_SCHEMA_VERSION,
        SessionBinding,
        resolve_binding_path,
        write_binding,
    )
    from autoskillit.server._managed_join_attestation import (
        _write_managed_parent_binding,
    )

    project_root = isolated_state_dir(tmp_path)
    home = project_root / "home"
    _seed_projection(home, skill_name="my-skill")
    monkeypatch.setenv(CODEX_HOME_ENV_VAR, str(home))

    binding_path = resolve_binding_path(str(project_root), "parent-1")
    # Seed a binding with a *different* managed_route than the attestation would produce.
    write_binding(
        binding_path,
        SessionBinding(
            schema_version=SESSION_BINDING_SCHEMA_VERSION,
            session_id="parent-1",
            join_required=True,
            binding_valid=True,
            artifact_digest="d" * 64,
            loaded_skills=(),
            managed_parent_id="parent-1",
            managed_leaf_id="",
            managed_route="leaf",  # deliberately mismatched
            managed_guard_set=tuple(sorted(managed_codex_guard_set("leaf"))),
            managed_config_digest="c" * 64,
        ),
    )

    with pytest.raises(SkillContractError, match="does not match the managed parent route"):
        _write_managed_parent_binding(
            binding_path=binding_path,
            binding_session_id="parent-1",
            normalized_skill_name="my-skill",
            backend=_StubBackend(),
            attestation=sample_attestation_only("parent-1"),
        )


def test_write_managed_parent_binding_rejects_backend_without_projection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from autoskillit.core import CODEX_HOME_ENV_VAR, SkillContractError
    from autoskillit.hooks._session_binding import resolve_binding_path
    from autoskillit.server._managed_join_attestation import (
        _write_managed_parent_binding,
    )

    project_root = isolated_state_dir(tmp_path)
    monkeypatch.setenv(CODEX_HOME_ENV_VAR, str(project_root / "home"))
    binding_path = resolve_binding_path(str(project_root), "parent-1")

    with pytest.raises(SkillContractError, match="cannot locate its projection"):
        _write_managed_parent_binding(
            binding_path=binding_path,
            binding_session_id="parent-1",
            normalized_skill_name="my-skill",
            backend=_NoProjectionBackend(),
            attestation=sample_attestation_only("parent-1"),
        )


def test_write_managed_parent_binding_requires_codex_home(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from autoskillit.core import CODEX_HOME_ENV_VAR, SkillContractError
    from autoskillit.hooks._session_binding import resolve_binding_path
    from autoskillit.server._managed_join_attestation import (
        _write_managed_parent_binding,
    )

    project_root = isolated_state_dir(tmp_path)
    monkeypatch.delenv(CODEX_HOME_ENV_VAR, raising=False)
    binding_path = resolve_binding_path(str(project_root), "parent-1")

    with pytest.raises(SkillContractError, match="CODEX_HOME"):
        _write_managed_parent_binding(
            binding_path=binding_path,
            binding_session_id="parent-1",
            normalized_skill_name="my-skill",
            backend=_StubBackend(),
            attestation=sample_attestation_only("parent-1"),
        )


def test_write_managed_parent_binding_rejects_skill_not_projected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from autoskillit.core import CODEX_HOME_ENV_VAR, SkillContractError
    from autoskillit.hooks._session_binding import resolve_binding_path
    from autoskillit.server._managed_join_attestation import (
        _write_managed_parent_binding,
    )

    project_root = isolated_state_dir(tmp_path)
    home = project_root / "home"
    home.mkdir()
    catalog_dir = home / "catalog"
    catalog_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = catalog_dir.parent / f".{catalog_dir.name}.autoskillit-projection.json"
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "artifact_digest": "d" * 64,
                "incarnation_id": "test-incarnation",
                "skills": {},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv(CODEX_HOME_ENV_VAR, str(home))

    binding_path = resolve_binding_path(str(project_root), "parent-1")

    with pytest.raises(SkillContractError, match="not projected"):
        _write_managed_parent_binding(
            binding_path=binding_path,
            binding_session_id="parent-1",
            normalized_skill_name="missing-skill",
            backend=_StubBackend(),
            attestation=sample_attestation_only("parent-1"),
        )
