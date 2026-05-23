from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.execution.backends import ensure_codex_mcp_registered
from autoskillit.execution.backends.codex import (
    _is_autoskillit_registered,
    _read_codex_config,
    _serialize_toml,
    _write_codex_config,
)

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]


class TestReadCodexConfig:
    def test_returns_empty_dict_when_path_missing(self, tmp_path):
        result = _read_codex_config(tmp_path / "nonexistent.toml")
        assert result == {}

    def test_returns_empty_dict_when_file_is_empty(self, tmp_path):
        p = tmp_path / "config.toml"
        p.write_bytes(b"")
        result = _read_codex_config(p)
        assert result == {}

    def test_parses_mcp_servers_section(self, tmp_path):
        p = tmp_path / "config.toml"
        p.write_bytes(b'[mcp_servers.autoskillit]\ncommand = "autoskillit"\n')
        result = _read_codex_config(p)
        assert result["mcp_servers"]["autoskillit"]["command"] == "autoskillit"


class TestSerializeToml:
    def test_top_level_string(self):
        result = _serialize_toml({"key": "value"})
        assert 'key = "value"' in result

    def test_top_level_float(self):
        result = _serialize_toml({"timeout": 30.0})
        assert "timeout = 30.0" in result

    def test_section_header(self):
        data = {"section": {"key": "val"}}
        result = _serialize_toml(data)
        assert "[section]" in result

    def test_nested_table_header(self):
        data = {"parent": {"child": {"key": "val"}}}
        result = _serialize_toml(data)
        assert "[parent.child]" in result

    def test_inline_dict(self):
        data = {"sec": {"sub": {"key": "val", "env": {"A": "1"}}}}
        result = _serialize_toml(data)
        assert "env = {" in result

    def test_list_values_serialize_as_arrays(self):
        data = {"sec": {"sub": {"args": ["--stdio", "--verbose"]}}}
        result = _serialize_toml(data)
        assert 'args = ["--stdio", "--verbose"]' in result

    def test_round_trip_fidelity(self):
        import tomllib

        original = {
            "mcp_servers": {
                "autoskillit": {
                    "command": "autoskillit",
                    "startup_timeout_sec": 30.0,
                    "tool_timeout_sec": 120.0,
                    "env": {"AUTOSKILLIT_HEADLESS": "1"},
                }
            }
        }
        serialized = _serialize_toml(original)
        parsed = tomllib.loads(serialized)
        assert parsed == original

    def test_round_trip_with_list_values(self):
        import tomllib

        original = {
            "mcp_servers": {
                "other": {
                    "command": "other",
                    "args": ["--stdio"],
                },
                "autoskillit": {
                    "command": "autoskillit",
                    "env": {"AUTOSKILLIT_HEADLESS": "1"},
                },
            }
        }
        serialized = _serialize_toml(original)
        parsed = tomllib.loads(serialized)
        assert parsed == original

    def test_no_duplicate_section_headers(self):
        import tomllib

        data = {
            "mcp_servers": {
                "server1": {
                    "command": "s1",
                    "timeout": 10.0,
                    "env": {"A": "1"},
                },
            }
        }
        serialized = _serialize_toml(data)
        count = serialized.count("[mcp_servers.server1]")
        assert count == 1
        tomllib.loads(serialized)


class TestWriteCodexConfig:
    def test_writes_readable_toml(self, tmp_path):
        p = tmp_path / "config.toml"
        data = {"mcp_servers": {"test": {"command": "test"}}}
        _write_codex_config(p, data)
        assert _read_codex_config(p) == data

    def test_atomic_write_creates_parent_dirs(self, tmp_path):
        p = tmp_path / "nested" / "dir" / "config.toml"
        _write_codex_config(p, {"key": "val"})
        assert p.exists()


class TestIsAutoskillitRegistered:
    def test_empty_config_returns_false(self):
        assert _is_autoskillit_registered({}, headless_auto_gate=True) is False

    def test_mcp_servers_without_autoskillit_returns_false(self):
        assert (
            _is_autoskillit_registered({"mcp_servers": {"other": {}}}, headless_auto_gate=True)
            is False
        )

    def test_wrong_command_returns_false(self):
        config = {
            "mcp_servers": {
                "autoskillit": {
                    "command": "wrong",
                    "env": {"AUTOSKILLIT_HEADLESS": "1"},
                }
            }
        }
        assert _is_autoskillit_registered(config, headless_auto_gate=False) is False

    def test_missing_headless_env_returns_false(self):
        config = {
            "mcp_servers": {
                "autoskillit": {
                    "command": "autoskillit",
                    "env": {},
                }
            }
        }
        assert _is_autoskillit_registered(config, headless_auto_gate=False) is False

    def test_headless_auto_gate_true_missing_auto_gate_env_returns_false(self):
        config = {
            "mcp_servers": {
                "autoskillit": {
                    "command": "autoskillit",
                    "env": {"AUTOSKILLIT_HEADLESS": "1"},
                }
            }
        }
        assert _is_autoskillit_registered(config, headless_auto_gate=True) is False

    def test_headless_auto_gate_false_ignores_auto_gate_env(self):
        config = {
            "mcp_servers": {
                "autoskillit": {
                    "command": "autoskillit",
                    "env": {"AUTOSKILLIT_HEADLESS": "1"},
                }
            }
        }
        assert _is_autoskillit_registered(config, headless_auto_gate=False) is True

    def test_all_checks_pass_with_auto_gate(self):
        config = {
            "mcp_servers": {
                "autoskillit": {
                    "command": "autoskillit",
                    "env": {
                        "AUTOSKILLIT_HEADLESS": "1",
                        "AUTOSKILLIT_HEADLESS_AUTO_GATE": "1",
                    },
                }
            }
        }
        assert _is_autoskillit_registered(config, headless_auto_gate=True) is True

    def test_extra_keys_tolerated(self):
        config = {
            "mcp_servers": {
                "autoskillit": {
                    "command": "autoskillit",
                    "env": {"AUTOSKILLIT_HEADLESS": "1", "EXTRA": "yes"},
                    "extra_field": 42,
                }
            }
        }
        assert _is_autoskillit_registered(config, headless_auto_gate=False) is True


class TestEnsureCodexMcpRegistered:
    def test_first_call_returns_true_and_writes(self, tmp_path):
        p = tmp_path / ".codex" / "config.toml"
        result = ensure_codex_mcp_registered(config_path=p)
        assert result is True
        config = _read_codex_config(p)
        entry = config["mcp_servers"]["autoskillit"]
        assert entry["command"] == "autoskillit"
        assert entry["env"]["AUTOSKILLIT_HEADLESS"] == "1"
        assert entry["startup_timeout_sec"] == 30.0
        assert entry["tool_timeout_sec"] == 120.0

    def test_headless_auto_gate_true_includes_auto_gate_env(self, tmp_path):
        p = tmp_path / "config.toml"
        ensure_codex_mcp_registered(config_path=p, headless_auto_gate=True)
        config = _read_codex_config(p)
        assert config["mcp_servers"]["autoskillit"]["env"]["AUTOSKILLIT_HEADLESS_AUTO_GATE"] == "1"

    def test_headless_auto_gate_false_omits_auto_gate_env(self, tmp_path):
        p = tmp_path / "config.toml"
        ensure_codex_mcp_registered(config_path=p, headless_auto_gate=False)
        config = _read_codex_config(p)
        assert "AUTOSKILLIT_HEADLESS_AUTO_GATE" not in config["mcp_servers"]["autoskillit"]["env"]

    def test_second_call_returns_false(self, tmp_path):
        p = tmp_path / "config.toml"
        ensure_codex_mcp_registered(config_path=p)
        mtime_before = p.stat().st_mtime_ns
        result = ensure_codex_mcp_registered(config_path=p)
        assert result is False
        assert p.stat().st_mtime_ns == mtime_before

    def test_default_config_path(self, monkeypatch, tmp_path):
        fake_home = tmp_path / "fakehome"
        fake_home.mkdir()
        monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_home))
        ensure_codex_mcp_registered()
        assert (fake_home / ".codex" / "config.toml").exists()

    def test_creates_parent_directory(self, tmp_path):
        p = tmp_path / "nonexistent" / ".codex" / "config.toml"
        ensure_codex_mcp_registered(config_path=p)
        assert p.exists()

    def test_no_type_field_in_written_toml(self, tmp_path):
        p = tmp_path / "config.toml"
        ensure_codex_mcp_registered(config_path=p)
        raw = p.read_text()
        assert "type" not in raw

    def test_preserves_existing_config(self, tmp_path):
        p = tmp_path / "config.toml"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text('[mcp_servers.other]\ncommand = "other"\n')
        ensure_codex_mcp_registered(config_path=p)
        config = _read_codex_config(p)
        assert "other" in config["mcp_servers"]
        assert "autoskillit" in config["mcp_servers"]

    def test_preserves_existing_config_with_list_values(self, tmp_path):
        p = tmp_path / "config.toml"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text('[mcp_servers.other]\ncommand = "other"\nargs = ["--stdio"]\n')
        ensure_codex_mcp_registered(config_path=p)
        config = _read_codex_config(p)
        assert config["mcp_servers"]["other"]["args"] == ["--stdio"]
        assert "autoskillit" in config["mcp_servers"]


class TestExports:
    def test_ensure_codex_mcp_registered_in_codex_all(self):
        from autoskillit.execution.backends import codex

        assert "ensure_codex_mcp_registered" in codex.__all__

    def test_private_functions_not_in_all(self):
        from autoskillit.execution.backends import codex

        assert "_read_codex_config" not in codex.__all__
        assert "_write_codex_config" not in codex.__all__
        assert "_serialize_toml" not in codex.__all__
        assert "_is_autoskillit_registered" not in codex.__all__

    def test_importable_from_backends(self):
        from autoskillit.execution.backends import ensure_codex_mcp_registered

        assert callable(ensure_codex_mcp_registered)

    def test_no_third_party_toml_in_codex(self):
        import ast

        source = (
            Path(__file__).resolve().parents[3]
            / "src"
            / "autoskillit"
            / "execution"
            / "backends"
            / "codex.py"
        )
        tree = ast.parse(source.read_text())
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = []
                if isinstance(node, ast.Import):
                    names = [a.name for a in node.names]
                else:
                    names = [node.module or ""]
                for name in names:
                    assert name not in ("toml", "tomli", "tomlkit"), (
                        f"Third-party TOML import found: {name}"
                    )
