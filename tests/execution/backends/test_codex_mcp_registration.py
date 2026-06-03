from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from autoskillit.execution.backends._codex_config import (
    CODEX_MCP_STARTUP_TIMEOUT_SEC,
    CODEX_MCP_TOOL_TIMEOUT_FLOOR,
    ensure_codex_mcp_registered,
)

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]


@pytest.fixture()
def fake_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr("pathlib.Path.home", lambda: home)
    return home


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


class TestEnsureCodexMcpRegisteredCreate:
    def test_config_toml_does_not_exist_before_call(self, fake_home: Path) -> None:
        assert not (fake_home / ".codex" / "config.toml").exists()

    def test_config_toml_exists_after_call(self, fake_home: Path) -> None:
        ensure_codex_mcp_registered()
        assert (fake_home / ".codex" / "config.toml").exists()

    def test_mcp_servers_autoskillit_section_present(self, fake_home: Path) -> None:
        ensure_codex_mcp_registered()
        data = tomllib.loads((fake_home / ".codex" / "config.toml").read_text())
        assert "autoskillit" in data.get("mcp_servers", {})

    def test_command_is_autoskillit(self, fake_home: Path) -> None:
        ensure_codex_mcp_registered()
        data = tomllib.loads((fake_home / ".codex" / "config.toml").read_text())
        section = data["mcp_servers"]["autoskillit"]
        assert section["command"] == "autoskillit"

    def test_env_vars_matches_canonical_constant(self, fake_home: Path) -> None:
        from autoskillit.core import CODEX_MCP_ENV_FORWARD_VARS

        ensure_codex_mcp_registered()
        data = tomllib.loads((fake_home / ".codex" / "config.toml").read_text())
        env_vars = data["mcp_servers"]["autoskillit"]["env_vars"]
        assert set(env_vars) == set(CODEX_MCP_ENV_FORWARD_VARS)

    def test_startup_timeout(self, fake_home: Path) -> None:
        ensure_codex_mcp_registered()
        data = tomllib.loads((fake_home / ".codex" / "config.toml").read_text())
        section = data["mcp_servers"]["autoskillit"]
        assert section["startup_timeout_sec"] == CODEX_MCP_STARTUP_TIMEOUT_SEC

    def test_tool_timeout(self, fake_home: Path) -> None:
        ensure_codex_mcp_registered()
        data = tomllib.loads((fake_home / ".codex" / "config.toml").read_text())
        section = data["mcp_servers"]["autoskillit"]
        assert section["tool_timeout_sec"] == CODEX_MCP_TOOL_TIMEOUT_FLOOR

    def test_no_type_key(self, fake_home: Path) -> None:
        ensure_codex_mcp_registered()
        data = tomllib.loads((fake_home / ".codex" / "config.toml").read_text())
        section = data["mcp_servers"]["autoskillit"]
        assert "type" not in section


# ---------------------------------------------------------------------------
# Idempotent
# ---------------------------------------------------------------------------


class TestEnsureCodexMcpRegisteredIdempotent:
    def test_second_call_returns_false(self, fake_home: Path) -> None:
        first = ensure_codex_mcp_registered()
        second = ensure_codex_mcp_registered()
        assert first is True
        assert second is False

    def test_exactly_one_section(self, fake_home: Path) -> None:
        ensure_codex_mcp_registered()
        ensure_codex_mcp_registered()
        raw = (fake_home / ".codex" / "config.toml").read_text()
        assert raw.count("[mcp_servers.autoskillit]") == 1

    def test_toml_parseable_after_double_call(self, fake_home: Path) -> None:
        ensure_codex_mcp_registered()
        ensure_codex_mcp_registered()
        raw = (fake_home / ".codex" / "config.toml").read_text()
        tomllib.loads(raw)


# ---------------------------------------------------------------------------
# Preservation
# ---------------------------------------------------------------------------


class TestEnsureCodexMcpRegisteredPreservation:
    _FOREIGN_TOML = '[mcp_servers.other_tool]\ncommand = "other"\n'

    def _pre_write(self, fake_home: Path) -> None:
        codex_dir = fake_home / ".codex"
        codex_dir.mkdir(parents=True, exist_ok=True)
        (codex_dir / "config.toml").write_text(self._FOREIGN_TOML)

    def test_foreign_section_survives(self, fake_home: Path) -> None:
        self._pre_write(fake_home)
        ensure_codex_mcp_registered()
        data = tomllib.loads((fake_home / ".codex" / "config.toml").read_text())
        assert "other_tool" in data["mcp_servers"]
        assert data["mcp_servers"]["other_tool"]["command"] == "other"

    def test_autoskillit_section_added(self, fake_home: Path) -> None:
        self._pre_write(fake_home)
        ensure_codex_mcp_registered()
        data = tomllib.loads((fake_home / ".codex" / "config.toml").read_text())
        assert "autoskillit" in data["mcp_servers"]


# ---------------------------------------------------------------------------
# Nested env sub-table preservation (standalone)
# ---------------------------------------------------------------------------


def test_other_tool_env_block_is_unchanged(fake_home: Path) -> None:
    foreign = (
        '[mcp_servers.other_tool]\ncommand = "other"\n\n'
        '[mcp_servers.other_tool.env]\nFOO = "bar"\n'
    )
    codex_dir = fake_home / ".codex"
    codex_dir.mkdir(parents=True, exist_ok=True)
    (codex_dir / "config.toml").write_text(foreign)
    ensure_codex_mcp_registered()
    data = tomllib.loads((fake_home / ".codex" / "config.toml").read_text())
    assert data["mcp_servers"]["other_tool"]["env"]["FOO"] == "bar"


# ---------------------------------------------------------------------------
# Dir creation
# ---------------------------------------------------------------------------


class TestEnsureCodexMcpRegisteredDirCreation:
    def test_codex_dir_does_not_exist_before_call(self, fake_home: Path) -> None:
        assert not (fake_home / ".codex").exists()

    def test_codex_dir_created(self, fake_home: Path) -> None:
        ensure_codex_mcp_registered()
        assert (fake_home / ".codex").is_dir()

    def test_config_toml_created(self, fake_home: Path) -> None:
        ensure_codex_mcp_registered()
        assert (fake_home / ".codex" / "config.toml").is_file()

    def test_toml_valid(self, fake_home: Path) -> None:
        ensure_codex_mcp_registered()
        raw = (fake_home / ".codex" / "config.toml").read_text()
        tomllib.loads(raw)
