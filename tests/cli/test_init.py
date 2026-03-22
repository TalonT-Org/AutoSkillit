"""Tests for CLI init, config, and serve-related commands."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

from autoskillit import cli
from autoskillit.cli import _generate_config_yaml


class TestCLIInit:
    @pytest.fixture(autouse=True)
    def _pre_commit_with_scanner(self, tmp_path: Path) -> None:
        """Ensure every TestCLIInit test has a scanner-present pre-commit config."""
        (tmp_path / ".pre-commit-config.yaml").write_text(
            "repos:\n  - repo: dummy\n    hooks:\n      - id: gitleaks\n"
        )

    # CL1
    def test_serve_calls_mcp_run(self) -> None:
        mock_mcp = MagicMock()
        with patch.object(cli, "serve", wraps=cli.serve):
            with (
                patch("autoskillit.server.mcp", mock_mcp),
                patch("autoskillit.core.configure_logging"),
            ):
                cli.serve()
        mock_mcp.run.assert_called_once()

    # CL3
    def test_init_creates_config_dir(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        cli.init(test_command="pytest -v")
        assert (tmp_path / ".autoskillit").is_dir()

    # CL4
    def test_init_writes_config_yaml(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        cli.init(test_command="pytest -v")
        config_path = tmp_path / ".autoskillit" / "config.yaml"
        assert config_path.is_file()
        data = yaml.safe_load(config_path.read_text())
        assert data["test_check"]["command"] == ["pytest", "-v"]
        assert data["safety"]["reset_guard_marker"] == ".autoskillit-workspace"

    # CL5
    def test_init_interactive_prompts_for_test_command(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        with patch("autoskillit.cli.app._prompt_test_command", return_value=["npm", "test"]):
            cli.init()
        config_path = tmp_path / ".autoskillit" / "config.yaml"
        data = yaml.safe_load(config_path.read_text())
        assert data["test_check"]["command"] == ["npm", "test"]

    # CL6
    def test_init_no_overwrite_without_force(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        config_dir = tmp_path / ".autoskillit"
        config_dir.mkdir()
        config_path = config_dir / "config.yaml"
        config_path.write_text("original: true\n")

        cli.init(force=False)

        assert config_path.read_text() == "original: true\n"
        captured = capsys.readouterr()
        assert "already exists" in captured.out

    # CL7
    def test_config_show_outputs_json(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        monkeypatch.chdir(tmp_path)
        cli.config_show()
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert "test_check" in data
        assert "safety" in data

    # CL8
    def test_unknown_command_exits_nonzero(self) -> None:
        with pytest.raises(SystemExit) as exc_info:
            with patch("sys.argv", ["autoskillit", "nonexistent"]):
                cli.main()
        assert exc_info.value.code != 0

    def test_init_force_overwrites(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        config_dir = tmp_path / ".autoskillit"
        config_dir.mkdir()
        config_path = config_dir / "config.yaml"
        config_path.write_text("old: true\n")

        cli.init(test_command="pytest -v", force=True)

        data = yaml.safe_load(config_path.read_text())
        assert data["test_check"]["command"] == ["pytest", "-v"]

    def test_generate_config_yaml_contains_test_command(self) -> None:
        """_generate_config_yaml embeds the test command in active YAML."""
        yaml_str = _generate_config_yaml(["pytest", "-v"])
        assert 'command: ["pytest", "-v"]' in yaml_str

    def test_generate_config_yaml_has_commented_advanced_sections(self) -> None:
        """Generated YAML includes commented-out advanced config sections."""
        yaml_str = _generate_config_yaml(["pytest", "-v"])
        assert "# classify_fix:" in yaml_str
        assert "# reset_workspace:" in yaml_str
        assert "# implement_gate:" in yaml_str

    def test_generate_config_yaml_uncommented_parts_are_valid(self) -> None:
        """The uncommented portion of generated YAML parses as valid config."""
        yaml_str = _generate_config_yaml(["task", "test-all"])
        parsed = yaml.safe_load(yaml_str)
        assert parsed["test_check"]["command"] == ["task", "test-all"]
        assert parsed["safety"]["reset_guard_marker"] == ".autoskillit-workspace"

    def test_generate_config_yaml_forbids_secret_fields(self) -> None:
        """SEC-6: _generate_config_yaml() output contains no token, secret, or password keys."""
        yaml_str = _generate_config_yaml(["task", "test-check"])
        # Strip comment lines, then parse with YAML to inspect keys and values only
        active_lines = [ln for ln in yaml_str.splitlines() if not ln.lstrip().startswith("#")]
        parsed = yaml.safe_load("\n".join(active_lines)) or {}

        def _collect_strings(obj: object) -> list[str]:
            if isinstance(obj, dict):
                return [str(k) for k in obj] + [
                    s for v in obj.values() for s in _collect_strings(v)
                ]
            if isinstance(obj, list):
                return [s for item in obj for s in _collect_strings(item)]
            return [str(obj)]

        all_strings = " ".join(_collect_strings(parsed)).lower()
        for forbidden in ("token", "secret", "password", "credential"):
            assert forbidden not in all_strings, (
                f"_generate_config_yaml() must not emit '{forbidden}' in active YAML keys/values"
            )

    def test_init_writes_template_with_comments(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """init writes a config file containing commented advanced sections."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        cli.init(test_command="pytest -v")

        config_path = tmp_path / ".autoskillit" / "config.yaml"
        content = config_path.read_text()
        assert "# classify_fix:" in content
        assert "test_check:" in content

    def test_init_test_command_with_force(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """--test-command combined with --force overwrites existing config."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        config_dir = tmp_path / ".autoskillit"
        config_dir.mkdir()
        (config_dir / "config.yaml").write_text("old: true\n")

        cli.init(test_command="npm test", force=True)

        data = yaml.safe_load((config_dir / "config.yaml").read_text())
        assert data["test_check"]["command"] == ["npm", "test"]

    def test_init_idempotent_rerun(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Running init twice is safe — config preserved on second run."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        cli.init(test_command="pytest -v")
        config_before = (tmp_path / ".autoskillit" / "config.yaml").read_text()
        # Re-run init — should not overwrite without --force
        cli.init(test_command="pytest -v")
        assert (tmp_path / ".autoskillit" / "config.yaml").read_text() == config_before

    # CI-SCOPE-1
    def test_init_registers_mcp_server_in_claude_json(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """init --scope user writes mcpServers.autoskillit to ~/.claude.json."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.setattr("autoskillit.cli._init_helpers._is_plugin_installed", lambda: False)
        cli.init(scope="user", test_command="task test-all")
        claude_json = tmp_path / ".claude.json"
        data = json.loads(claude_json.read_text())
        assert "autoskillit" in data["mcpServers"]
        assert data["mcpServers"]["autoskillit"]["command"] == "autoskillit"
        assert data["mcpServers"]["autoskillit"]["args"] == []

    # CI-SCOPE-2
    def test_init_registers_hooks_in_settings_json(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """init --scope user registers all HOOK_REGISTRY hooks in settings.json."""
        from autoskillit.hook_registry import HOOK_REGISTRY

        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        cli.init(scope="user", test_command="task test-all")
        settings_path = tmp_path / ".claude" / "settings.json"
        data = json.loads(settings_path.read_text())
        registered = " ".join(
            cmd
            for event_entries in data["hooks"].values()
            for entry in event_entries
            for hook in entry.get("hooks", [])
            for cmd in [hook.get("command", "")]
        )
        for hdef in HOOK_REGISTRY:
            for script in hdef.scripts:
                assert script in registered, f"Expected hook script {script!r} to be registered"

    # CI-SCOPE-3
    def test_init_idempotent_no_duplicates(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Running init twice does not duplicate mcpServers.autoskillit or hook entries."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.setattr("autoskillit.cli._init_helpers._is_plugin_installed", lambda: False)
        cli.init(scope="user", test_command="task test-all")
        cli.init(scope="user", test_command="task test-all")
        claude_json = tmp_path / ".claude.json"
        data = json.loads(claude_json.read_text())
        assert list(data["mcpServers"].keys()).count("autoskillit") == 1
        settings_path = tmp_path / ".claude" / "settings.json"
        settings = json.loads(settings_path.read_text())
        pretooluse = settings.get("hooks", {}).get("PreToolUse", [])
        matchers = [e.get("matcher", "") for e in pretooluse]
        # No duplicate matchers — each matcher string appears exactly once
        assert len(matchers) == len(set(matchers)), (
            f"Duplicate PreToolUse matchers after double init: {matchers}"
        )

    # CI-SCOPE-4
    def test_init_default_scope_is_user(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """init without --scope defaults to user scope (writes to ~/.claude.json)."""
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        (project_dir / ".pre-commit-config.yaml").write_text(
            "repos:\n  - repo: dummy\n    hooks:\n      - id: gitleaks\n"
        )
        monkeypatch.chdir(project_dir)
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.setattr("autoskillit.cli._init_helpers._is_plugin_installed", lambda: False)
        cli.init(test_command="task test-all")
        # MCP server should be registered to user home, not project dir
        assert (tmp_path / ".claude.json").exists()

    def test_register_all_config_write_is_validated(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The github.default_repo write in _register_all goes through write_config_layer.

        If someone injects a schema-invalid key before the write, it must be caught
        at write time, not deferred to load_config.
        """
        from autoskillit.config.settings import ConfigSchemaError, write_config_layer

        # Simulate a pre-existing config.yaml with an invalid key
        config_dir = tmp_path / ".autoskillit"
        config_dir.mkdir()
        # The gateway should reject writing a merge of this invalid content
        with pytest.raises(ConfigSchemaError):
            write_config_layer(
                config_dir / "config.yaml",
                {"github": {"token": "ghp_should_not_be_here", "default_repo": "owner/repo"}},
            )


class TestEnsureProjectTemp:
    """N5: ensure_project_temp moved from config.py to _io.py."""

    def test_ensure_project_temp_importable_from_io(self):
        from autoskillit.core.io import ensure_project_temp  # noqa: F401

    def test_ensure_project_temp_creates_temp_dir(self, tmp_path):
        from autoskillit.core.io import ensure_project_temp

        result = ensure_project_temp(tmp_path)
        assert result == tmp_path / ".autoskillit" / "temp"
        assert result.is_dir()

    def test_ensure_project_temp_writes_gitignore(self, tmp_path):
        from autoskillit.core.io import ensure_project_temp

        ensure_project_temp(tmp_path)
        gitignore = tmp_path / ".autoskillit" / ".gitignore"
        content = gitignore.read_text()
        assert "temp/" in content
        assert ".secrets.yaml" in content

    def test_ensure_project_temp_is_idempotent(self, tmp_path):
        from autoskillit.core.io import ensure_project_temp

        ensure_project_temp(tmp_path)
        ensure_project_temp(tmp_path)  # second call must not raise
        assert (tmp_path / ".autoskillit" / "temp").is_dir()

    def test_ensure_project_temp_backfills_secrets_into_existing_gitignore(self, tmp_path):
        from autoskillit.core.io import ensure_project_temp

        # Simulate a pre-fix .gitignore with only temp/
        autoskillit_dir = tmp_path / ".autoskillit"
        autoskillit_dir.mkdir()
        (autoskillit_dir / ".gitignore").write_text("temp/\n")

        ensure_project_temp(tmp_path)
        content = (autoskillit_dir / ".gitignore").read_text()
        assert ".secrets.yaml" in content
        assert "temp/" in content


class TestServeStartupLog:
    """N11: serve() logs startup info including resolved config path and test command."""

    def test_serve_logs_startup_with_config_path(self, tmp_path, monkeypatch):
        from unittest.mock import patch

        import structlog.testing

        import autoskillit.cli as cli_mod
        import autoskillit.server as server_mod

        monkeypatch.chdir(tmp_path)
        (tmp_path / ".autoskillit").mkdir()
        (tmp_path / ".autoskillit" / "config.yaml").write_text(
            "test_check:\n  command: [make, test]\n"
        )

        with (
            patch.object(server_mod.mcp, "run"),
            patch("autoskillit.core.configure_logging"),
            structlog.testing.capture_logs() as logs,
        ):
            cli_mod.serve()

        startup = next((entry for entry in logs if entry.get("event") == "serve_startup"), None)
        assert startup is not None
        assert startup["test_check_command"] == ["make", "test"]
        assert str(tmp_path / ".autoskillit" / "config.yaml") in startup["config_path"]

    def test_serve_logs_startup_config_path_none_when_no_config(self, tmp_path, monkeypatch):
        from unittest.mock import patch

        import structlog.testing

        import autoskillit.cli as cli_mod
        import autoskillit.server as server_mod

        monkeypatch.chdir(tmp_path)

        with (
            patch.object(server_mod.mcp, "run"),
            patch("autoskillit.core.configure_logging"),
            patch("autoskillit.cli.Path.home", return_value=tmp_path),
            structlog.testing.capture_logs() as logs,
        ):
            cli_mod.serve()

        startup = next((entry for entry in logs if entry.get("event") == "serve_startup"), None)
        assert startup is not None
        assert startup["config_path"] is None


def test_init_prompts_for_github_default_repo() -> None:
    """autoskillit init must prompt the user for github.default_repo."""
    import inspect

    from autoskillit.cli._init_helpers import _register_all

    source = inspect.getsource(_register_all)
    assert "github" in source.lower() and (
        "default_repo" in source or "_prompt_github_repo" in source
    ), "init flow must prompt for github.default_repo (REQ-CFG-002)"


def test_init_creates_secrets_template(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """autoskillit init must create .secrets.yaml with a token placeholder."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".autoskillit").mkdir()
    from autoskillit.cli._init_helpers import _create_secrets_template

    _create_secrets_template(tmp_path)
    secrets_path = tmp_path / ".autoskillit" / ".secrets.yaml"
    assert secrets_path.exists(), ".secrets.yaml template not created"
    content = secrets_path.read_text()
    assert "github" in content.lower() and "token" in content.lower()


def test_init_all_created_files_covered_by_gitignore(tmp_path: Path) -> None:
    """Every file in .autoskillit/ after init must be gitignored or in the committed allowlist."""
    from autoskillit.cli._init_helpers import _create_secrets_template
    from autoskillit.core.io import _COMMITTED_BY_DESIGN, ensure_project_temp

    ensure_project_temp(tmp_path)
    _create_secrets_template(tmp_path)

    autoskillit_dir = tmp_path / ".autoskillit"
    gitignore_content = (autoskillit_dir / ".gitignore").read_text()

    for item in autoskillit_dir.iterdir():
        if item.name == ".gitignore":
            continue
        if item.name in _COMMITTED_BY_DESIGN:
            continue
        check_name = item.name + "/" if item.is_dir() else item.name
        assert check_name in gitignore_content, (
            f"{item.name} was created in .autoskillit/ but is not in .gitignore "
            f"and not in _COMMITTED_BY_DESIGN allowlist. "
            f"Add it to _AUTOSKILLIT_GITIGNORE_ENTRIES in core/io.py "
            f"or to _COMMITTED_BY_DESIGN if it should be committed."
        )


def test_secrets_template_gitignore_comment_is_true(tmp_path: Path) -> None:
    """The comment in .secrets.yaml claiming it is gitignored must be actually true."""
    from autoskillit.cli._init_helpers import _create_secrets_template
    from autoskillit.core.io import ensure_project_temp

    ensure_project_temp(tmp_path)
    _create_secrets_template(tmp_path)

    autoskillit_dir = tmp_path / ".autoskillit"
    secrets_content = (autoskillit_dir / ".secrets.yaml").read_text()
    gitignore_content = (autoskillit_dir / ".gitignore").read_text()

    if "already listed in .gitignore" in secrets_content:
        assert ".secrets.yaml" in gitignore_content, (
            ".secrets.yaml comment claims 'already listed in .gitignore' "
            "but .secrets.yaml is not in .autoskillit/.gitignore"
        )


def test_gitignore_entries_includes_secrets_yaml() -> None:
    """_AUTOSKILLIT_GITIGNORE_ENTRIES must include .secrets.yaml — regression guard."""
    from autoskillit.core.io import _AUTOSKILLIT_GITIGNORE_ENTRIES

    assert ".secrets.yaml" in _AUTOSKILLIT_GITIGNORE_ENTRIES


def test_gitignore_entries_includes_temp() -> None:
    """_AUTOSKILLIT_GITIGNORE_ENTRIES must include temp/ — regression guard."""
    from autoskillit.core.io import _AUTOSKILLIT_GITIGNORE_ENTRIES

    assert "temp/" in _AUTOSKILLIT_GITIGNORE_ENTRIES


# RG-ROOT-2
def test_root_gitignore_entries_covers_secrets_yaml() -> None:
    """_ROOT_GITIGNORE_ENTRIES must include the root-scope secrets entry — regression guard."""
    from autoskillit.core.io import _ROOT_GITIGNORE_ENTRIES

    assert ".autoskillit/.secrets.yaml" in _ROOT_GITIGNORE_ENTRIES


# RG-ROOT-3
def test_ensure_project_temp_writes_root_gitignore(tmp_path: Path) -> None:
    """ensure_project_temp() must write all _ROOT_GITIGNORE_ENTRIES to the root .gitignore.

    This is the structural invariant test. Its failure signals that a new sensitive
    root-scope entry was added without updating _ROOT_GITIGNORE_ENTRIES in core/io.py.
    """
    from autoskillit.core.io import _ROOT_GITIGNORE_ENTRIES, ensure_project_temp

    assert not (tmp_path / ".gitignore").exists(), "precondition: no root .gitignore"

    ensure_project_temp(tmp_path)

    root_gitignore = tmp_path / ".gitignore"
    assert root_gitignore.exists(), "ensure_project_temp must create root .gitignore"
    content = root_gitignore.read_text()
    for entry in _ROOT_GITIGNORE_ENTRIES:
        assert entry in content, (
            f"Root .gitignore is missing {entry!r}. "
            "Add it to _ROOT_GITIGNORE_ENTRIES in core/io.py."
        )


# RG-ROOT-4
def test_ensure_project_temp_appends_to_existing_root_gitignore(tmp_path: Path) -> None:
    """ensure_project_temp() must append to an existing root .gitignore without overwriting."""
    from autoskillit.core.io import _ROOT_GITIGNORE_ENTRIES, ensure_project_temp

    existing_content = "*.pyc\n__pycache__/\n.env\n"
    (tmp_path / ".gitignore").write_text(existing_content)

    ensure_project_temp(tmp_path)

    content = (tmp_path / ".gitignore").read_text()
    assert "*.pyc" in content, "existing root .gitignore content must be preserved"
    assert "__pycache__/" in content, "existing root .gitignore content must be preserved"
    for entry in _ROOT_GITIGNORE_ENTRIES:
        assert entry in content, f"Missing entry {entry!r} after append"


# RG-ROOT-5
def test_ensure_project_temp_root_gitignore_idempotent(tmp_path: Path) -> None:
    """Running ensure_project_temp() twice must not duplicate root .gitignore entries."""
    from autoskillit.core.io import _ROOT_GITIGNORE_ENTRIES, ensure_project_temp

    ensure_project_temp(tmp_path)
    ensure_project_temp(tmp_path)

    content = (tmp_path / ".gitignore").read_text()
    for entry in _ROOT_GITIGNORE_ENTRIES:
        assert content.count(entry) == 1, (
            f"Root .gitignore has duplicate entry for {entry!r} after two init calls"
        )


# SS-INIT-1
def test_init_aborts_in_noninteractive_mode_without_scanner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """init raises SystemExit(1) when no scanner found and stdin is not a tty."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    # No .pre-commit-config.yaml created
    with pytest.raises(SystemExit) as exc_info:
        cli.init(test_command="pytest -v")
    assert exc_info.value.code == 1


# SS-INIT-2
def test_init_proceeds_when_scanner_found(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """init proceeds normally when .pre-commit-config.yaml contains a known scanner."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    (tmp_path / ".pre-commit-config.yaml").write_text(
        "repos:\n  - repo: https://github.com/gitleaks/gitleaks\n"
        "    hooks:\n      - id: gitleaks\n"
    )
    cli.init(test_command="pytest -v")
    assert (tmp_path / ".autoskillit" / "config.yaml").is_file()


# SS-INIT-3
def test_init_blocks_without_correct_phrase(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """init raises SystemExit(1) when user types wrong phrase in interactive mode."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    with patch("builtins.input", side_effect=["nope"]) as mock_input:
        with pytest.raises(SystemExit) as exc_info:
            cli.init()
    assert exc_info.value.code == 1
    assert mock_input.call_count == 1


# SS-INIT-4
def test_init_proceeds_with_correct_phrase(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """init completes when user types the exact consent phrase."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    phrase = "I accept the risk of leaking secrets without pre-commit scanning"
    with patch("builtins.input", side_effect=[phrase, "pytest -v", ""]) as mock_input:
        cli.init()
    assert (tmp_path / ".autoskillit" / "config.yaml").is_file()
    assert mock_input.call_count == 3


# SS-INIT-5
def test_init_bypass_logged_to_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """When bypass is accepted, .state.yaml records the bypass with a timestamp."""
    import yaml as _yaml

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    phrase = "I accept the risk of leaking secrets without pre-commit scanning"
    with patch("builtins.input", side_effect=[phrase, "pytest -v", ""]) as mock_input:
        cli.init()
    assert mock_input.call_count == 3
    state = _yaml.safe_load((tmp_path / ".autoskillit" / ".state.yaml").read_text())
    bypass_value = state.get("safety", {}).get("secret_scan_bypass_accepted")
    assert bypass_value is not None, "bypass_accepted timestamp must be persisted in .state.yaml"


# SS-INIT-ROUNDTRIP
def test_bypass_accepted_init_load_config_round_trip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After bypass-accepted init, load_config must NOT raise ConfigSchemaError.

    Regression guard for _log_secret_scan_bypass writing safety.secret_scan_bypass_accepted
    to config.yaml — a key not present in SafetyConfig. This test catches the self-inflicted
    schema violation that was previously undetected because SS-INIT-5 never called load_config.
    """
    from autoskillit.config import load_config

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    phrase = "I accept the risk of leaking secrets without pre-commit scanning"
    with patch("builtins.input", side_effect=[phrase, "pytest -v", ""]):
        cli.init()
    # This must not raise ConfigSchemaError
    cfg = load_config(tmp_path)
    assert cfg.test_check.command == ["pytest", "-v"]


# SS-INIT-STATE-FILE
def test_bypass_log_writes_to_state_file_not_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """_log_secret_scan_bypass must write the timestamp to .state.yaml, not config.yaml.

    config.yaml is schema-validated; .state.yaml holds internal operational state only.
    """
    import yaml as _yaml

    from autoskillit.cli._init_helpers import _log_secret_scan_bypass

    _log_secret_scan_bypass(tmp_path)

    state_path = tmp_path / ".autoskillit" / ".state.yaml"
    config_path = tmp_path / ".autoskillit" / "config.yaml"

    assert state_path.is_file(), ".state.yaml must be created by _log_secret_scan_bypass"
    state_data = _yaml.safe_load(state_path.read_text())
    assert state_data.get("safety", {}).get("secret_scan_bypass_accepted") is not None

    # config.yaml must NOT have the bypass key (either no file or no key)
    if config_path.is_file():
        config_data = _yaml.safe_load(config_path.read_text()) or {}
        assert "secret_scan_bypass_accepted" not in config_data.get("safety", {}), (
            "config.yaml must not contain secret_scan_bypass_accepted"
        )


# SS-INIT-6
def test_init_force_does_not_bypass_secret_scan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """--force must NOT bypass the secret scanning check."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    # Pre-existing config — so --force is relevant
    (tmp_path / ".autoskillit").mkdir()
    (tmp_path / ".autoskillit" / "config.yaml").write_text("old: true\n")
    with pytest.raises(SystemExit) as exc_info:
        cli.init(test_command="pytest -v", force=True)
    assert exc_info.value.code == 1


# SS-INIT-7 (unit test for the helper directly)
def test_check_secret_scanning_detects_known_scanners(tmp_path: Path) -> None:
    """_check_secret_scanning returns True without prompt when scanner is present."""
    from autoskillit.cli._init_helpers import _check_secret_scanning

    for hook_id in ("gitleaks", "detect-secrets", "trufflehog", "git-secrets"):
        repo_dir = tmp_path / hook_id
        repo_dir.mkdir()
        (repo_dir / ".pre-commit-config.yaml").write_text(
            f"repos:\n  - repo: dummy\n    hooks:\n      - id: {hook_id}\n"
        )
        (repo_dir / ".autoskillit").mkdir()
        (repo_dir / ".autoskillit" / "config.yaml").write_text("")
        result = _check_secret_scanning(repo_dir)
        assert result.passed, f"expected passed=True for hook_id={hook_id!r}"


# SS-INIT-8
def test_init_noninteractive_without_test_command_triggers_gate_not_eoferror(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """init without --test-command in non-interactive mode must fire the security gate
    and raise SystemExit(1), not crash with EOFError before the gate is reached.
    Regression guard for: secret scanning gate silently skipped during init (issue #470).
    """
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    # No .pre-commit-config.yaml — gate should block
    # No test_command= — would have triggered EOFError in buggy code
    with pytest.raises(SystemExit) as exc_info:
        cli.init()  # no test_command argument
    assert exc_info.value.code == 1
    # Config must NOT have been written — gate fired before any write
    assert not (tmp_path / ".autoskillit" / "config.yaml").exists()


# SS-INIT-9
def test_init_noninteractive_without_test_command_clean_exit_when_scanner_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """init without --test-command in non-interactive mode raises SystemExit(1) with a
    clear message, even when the secret scanning gate passes. --test-command is required
    for non-interactive init regardless of scanner presence."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    (tmp_path / ".pre-commit-config.yaml").write_text(
        "repos:\n  - repo: dummy\n    hooks:\n      - id: gitleaks\n"
    )
    with pytest.raises(SystemExit) as exc_info:
        cli.init()  # no test_command
    assert exc_info.value.code == 1


# SS-INIT-10
def test_prompt_test_command_noninteractive_raises_system_exit(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """_prompt_test_command() in non-interactive mode must raise SystemExit(1) with
    a clear message pointing to --test-command, rather than raising bare EOFError."""
    from autoskillit.cli._init_helpers import _prompt_test_command

    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    with pytest.raises(SystemExit) as exc_info:
        _prompt_test_command()
    assert exc_info.value.code == 1
    captured = capsys.readouterr()
    assert "non-interactive" in captured.out.lower() and "autoskillit init" in captured.out


# SS-INIT-11
def test_init_gate_fires_before_config_is_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """_check_secret_scanning must be called before atomic_write.
    Verifies ordering invariant: on gate failure, no config.yaml should exist.
    This test uses the gate-fail path (non-interactive, no scanner)."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    # Gate will fail (no scanner). Config must not be written.
    with pytest.raises(SystemExit):
        cli.init(test_command="pytest -v")
    assert not (tmp_path / ".autoskillit" / "config.yaml").exists()


# ON-INIT-1
def test_gitignore_entries_includes_onboarded() -> None:
    """_AUTOSKILLIT_GITIGNORE_ENTRIES must contain '.onboarded'."""
    from autoskillit.core.io import _AUTOSKILLIT_GITIGNORE_ENTRIES

    assert ".onboarded" in _AUTOSKILLIT_GITIGNORE_ENTRIES


# ON-INIT-2
def test_init_force_resets_onboarded_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Given .autoskillit/.onboarded exists, running init(force=True) deletes the marker."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    (tmp_path / ".pre-commit-config.yaml").write_text(
        "repos:\n  - repo: dummy\n    hooks:\n      - id: gitleaks\n"
    )
    config_dir = tmp_path / ".autoskillit"
    config_dir.mkdir()
    (config_dir / "config.yaml").write_text("test_check:\n  command: [task, test-check]\n")
    marker = config_dir / ".onboarded"
    marker.write_text("")
    assert marker.exists()

    cli.init(test_command="pytest -v", force=True)

    assert not marker.exists()


# ON-INIT-3
def test_init_no_force_preserves_onboarded_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Given .autoskillit/.onboarded exists and config.yaml exists, init(force=False) keeps it."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    (tmp_path / ".pre-commit-config.yaml").write_text(
        "repos:\n  - repo: dummy\n    hooks:\n      - id: gitleaks\n"
    )
    config_dir = tmp_path / ".autoskillit"
    config_dir.mkdir()
    (config_dir / "config.yaml").write_text("test_check:\n  command: [task, test-check]\n")
    marker = config_dir / ".onboarded"
    marker.write_text("")

    cli.init(force=False)

    assert marker.exists()
