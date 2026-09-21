"""Direct coverage for ``_write_managed_parent_binding`` binding invariants."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


def _sample_attestation(parent_session_id: str = "parent-1"):
    from autoskillit.core import (
        MANAGED_JOIN_ATTESTATION_SCHEMA_VERSION,
        ManagedJoinAttestation,
    )

    return ManagedJoinAttestation(
        schema_version=MANAGED_JOIN_ATTESTATION_SCHEMA_VERSION,
        backend="codex",
        launch_context="interactive",
        parent_session_id=parent_session_id,
        activation_epoch=0,
        direct_tool_mode=True,
        resolved_model="gpt-5.6-luna",
        resolved_reasoning_effort="high",
        codex_catalog_digest="a" * 64,
        fixed_batch_tool_registry_digest="b" * 64,
        hook_registry_digest="c" * 64,
        skill_load_applies=True,
        guards_apply=True,
        provenance="autoskillit-server",
    )


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

    home = tmp_path / "home"
    _seed_projection(home, skill_name="my-skill")
    monkeypatch.setenv(CODEX_HOME_ENV_VAR, str(home))

    binding_path = resolve_binding_path(str(tmp_path), "parent-1")
    _write_managed_parent_binding(
        binding_path=binding_path,
        binding_session_id="parent-1",
        normalized_skill_name="my-skill",
        backend=_StubBackend(),
        attestation=_sample_attestation("parent-1"),
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
    from autoskillit.hooks._session_binding import (
        read_binding,
        resolve_binding_path,
    )
    from autoskillit.server._managed_join_attestation import (
        _write_managed_parent_binding,
    )

    home = tmp_path / "home"
    _seed_projection(home, skill_name="my-skill")
    monkeypatch.setenv(CODEX_HOME_ENV_VAR, str(home))

    binding_path = resolve_binding_path(str(tmp_path), "parent-1")
    _write_managed_parent_binding(
        binding_path=binding_path,
        binding_session_id="parent-1",
        normalized_skill_name="my-skill",
        backend=_StubBackend(),
        attestation=_sample_attestation("parent-1"),
    )
    _write_managed_parent_binding(
        binding_path=binding_path,
        binding_session_id="parent-1",
        normalized_skill_name="my-skill",
        backend=_StubBackend(),
        attestation=_sample_attestation("parent-1"),
    )

    binding = read_binding(binding_path)
    assert binding is not None
    assert binding.managed_route == "interactive-parent"
    assert binding.managed_parent_id == "parent-1"


def test_write_managed_parent_binding_rejects_backend_without_projection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from autoskillit.core import CODEX_HOME_ENV_VAR, SkillContractError
    from autoskillit.hooks._session_binding import resolve_binding_path
    from autoskillit.server._managed_join_attestation import (
        _write_managed_parent_binding,
    )

    monkeypatch.setenv(CODEX_HOME_ENV_VAR, str(tmp_path / "home"))
    binding_path = resolve_binding_path(str(tmp_path), "parent-1")

    with pytest.raises(SkillContractError, match="cannot locate its projection"):
        _write_managed_parent_binding(
            binding_path=binding_path,
            binding_session_id="parent-1",
            normalized_skill_name="my-skill",
            backend=_NoProjectionBackend(),
            attestation=_sample_attestation("parent-1"),
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

    monkeypatch.delenv(CODEX_HOME_ENV_VAR, raising=False)
    binding_path = resolve_binding_path(str(tmp_path), "parent-1")

    with pytest.raises(SkillContractError, match="CODEX_HOME"):
        _write_managed_parent_binding(
            binding_path=binding_path,
            binding_session_id="parent-1",
            normalized_skill_name="my-skill",
            backend=_StubBackend(),
            attestation=_sample_attestation("parent-1"),
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

    home = tmp_path / "home"
    home.mkdir()
    (home / ".autoskillit-projection.json").write_text(
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

    binding_path = resolve_binding_path(str(tmp_path), "parent-1")

    with pytest.raises(SkillContractError, match="not projected"):
        _write_managed_parent_binding(
            binding_path=binding_path,
            binding_session_id="parent-1",
            normalized_skill_name="missing-skill",
            backend=_StubBackend(),
            attestation=_sample_attestation("parent-1"),
        )
