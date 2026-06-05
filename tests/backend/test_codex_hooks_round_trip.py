from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from autoskillit.cli._hooks_codex import (
    generate_codex_hooks_config,
    sync_hooks_to_codex_config,
)
from autoskillit.execution.backends._codex_config import _serialize_toml
from autoskillit.hooks import HOOK_REGISTRY

pytestmark = [pytest.mark.medium]


@pytest.fixture()
def fake_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr("pathlib.Path.home", lambda: home)
    return home


class TestCodexHooksConfigRoundTrip:
    def test_returns_dict_not_list(self):
        result = generate_codex_hooks_config()
        assert isinstance(result, dict)

    def test_dict_keys_are_event_types(self):
        result = generate_codex_hooks_config()
        assert len(result) > 0
        assert set(result.keys()).issubset({"PreToolUse", "PostToolUse", "SessionStart"})

    def test_dict_values_are_lists_of_dicts(self):
        result = generate_codex_hooks_config()
        for event_type, entries in result.items():
            assert isinstance(entries, list), f"{event_type} value is not a list"
            for entry in entries:
                assert isinstance(entry, dict), f"{event_type} entry is not a dict"

    def test_no_event_key_on_inner_entries(self):
        result = generate_codex_hooks_config()
        for event_type, entries in result.items():
            for entry in entries:
                assert "event" not in entry, (
                    f"Inner entry in {event_type} has 'event' key (pre-Phase-2 flat format)"
                )

    def test_toml_serialization_produces_nested_key_syntax(self):
        config = generate_codex_hooks_config()
        toml_str = _serialize_toml({"hooks": config})
        assert "[[hooks.PreToolUse]]" in toml_str

    def test_round_trip_via_tomllib(self):
        config = generate_codex_hooks_config()
        toml_str = _serialize_toml({"hooks": config})
        parsed = tomllib.loads(toml_str)
        assert "hooks" in parsed
        assert isinstance(parsed["hooks"], dict)

    def test_round_trip_preserves_hook_commands(self):
        config = generate_codex_hooks_config()
        toml_str = _serialize_toml({"hooks": config})
        parsed = tomllib.loads(toml_str)
        for event_type, entries in parsed["hooks"].items():
            for entry in entries:
                for hook in entry.get("hooks", []):
                    assert hook.get("type") == "command", (
                        f"Hook in {event_type} missing type='command'"
                    )
                    assert "command" in hook, f"Hook in {event_type} missing 'command' key"

    def test_excludes_interactive_only(self):
        interactive_scripts: set[str] = set()
        for hook_def in HOOK_REGISTRY:
            if hook_def.session_scope == "interactive_only":
                interactive_scripts.update(hook_def.scripts)
        assert len(interactive_scripts) > 0, "No interactive_only hooks found in registry"

        result = generate_codex_hooks_config()
        for event_type, entries in result.items():
            for entry in entries:
                for hook in entry.get("hooks", []):
                    cmd = hook.get("command", "")
                    for script in interactive_scripts:
                        script_name = Path(script).stem
                        assert script_name not in cmd, (
                            f"interactive_only script {script!r} found in "
                            f"{event_type} command: {cmd}"
                        )


class TestSyncHooksToCodexConfig:
    def test_creates_config_toml_from_missing(self, fake_home: Path):
        result = sync_hooks_to_codex_config()
        config_path = fake_home / ".codex" / "config.toml"
        assert config_path.exists()
        assert result is True

    def test_written_file_is_valid_toml(self, fake_home: Path):
        sync_hooks_to_codex_config()
        config_path = fake_home / ".codex" / "config.toml"
        tomllib.loads(config_path.read_text())

    def test_hooks_section_is_dict_not_list(self, fake_home: Path):
        sync_hooks_to_codex_config()
        config_path = fake_home / ".codex" / "config.toml"
        parsed = tomllib.loads(config_path.read_text())
        assert isinstance(parsed["hooks"], dict)

    def test_hooks_section_has_event_type_keys(self, fake_home: Path):
        sync_hooks_to_codex_config()
        config_path = fake_home / ".codex" / "config.toml"
        parsed = tomllib.loads(config_path.read_text())
        hooks = parsed["hooks"]
        assert any(k in hooks for k in ("PreToolUse", "PostToolUse", "SessionStart"))

    def test_idempotent_returns_false(self, fake_home: Path):
        first = sync_hooks_to_codex_config()
        second = sync_hooks_to_codex_config()
        assert first is True
        assert second is False

    def test_idempotent_does_not_modify_file(self, fake_home: Path):
        sync_hooks_to_codex_config()
        config_path = fake_home / ".codex" / "config.toml"
        mtime_before = config_path.stat().st_mtime_ns
        sync_hooks_to_codex_config()
        assert config_path.stat().st_mtime_ns == mtime_before

    def test_preserves_foreign_hooks(self, fake_home: Path):
        codex_dir = fake_home / ".codex"
        codex_dir.mkdir(parents=True)
        config_path = codex_dir / "config.toml"
        config_path.write_text(
            "[hooks]\n"
            "[[hooks.SomeOtherTool]]\n"
            'matcher = "ForeignMatcher"\n'
            "[[hooks.SomeOtherTool.hooks]]\n"
            'command = "python3 /usr/local/foreign.py"\n',
        )
        sync_hooks_to_codex_config()
        parsed = tomllib.loads(config_path.read_text())
        assert "SomeOtherTool" in parsed["hooks"]

    def test_codex_dir_created_if_absent(self, fake_home: Path):
        codex_dir = fake_home / ".codex"
        assert not codex_dir.exists()
        sync_hooks_to_codex_config()
        assert codex_dir.exists()
