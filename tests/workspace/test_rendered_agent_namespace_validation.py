"""The shared publication validator checks rendered agents against the plugin namespace."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from autoskillit.core import SkillExecutionRole, SkillSource, pkg_root
from autoskillit.workspace._projected_artifact._validation import (
    validate_rendered_agent_tool_namespace,
    validate_sanitized_plugin_artifact,
)

pytestmark = [pytest.mark.layer("workspace"), pytest.mark.small]

_PLUGIN_NAMESPACE = "mcp__plugin_autoskillit_autoskillit__"


def _write_probe(plugin_root: Path, tools_line: str, *, body: str = "Probe body.") -> Path:
    agents_dir = plugin_root / "agents"
    agents_dir.mkdir(parents=True, exist_ok=True)
    probe = agents_dir / "probe.md"
    probe.write_text(
        "---\n"
        "name: probe\n"
        'description: "Probe agent."\n'
        f"tools: {tools_line}\n"
        "model: sonnet\n"
        "maxTurns: 5\n"
        "---\n"
        f"\n{body}\n",
        encoding="utf-8",
    )
    return probe


def _write_manifests(plugin_root: Path, *, mcp_json: bool = True) -> None:
    (plugin_root / ".claude-plugin").mkdir(parents=True, exist_ok=True)
    (plugin_root / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"name": "autoskillit"}), encoding="utf-8"
    )
    if mcp_json:
        (plugin_root / ".mcp.json").write_text(
            json.dumps({"mcpServers": {"autoskillit": {}}}), encoding="utf-8"
        )


def test_direct_prefix_agent_is_reported_with_expected_namespace(tmp_path: Path) -> None:
    _write_manifests(tmp_path)
    _write_probe(tmp_path, "[mcp__autoskillit__inspect_session_logs]")

    errors = validate_rendered_agent_tool_namespace(tmp_path)

    assert len(errors) == 1
    assert "probe.md" in errors[0]
    assert _PLUGIN_NAMESPACE in errors[0]


def test_plugin_namespace_agent_is_valid(tmp_path: Path) -> None:
    _write_manifests(tmp_path)
    _write_probe(tmp_path, f"[{_PLUGIN_NAMESPACE}inspect_session_logs]")

    assert validate_rendered_agent_tool_namespace(tmp_path) == ()


def test_unregistered_short_name_under_plugin_namespace_is_reported(tmp_path: Path) -> None:
    _write_manifests(tmp_path)
    _write_probe(tmp_path, f"[{_PLUGIN_NAMESPACE}nonexistent_tool]")

    errors = validate_rendered_agent_tool_namespace(tmp_path)

    assert len(errors) == 1
    assert "nonexistent_tool" in errors[0]


def test_mcp_agent_without_mcp_json_fails_closed(tmp_path: Path) -> None:
    _write_manifests(tmp_path, mcp_json=False)
    _write_probe(tmp_path, f"[{_PLUGIN_NAMESPACE}inspect_session_logs]")

    errors = validate_rendered_agent_tool_namespace(tmp_path)

    assert len(errors) == 1
    assert ".mcp.json" in errors[0]


def test_agents_without_mcp_tools_need_no_mcp_json(tmp_path: Path) -> None:
    _write_probe(tmp_path, "[Read, Grep, Glob]")

    assert validate_rendered_agent_tool_namespace(tmp_path) == ()


def test_missing_agents_directory_yields_no_error(tmp_path: Path) -> None:
    assert validate_rendered_agent_tool_namespace(tmp_path) == ()


def test_malformed_frontmatter_is_an_error_not_an_exception(tmp_path: Path) -> None:
    _write_manifests(tmp_path)
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    (agents_dir / "broken.md").write_text(
        "---\nname: broken\ntools: [unterminated\n---\nBody.\n", encoding="utf-8"
    )

    errors = validate_rendered_agent_tool_namespace(tmp_path)

    assert len(errors) == 1
    assert "broken.md" in errors[0]


def test_agent_vanishing_between_listing_and_reading_is_an_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_manifests(tmp_path)
    probe = _write_probe(tmp_path, f"[{_PLUGIN_NAMESPACE}inspect_session_logs]")
    original_read_text = Path.read_text

    def vanish_on_read(path: Path, *args, **kwargs) -> str:
        if path == probe:
            raise FileNotFoundError("injected vanish")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", vanish_on_read)

    errors = validate_rendered_agent_tool_namespace(tmp_path)

    assert len(errors) == 1
    assert "probe.md" in errors[0]
    assert "injected vanish" in errors[0]


def test_invalid_plugin_json_is_an_error_not_an_exception(tmp_path: Path) -> None:
    _write_manifests(tmp_path)
    (tmp_path / ".claude-plugin" / "plugin.json").write_text("{not json", encoding="utf-8")
    _write_probe(tmp_path, f"[{_PLUGIN_NAMESPACE}inspect_session_logs]")

    errors = validate_rendered_agent_tool_namespace(tmp_path)

    assert len(errors) == 1
    assert "plugin.json" in errors[0]


def test_validate_sanitized_plugin_artifact_reports_agent_namespace_errors(
    tmp_path: Path,
) -> None:
    from autoskillit.workspace import (
        SkillProjectionContext,
        materialize_sanitized_plugin_root,
    )
    from autoskillit.workspace.skills import (
        DefaultSkillResolver,
        EffectiveSkillCatalog,
        SkillCatalogEntry,
    )

    source_infos = tuple(
        s for s in DefaultSkillResolver().list_all() if s.source is SkillSource.BUNDLED
    )
    catalog = EffectiveSkillCatalog(
        skills=tuple(SkillCatalogEntry.from_skill_info(s) for s in source_infos),
        execution_role=SkillExecutionRole.SESSION,
    )
    public_root = tmp_path / "plugins" / "autoskillit"
    public_root.parent.mkdir(parents=True)
    manifest_path = materialize_sanitized_plugin_root(
        pkg_root(),
        public_root,
        catalog,
        SkillProjectionContext(cwd=tmp_path, catalog=catalog),
    )
    assert (
        validate_sanitized_plugin_artifact(pkg_root(), public_root, manifest_path, source_infos)
        == ()
    )

    reader = public_root / "agents" / "session-log-reader.md"
    reader.write_text(
        reader.read_text(encoding="utf-8").replace(
            f"{_PLUGIN_NAMESPACE}inspect_session_logs", "mcp__autoskillit__inspect_session_logs"
        ),
        encoding="utf-8",
    )

    errors = validate_sanitized_plugin_artifact(
        pkg_root(), public_root, manifest_path, source_infos
    )

    assert any("session-log-reader.md" in error and _PLUGIN_NAMESPACE in error for error in errors)
