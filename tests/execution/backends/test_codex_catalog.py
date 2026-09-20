"""Regression tests for shared Codex model-catalog projection."""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

import pytest

from autoskillit.execution.backends._codex_catalog import project_codex_catalog
from tests.execution.backends._codex_fixtures import installed_catalog

pytestmark = [pytest.mark.layer("execution"), pytest.mark.medium]

_READER_MODEL = "gpt-5.6-luna"
_READER_REASONING_EFFORT = "xhigh"


def _installed_catalog() -> dict[str, object]:
    return installed_catalog()


def _catalog_bytes(catalog: object) -> bytes:
    return json.dumps(catalog).encode()


def test_managed_discovery_route_catalog_relpath_matches_codex_session_layout() -> None:
    from autoskillit.core import SESSION_ADD_DIR_SUBDIR
    from autoskillit.execution.backends.codex import CODEX_MANAGED_HOME_ROUTE, CodexBackend

    assert Path(CODEX_MANAGED_HOME_ROUTE.catalog_relpath) == (
        Path(SESSION_ADD_DIR_SUBDIR) / CodexBackend().conventions.skills_subdir
    )


def test_reader_projection_preserves_the_complete_installed_catalog() -> None:
    installed = _installed_catalog()

    projection = project_codex_catalog(
        _catalog_bytes(installed),
        expected_model=_READER_MODEL,
        expected_reasoning_effort=_READER_REASONING_EFFORT,
    )
    projected = json.loads(projection.canonical_projected_bytes)

    expected = json.loads(_catalog_bytes(installed))
    expected_reader = expected["models"][1]
    expected_reader["tool_mode"] = "direct"
    expected_reader["apply_patch_tool_type"] = None
    assert projected == expected
    assert projected["models"][0] == installed["models"][0]
    assert projected["models"][1]["tool_mode"] == "direct"
    assert projected["models"][1]["apply_patch_tool_type"] is None
    assert projection.bundled_sha256.startswith("sha256:")
    assert projection.projected_sha256.startswith("sha256:")
    assert projection.bundled_sha256 != projection.projected_sha256


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        (b'{"models":', "malformed"),
        (b'{"models":[],"models":[]}', "malformed"),
        (b'{"models":[],"revision":NaN}', "malformed"),
        (_catalog_bytes({"models": None}), "no model list"),
        (_catalog_bytes({"models": [{"slug": 1}]}), "malformed model entry"),
    ],
)
def test_catalog_schema_fails_closed(raw: bytes, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        project_codex_catalog(
            raw,
            expected_model=_READER_MODEL,
            expected_reasoning_effort=_READER_REASONING_EFFORT,
        )


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("missing_model", "exactly one"),
        ("duplicate_model", "exactly one"),
        ("malformed_reasoning", "malformed supported reasoning"),
        ("missing_effort", "does not advertise"),
        ("direct_tool_mode", "bundled tool_mode"),
        ("null_apply_patch", "bundled apply_patch_tool_type"),
    ],
)
def test_reader_projection_rejects_incomplete_or_preprojected_surfaces(
    mutation: str,
    message: str,
) -> None:
    catalog = _installed_catalog()
    models = catalog["models"]
    assert isinstance(models, list)
    reader = models[1]
    assert isinstance(reader, dict)
    if mutation == "missing_model":
        models.pop()
    elif mutation == "duplicate_model":
        models.append(dict(reader))
    elif mutation == "malformed_reasoning":
        reader["supported_reasoning_levels"] = [{"effort": 1}]
    elif mutation == "missing_effort":
        reader["supported_reasoning_levels"] = [{"effort": "high"}]
    elif mutation == "direct_tool_mode":
        reader["tool_mode"] = "direct"
    else:
        reader["apply_patch_tool_type"] = None

    with pytest.raises(ValueError, match=message):
        project_codex_catalog(
            _catalog_bytes(catalog),
            expected_model=_READER_MODEL,
            expected_reasoning_effort=_READER_REASONING_EFFORT,
        )


def test_codex_managed_join_adaptation_requires_context_without_native_capability() -> None:
    from autoskillit.core import (
        JoinSpec,
        SkillSemanticPlan,
    )
    from autoskillit.execution.backends import CodexBackend
    from autoskillit.server._managed_join_attestation import DefaultManagedJoinAttestationAuthority

    plan = SkillSemanticPlan(schema_version=1, join=JoinSpec(required=True))
    context = DefaultManagedJoinAttestationAuthority().issue(
        backend="codex",
        launch_context="direct",
        parent_session_id="parent-1",
        direct_tool_mode=True,
        resolved_model="gpt-5.6-sol",
        resolved_reasoning_effort="high",
        codex_catalog_digest="c" * 64,
        fixed_batch_tool_registry_digest="a" * 64,
        hook_registry_digest="b" * 64,
        skill_load_applies=True,
        guards_apply=True,
    )
    backend = CodexBackend()

    assert backend.capabilities.fixed_set_join_capable is False
    assert backend.adapt_skill_semantics(plan).unsupported_operation is not None
    adaptation = backend.adapt_skill_semantics(plan, context)
    assert adaptation.unsupported_operation is None
    assert adaptation.adaptation_context_digest == context.digest
    assert adaptation.instruction_fragments[-1].startswith("Use the server-owned managed")


@pytest.mark.parametrize("route", ["parent", "interactive-parent"])
def test_managed_parent_home_projects_catalog_tools_and_stop_hook(tmp_path, route) -> None:
    from autoskillit.execution.backends import CodexBackend
    from autoskillit.server._managed_join_attestation import DefaultManagedJoinAttestationAuthority

    source_home = tmp_path / "source"
    session_home = tmp_path / "session"
    source_home.mkdir()
    session_home.mkdir()
    raw_catalog = _catalog_bytes(_installed_catalog())
    (source_home / "models_cache.json").write_bytes(raw_catalog)
    (session_home / "config.toml").write_text(
        '[mcp_servers.autoskillit]\ncommand = "autoskillit"\n',
        encoding="utf-8",
    )
    projection = project_codex_catalog(
        raw_catalog,
        expected_model=_READER_MODEL,
        expected_reasoning_effort=_READER_REASONING_EFFORT,
    )
    context = DefaultManagedJoinAttestationAuthority().issue(
        backend="codex",
        launch_context="interactive" if route == "interactive-parent" else "direct",
        parent_session_id="parent-1",
        direct_tool_mode=True,
        resolved_model=_READER_MODEL,
        resolved_reasoning_effort=_READER_REASONING_EFFORT,
        codex_catalog_digest=projection.projected_sha256.removeprefix("sha256:"),
        fixed_batch_tool_registry_digest="a" * 64,
        hook_registry_digest="b" * 64,
        skill_load_applies=True,
        guards_apply=True,
    )
    attestation = context.managed_join_attestation
    assert attestation is not None

    CodexBackend(source_codex_home=source_home).configure_managed_session_dir(
        session_home,
        attestation=attestation,
        route=route,
    )

    config = tomllib.loads((session_home / "config.toml").read_text(encoding="utf-8"))
    tools = config["mcp_servers"]["autoskillit"].get("enabled_tools")
    if route == "interactive-parent":
        assert tools is None
        rendered = json.dumps(config["hooks"])
        assert "join_stop_guard.py" in rendered
        assert "join_followup_guard.py" in rendered
        assert "skill_orchestration_guard.py" not in rendered
    else:
        assert tools == ["run_fixed_batch", "read_fixed_batch_result"]
    assert "Stop" in config["hooks"]
    projected_model = json.loads((session_home / "models_cache.json").read_bytes())["models"][1]
    assert projected_model["tool_mode"] == "direct"
    assert projected_model["apply_patch_tool_type"] is None
