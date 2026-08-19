"""Tests for CLI install, upgrade, and quota-related commands."""

from __future__ import annotations

import hashlib
import importlib
import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from autoskillit import __version__, cli
from autoskillit.cli.install._install_contract import InstallMode, InstallRequest

pytestmark = [pytest.mark.layer("cli"), pytest.mark.medium]


def _direct_request(scope: str = "user") -> InstallRequest:
    return InstallRequest(
        scope=scope,
        mode=InstallMode.DIRECT,
        require_registered_plugin=True,
        expected_version=__version__,
    )


def _seed_current_installed_plugin(home: Path) -> Path:
    """Materialize the cache root a successful mocked Claude install creates."""
    from autoskillit import __version__

    root = (
        home / ".claude" / "plugins" / "cache" / "autoskillit-local" / "autoskillit" / __version__
    )
    plugin_dir = root / ".claude-plugin"
    hooks_dir = root / "hooks"
    plugin_dir.mkdir(parents=True, exist_ok=True)
    hooks_dir.mkdir(parents=True, exist_ok=True)
    (plugin_dir / "plugin.json").write_text(
        json.dumps({"name": "autoskillit", "version": __version__})
    )
    (hooks_dir / "hooks.json").write_text(json.dumps({"hooks": {}}))
    return root


def _successful_claude_run(home: Path):
    """Return a subprocess double that publishes cache bytes before success."""

    def run(cmd, *_args, **_kwargs):
        normalized = tuple(str(part) for part in cmd)
        if normalized[:3] == ("claude", "plugin", "install"):
            root = _seed_current_installed_plugin(home)
            registry_path = home / ".claude" / "plugins" / "installed_plugins.json"
            registry_path.parent.mkdir(parents=True, exist_ok=True)
            registry_path.write_text(
                json.dumps(
                    {
                        "version": 2,
                        "plugins": {
                            "autoskillit@autoskillit-local": [
                                {
                                    "installPath": str(root),
                                    "scope": "user",
                                }
                            ]
                        },
                    }
                )
            )
        return subprocess.CompletedProcess(cmd, returncode=0, stdout="", stderr="")

    return run


class TestCLIInstall:
    def test_install_validates_scope(self) -> None:
        """install rejects invalid scope values."""
        from autoskillit.cli.install._install_contract import InstallFailureKind, InstallOutcome
        from autoskillit.cli.install._marketplace import install

        result = install(request=_direct_request("invalid"))
        assert result.outcome is InstallOutcome.FAILED
        assert result.failure_kind is InstallFailureKind.PREFLIGHT
        assert "Invalid scope" in result.findings[0]

    def test_install_creates_marketplace_dir(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """install creates the marketplace directory structure."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        import importlib as _importlib

        _app_mod = _importlib.import_module("autoskillit.cli.install._marketplace")
        monkeypatch.setattr(_app_mod, "is_git_worktree", lambda path: False)
        from autoskillit.cli.install._marketplace import _ensure_marketplace

        marketplace_dir = _ensure_marketplace()
        assert (marketplace_dir / ".claude-plugin" / "marketplace.json").is_file()
        public_plugin = marketplace_dir / "plugins" / "autoskillit"
        assert public_plugin.is_dir()
        assert not public_plugin.is_symlink()
        assert (marketplace_dir / "plugins" / ".autoskillit.autoskillit-projection.json").is_file()

    def test_install_public_documents_and_private_manifest_are_synchronized(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Every installed public projection is safe and privately attested."""
        import importlib as _importlib

        from autoskillit.workspace import parse_frontmatter_content

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        marketplace = _importlib.import_module("autoskillit.cli.install._marketplace")
        monkeypatch.setattr(marketplace, "is_git_worktree", lambda path: False)

        marketplace_dir = marketplace._ensure_marketplace()
        public_root = marketplace_dir / "plugins" / "autoskillit"
        private_path = marketplace_dir / "plugins" / ".autoskillit.autoskillit-projection.json"
        private = json.loads(private_path.read_text())
        public_names = {path.name for path in (public_root / "skills").iterdir()}

        assert set(private["skills"]) == public_names
        for name, identity in private["skills"].items():
            projected = (public_root / "skills" / name / "SKILL.md").read_text()
            parsed = parse_frontmatter_content(projected)
            assert parsed.is_valid and parsed.data is not None
            assert {
                "activate_deps",
                "uses_capabilities",
                "execution_role",
            }.isdisjoint(parsed.data)
            assert {
                "canonical_digest",
                "projected_digest",
                "source",
                "logical_name",
                "search_dir",
                "precedence",
                "uses_capabilities",
                "execution_role",
                "activate_deps",
            } <= set(identity)
            assert "source_path" not in identity
            assert hashlib.sha256(projected.encode()).hexdigest() == identity["projected_digest"]

    def test_install_rejects_role_incompatible_skill_contract(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import importlib as _importlib

        from autoskillit.core import SkillContractError, SkillSource
        from autoskillit.workspace.skills import _skill_info_from_frontmatter

        invalid_md = tmp_path / "invalid" / "SKILL.md"
        invalid_md.parent.mkdir()
        invalid_md.write_text(
            "---\n"
            "name: invalid\n"
            "description: Invalid package contract.\n"
            "uses_capabilities: [run_skill]\n"
            "execution_role: session\n"
            "---\n"
            'run_skill("/child")\n'
        )
        invalid = _skill_info_from_frontmatter(
            "invalid",
            SkillSource.BUNDLED,
            invalid_md,
        )
        marketplace = _importlib.import_module("autoskillit.cli.install._marketplace")
        monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
        monkeypatch.setattr(marketplace, "is_git_worktree", lambda _path: False)
        monkeypatch.setattr(
            marketplace.DefaultSkillResolver,
            "list_all",
            lambda _self: [invalid],
        )

        with pytest.raises(SkillContractError, match="invalid|run_skill|role"):
            marketplace._ensure_marketplace()

    def test_install_projection_is_independent_of_test_file_location(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Published plugin metadata is projected from the installed package."""
        import importlib.resources as ir

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        import importlib as _importlib

        _app_mod = _importlib.import_module("autoskillit.cli.install._marketplace")
        monkeypatch.setattr(_app_mod, "is_git_worktree", lambda path: False)
        from autoskillit.cli.install._marketplace import _ensure_marketplace

        marketplace_dir = _ensure_marketplace()
        published = marketplace_dir / "plugins" / "autoskillit"
        expected = Path(ir.files("autoskillit"))
        assert published.resolve() != expected.resolve()
        assert (published / ".claude-plugin" / "plugin.json").read_bytes() == (
            expected / ".claude-plugin" / "plugin.json"
        ).read_bytes()

    def test_install_marketplace_json_content(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Marketplace manifest has correct structure and plugin name."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        import importlib as _importlib

        _app_mod = _importlib.import_module("autoskillit.cli.install._marketplace")
        monkeypatch.setattr(_app_mod, "is_git_worktree", lambda path: False)
        from autoskillit.cli.install._marketplace import _ensure_marketplace

        marketplace_dir = _ensure_marketplace()
        data = json.loads((marketplace_dir / ".claude-plugin" / "marketplace.json").read_text())
        assert data["name"] == "autoskillit-local"
        assert len(data["plugins"]) == 1
        assert data["plugins"][0]["name"] == "autoskillit"
        assert data["plugins"][0]["source"] == "./plugins/autoskillit"

    def test_install_publishes_plugin_generation(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """install() stages and publishes the plugin content into the generation store."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        import importlib as _importlib

        _app_mod = _importlib.import_module("autoskillit.cli.install._marketplace")
        monkeypatch.setattr(_app_mod, "is_git_worktree", lambda path: False)
        from autoskillit.cli.install._install_contract import InstallOutcome
        from autoskillit.cli.install._marketplace import install

        result = install(request=_direct_request())

        assert result.outcome is InstallOutcome.COMPLETED
        assert result.verified_identity == f"autoskillit@autoskillit-local:{__version__}"

        from autoskillit.core import resolve_current_generation

        generation_root = resolve_current_generation(
            tmp_path, "autoskillit@autoskillit-local", __version__
        )
        assert generation_root is not None
        assert (generation_root / ".claude-plugin" / "plugin.json").is_file()
        plugin_manifest = json.loads(
            (generation_root / ".claude-plugin" / "plugin.json").read_text()
        )
        assert plugin_manifest["name"] == "autoskillit"

    def test_install_scope_selects_settings_path_for_hook_eviction(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """install() forwards scope into the settings path used for hook eviction."""
        import importlib as _importlib

        _app_mod = _importlib.import_module("autoskillit.cli.install._marketplace")

        home = tmp_path / "home"
        project = tmp_path / "project"
        home.mkdir()
        project.mkdir()

        project_settings = project / ".claude" / "settings.json"
        project_settings.parent.mkdir(parents=True)
        project_settings.write_text(
            json.dumps(
                {
                    "hooks": {
                        "PostToolUse": [
                            {
                                "matcher": "mcp__.*autoskillit.*",
                                "hooks": [
                                    {
                                        "type": "command",
                                        "command": "python3 /stale/autoskillit_hook.py",
                                    }
                                ],
                            }
                        ]
                    }
                }
            )
        )

        monkeypatch.setattr(Path, "home", lambda: home)
        monkeypatch.setattr(Path, "cwd", lambda: project)
        monkeypatch.setattr(_app_mod, "is_git_worktree", lambda path: False)
        from autoskillit.cli.install._marketplace import install

        install(request=_direct_request("project"))

        data = json.loads(project_settings.read_text())
        for event_hooks in data.get("hooks", {}).values():
            for entry in event_hooks:
                for hook in entry.get("hooks", []):
                    assert "autoskillit" not in hook["command"], (
                        "install() must evict stale hooks from the scope-selected settings file"
                    )

    def test_install_idempotent_marketplace(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Running install twice recreates the sanitized projection without error."""
        import importlib as _importlib

        _app_mod = _importlib.import_module("autoskillit.cli.install._marketplace")

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.setattr(_app_mod, "is_git_worktree", lambda path: False)
        from autoskillit.cli.install._marketplace import install

        install(request=_direct_request())
        install(request=_direct_request())  # second run should not fail

        published = tmp_path / ".autoskillit" / "marketplace" / "plugins" / "autoskillit"
        assert published.is_dir()
        assert not published.is_symlink()
        assert (published / ".claude-plugin" / "plugin.json").is_file()

    def test_install_backend_guard_returns_declined_for_non_claude_code(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """install() returns a declined result when capability is false."""

        from autoskillit.config import AgentBackendConfig, AutomationConfig

        mock_cfg = AutomationConfig(agent_backend=AgentBackendConfig(backend="codex"))
        monkeypatch.setattr("autoskillit.config.load_config", lambda _: mock_cfg)
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
        monkeypatch.setattr(Path, "cwd", staticmethod(lambda: tmp_path))

        mock_backend = MagicMock()
        mock_backend.capabilities.plugin_install_capable = False
        monkeypatch.setattr("autoskillit.execution.get_backend", lambda name: mock_backend)

        from autoskillit.cli.install._marketplace import install

        result = install(request=_direct_request())

        from autoskillit.cli.install._install_contract import InstallOutcome

        assert result.outcome is InstallOutcome.DECLINED
        assert "plugin_install_capable" in result.findings[0]

    def test_install_backend_guard_allows_claude_code(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        """install() proceeds past backend guard when backend == 'claude-code'."""
        from autoskillit.config import AgentBackendConfig, AutomationConfig

        mock_cfg = AutomationConfig(agent_backend=AgentBackendConfig(backend="claude-code"))
        monkeypatch.setattr("autoskillit.config.load_config", lambda _: mock_cfg)
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
        monkeypatch.setattr(Path, "cwd", staticmethod(lambda: tmp_path))

        _app_mod = importlib.import_module("autoskillit.cli.install._marketplace")
        monkeypatch.setattr(_app_mod, "is_git_worktree", lambda path: False)
        monkeypatch.setattr(_app_mod, "evict_direct_mcp_entry", lambda _: False)
        monkeypatch.setattr(
            "autoskillit.cli._hooks._evict_stale_autoskillit_hooks", lambda _: None
        )
        monkeypatch.setattr(_app_mod, "write_generated_hooks_json", lambda _root: None)
        monkeypatch.setattr(_app_mod, "atomic_write", lambda *a, **kw: None)
        from autoskillit.cli.install._marketplace import install

        result = install(request=_direct_request())

        from autoskillit.cli.install._install_contract import InstallOutcome

        assert result.outcome is InstallOutcome.COMPLETED  # transaction ran past the guard

    def test_install_backend_guard_no_new_module_level_imports(self) -> None:
        """Verify load_config is NOT at module level in _marketplace.py."""
        import ast

        from autoskillit.core.paths import pkg_root

        source = (pkg_root() / "cli" / "install" / "_marketplace.py").read_text()
        tree = ast.parse(source)

        # Check that no top-level import references autoskillit.config
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert "autoskillit.config" not in node.module, (
                    "load_config must be a deferred import inside install(), not module-level"
                )
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    assert "autoskillit.config" not in alias.name, (
                        "load_config must be a deferred import inside install(), not module-level"
                    )

    def test_install_evicts_stale_direct_mcp_entry(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """install() must remove a stale mcpServers.autoskillit entry left by a prior init."""
        import importlib as _importlib

        _app_mod = _importlib.import_module("autoskillit.cli.install._marketplace")

        # Seed stale direct entry as left by a prior `autoskillit init`
        claude_json = tmp_path / ".claude.json"
        claude_json.write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "autoskillit": {"type": "stdio", "command": "autoskillit", "args": []}
                    }
                }
            )
        )
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.setattr(_app_mod, "is_git_worktree", lambda path: False)
        from autoskillit.cli.install._marketplace import install

        install(request=_direct_request())

        data = json.loads(claude_json.read_text())
        assert "autoskillit" not in data.get("mcpServers", {})


class TestMigrateCommand:
    """Tests for the ``autoskillit migrate`` CLI command."""

    # MIG1: --check reports outdated scripts without modifying them
    def test_check_reports_outdated_scripts(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        """migrate --check lists scripts needing migration and does not modify them."""
        import autoskillit

        monkeypatch.setattr(autoskillit, "__version__", "99.0.0")
        monkeypatch.chdir(tmp_path)
        scripts_dir = tmp_path / ".autoskillit" / "recipes"
        scripts_dir.mkdir(parents=True)
        script_content = 'name: my-pipeline\ndescription: Test\nautoskillit_version: "0.1.0"\n'
        (scripts_dir / "my-pipeline.yaml").write_text(script_content)

        with pytest.raises(SystemExit) as exc_info:
            cli.migrate(check=True)
        assert exc_info.value.code == 1

        captured = capsys.readouterr()
        assert "my-pipeline" in captured.out
        # Original file untouched
        assert (scripts_dir / "my-pipeline.yaml").read_text() == script_content

    # MIG2: No scripts to migrate prints "all scripts up to date"
    def test_no_pending_migrations_reports_up_to_date(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        """migrate reports all scripts up to date when versions match."""
        import autoskillit

        current_version = autoskillit.__version__
        monkeypatch.chdir(tmp_path)
        scripts_dir = tmp_path / ".autoskillit" / "recipes"
        scripts_dir.mkdir(parents=True)
        (scripts_dir / "current.yaml").write_text(
            f'name: current\ndescription: Up to date\nautoskillit_version: "{current_version}"\n'
        )

        cli.migrate(check=False)

        captured = capsys.readouterr()
        assert "All" in captured.out
        assert "at version" in captured.out

    # MIG3: Reports count of scripts needing migration
    def test_reports_count_of_pending_scripts(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        """migrate reports the number of scripts needing migration."""
        import autoskillit

        monkeypatch.setattr(autoskillit, "__version__", "99.0.0")
        monkeypatch.chdir(tmp_path)
        scripts_dir = tmp_path / ".autoskillit" / "recipes"
        scripts_dir.mkdir(parents=True)
        (scripts_dir / "old1.yaml").write_text(
            'name: old1\ndescription: Old\nautoskillit_version: "0.1.0"\n'
        )
        (scripts_dir / "old2.yaml").write_text(
            'name: old2\ndescription: Also old\nautoskillit_version: "0.1.0"\n'
        )

        cli.migrate(check=False)

        captured = capsys.readouterr()
        assert "2 recipe(s) need migration" in captured.out

    # MIG4: --check returns exit code 1 when migrations pending
    def test_check_exits_1_when_pending(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """migrate --check exits with code 1 when scripts need migration."""
        import autoskillit

        monkeypatch.setattr(autoskillit, "__version__", "99.0.0")
        monkeypatch.chdir(tmp_path)
        scripts_dir = tmp_path / ".autoskillit" / "recipes"
        scripts_dir.mkdir(parents=True)
        (scripts_dir / "outdated.yaml").write_text(
            'name: outdated\ndescription: Old\nautoskillit_version: "0.1.0"\n'
        )

        with pytest.raises(SystemExit) as exc_info:
            cli.migrate(check=True)
        assert exc_info.value.code == 1

    # MIG5: --check returns exit code 0 when all current
    def test_check_exits_0_when_current(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """migrate --check exits normally (no SystemExit) when all scripts are current."""
        import autoskillit

        current_version = autoskillit.__version__
        monkeypatch.chdir(tmp_path)
        scripts_dir = tmp_path / ".autoskillit" / "recipes"
        scripts_dir.mkdir(parents=True)
        (scripts_dir / "current.yaml").write_text(
            f'name: current\ndescription: Up to date\nautoskillit_version: "{current_version}"\n'
        )

        # Should not raise SystemExit
        cli.migrate(check=True)

    # MC3: Without --check, output contains no "Claude Code session" instructions
    def test_migrate_no_check_prints_summary_only(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        """MC3: Without --check, output lists pending but omits Claude Code session text."""
        import autoskillit

        monkeypatch.setattr(autoskillit, "__version__", "99.0.0")
        monkeypatch.chdir(tmp_path)
        scripts_dir = tmp_path / ".autoskillit" / "recipes"
        scripts_dir.mkdir(parents=True)
        (scripts_dir / "old.yaml").write_text(
            'name: old\ndescription: Old recipe\nautoskillit_version: "0.1.0"\n'
        )

        cli.migrate(check=False)

        captured = capsys.readouterr()
        assert "old" in captured.out
        assert "Claude Code session" not in captured.out

    # T10a: default `migrate` reports a pending skill migration, never touches the file
    def test_migrate_reports_pending_skill_migration_without_touching_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        """T10: default `migrate` reports a pending skill migration but never
        rewrites the file — preserving the report-only contract pinned above."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".autoskillit" / "recipes").mkdir(parents=True)
        skill_dir = tmp_path / ".claude" / "skills" / "audit-bugs"
        skill_dir.mkdir(parents=True)
        skill_path = skill_dir / "SKILL.md"
        corpus_dir = Path(__file__).parents[1] / "contracts" / "fixtures" / "skill_contract_corpus"
        original = (corpus_dir / "precontract_audit_bugs.md").read_text(encoding="utf-8")
        skill_path.write_text(original, encoding="utf-8")

        cli.migrate(check=False, fix=False)

        captured = capsys.readouterr()
        assert "audit-bugs" in captured.out
        assert "migrate --fix" in captured.out
        assert skill_path.read_text(encoding="utf-8") == original

    # T10b: `migrate --fix` rewrites the file — fails while the adapter is
    # registered but unreachable from the CLI driver.
    def test_migrate_fix_rewrites_pending_skill_on_disk(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        from autoskillit.workspace import read_skill_frontmatter

        monkeypatch.chdir(tmp_path)
        (tmp_path / ".autoskillit" / "recipes").mkdir(parents=True)
        skill_dir = tmp_path / ".claude" / "skills" / "audit-bugs"
        skill_dir.mkdir(parents=True)
        skill_path = skill_dir / "SKILL.md"
        corpus_dir = Path(__file__).parents[1] / "contracts" / "fixtures" / "skill_contract_corpus"
        original = (corpus_dir / "precontract_audit_bugs.md").read_text(encoding="utf-8")
        skill_path.write_text(original, encoding="utf-8")

        cli.migrate(check=False, fix=True)

        captured = capsys.readouterr()
        assert "fixed: audit-bugs" in captured.out
        rewritten = skill_path.read_text(encoding="utf-8")
        assert rewritten != original
        parsed = read_skill_frontmatter(skill_path)
        assert parsed.is_valid
        assert parsed.data is not None
        capabilities = parsed.data.get("uses_capabilities")
        assert isinstance(capabilities, list)
        assert "claude_dir" in capabilities

    def test_migrate_fix_reports_each_failure_and_exits_nonzero(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        from autoskillit.migration import MigrationFile
        from autoskillit.migration.engine import MigrationResult

        monkeypatch.chdir(tmp_path)
        (tmp_path / ".autoskillit" / "recipes").mkdir(parents=True)
        skill_files: list[MigrationFile] = []
        for name in ("first", "second"):
            skill_dir = tmp_path / ".claude" / "skills" / name
            skill_dir.mkdir(parents=True)
            skill_path = skill_dir / "SKILL.md"
            skill_path.write_text(f"---\nname: {name}\n---\nRead .claude/settings.json.\n")
            skill_files.append(
                MigrationFile(
                    name=name,
                    path=skill_path,
                    file_type="skill",
                    current_version=None,
                )
            )

        class FakeSkillAdapter:
            def discover(self, project_dir: Path) -> list[MigrationFile]:
                return skill_files

            def needs_migration(self, file: MigrationFile) -> bool:
                return True

        calls: list[str] = []

        class FakeEngine:
            def get_adapter(self, file_type: str) -> FakeSkillAdapter | None:
                return FakeSkillAdapter() if file_type == "skill" else None

            async def migrate_file(
                self, file: MigrationFile, **_kwargs: object
            ) -> MigrationResult:
                calls.append(file.name)
                if file.name == "first":
                    raise OSError("disk unavailable")
                return MigrationResult(success=False, name=file.name, error="still invalid")

        monkeypatch.setattr(
            "autoskillit.migration.default_migration_engine",
            lambda: FakeEngine(),
        )

        with pytest.raises(SystemExit) as exc_info:
            cli.migrate(check=False, fix=True)

        assert exc_info.value.code == 1
        assert calls == ["first", "second"]
        captured = capsys.readouterr()
        assert "FAILED: first: disk unavailable" in captured.out
        assert "FAILED: second: still invalid" in captured.out


class TestInstallCommand:
    def test_worktree_guard_raises_before_any_mutation(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The worktree guard raises SystemExit, and it now runs in install() preflight.

        Hoisted out of _ensure_marketplace so it fires ahead of every persistent
        mutation — the guard used to sit above _ensure_marketplace's first
        atomic_write, which was correct but only by a few lines.
        """
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        import importlib as _importlib

        _app_mod = _importlib.import_module("autoskillit.cli.install._marketplace")
        monkeypatch.setattr(_app_mod, "is_git_worktree", lambda path: True)
        from autoskillit.cli.install._marketplace import _assert_not_worktree

        with pytest.raises(RuntimeError, match="worktree"):
            _assert_not_worktree()

    def test_ensure_marketplace_succeeds_in_main_checkout(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """_ensure_marketplace() succeeds when is_git_worktree() returns False."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        import importlib as _importlib

        _app_mod = _importlib.import_module("autoskillit.cli.install._marketplace")
        monkeypatch.setattr(_app_mod, "is_git_worktree", lambda path: False)
        from autoskillit.cli.install._marketplace import _ensure_marketplace

        result = _ensure_marketplace()
        assert result == tmp_path / ".autoskillit" / "marketplace"

    def test_install_projection_is_not_inside_git_worktree(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """After install, the public projection must not be inside a git worktree.

        This is the regression test for transient source paths after cleanup.
        Skipped when running from a worktree install (which is the expected
        dev environment during worktree-based implementation).
        """
        from autoskillit.core.paths import is_git_worktree, pkg_root

        # Check filesystem directly — the cli conftest patches is_git_worktree
        # to return False, so we cannot rely on it for the skip guard.
        pkg = pkg_root()
        for ancestor in [pkg, *pkg.parents]:
            if (ancestor / ".git").is_file():
                pytest.skip("Cannot verify non-worktree install from a worktree environment")
            if (ancestor / ".git").is_dir():
                break

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        from autoskillit.cli.install._marketplace import _ensure_marketplace

        marketplace_dir = _ensure_marketplace()
        published = marketplace_dir / "plugins" / "autoskillit"

        target = published.resolve()
        assert target.is_dir(), "Published plugin must exist and be a directory"
        assert not published.is_symlink()
        assert not is_git_worktree(target), (
            f"Published plugin {target} is inside a git worktree — "
            "it will not survive source cleanup."
        )


class TestGroupFInstall:
    """P8-2, P3-2, P5-4: CLI refactoring — install/quota/upgrade tests."""

    def test_upgrade_uses_atomic_write(self, tmp_path, monkeypatch):
        """upgrade() must call atomic_write, not yaml_file.write_text."""
        import autoskillit.cli.install._marketplace as _mkt
        import autoskillit.core as _core

        monkeypatch.chdir(tmp_path)
        scripts_dir = tmp_path / ".autoskillit" / "scripts"
        scripts_dir.mkdir(parents=True)
        (scripts_dir / "test.yaml").write_text("inputs:\n  foo: bar\n")

        atomic_calls: list[tuple] = []
        original = _core.atomic_write

        def capture(path, content):
            atomic_calls.append((path, content))
            return original(path, content)

        monkeypatch.setattr(_mkt, "atomic_write", capture)
        from autoskillit.cli.install._marketplace import upgrade

        upgrade()

        assert len(atomic_calls) == 1, "Expected exactly one atomic_write call"
        _, content = atomic_calls[0]
        assert "ingredients:" in content
        assert "inputs:" not in content

    def test_upgrade_is_registered_as_cli_command(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """autoskillit upgrade must dispatch as a registered CLI command.

        Regression guard: removal of `@app.command def upgrade()` in cli/app.py
        fails this test via cyclopts raising SystemExit with a non-zero exit
        code (no command named 'upgrade' found). Tests the actual dispatch
        boundary rather than source-text greps.
        """
        from autoskillit.cli.app import app

        monkeypatch.chdir(tmp_path)
        # tmp_path has no .autoskillit/scripts/ → upgrade() prints
        # "Nothing to do" and returns cleanly; cyclopts then exits 0.
        with pytest.raises(SystemExit) as exc_info:
            app(["upgrade"])
        assert exc_info.value.code == 0, (
            f"upgrade must dispatch as a registered CLI command (got exit {exc_info.value.code})"
        )
        captured = capsys.readouterr()
        assert "Nothing to do" in captured.out

    def test_quota_status_subcommand_outputs_json(self, monkeypatch, capsys, tmp_path):
        """quota-status must emit JSON with required keys."""

        async def _mock_check(config):
            return {"should_sleep": False, "sleep_seconds": 0, "utilization": 45.0}

        monkeypatch.setattr("autoskillit.execution.check_and_sleep_if_needed", _mock_check)
        monkeypatch.chdir(tmp_path)
        cli.quota_status()
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert "should_sleep" in data
        assert "sleep_seconds" in data

    def test_quota_hook_script_exists(self):
        """The hook script must be present as a runnable module in the installed package."""
        from pathlib import Path

        import autoskillit

        pkg_dir = Path(autoskillit.__file__).parent
        hook_script = pkg_dir / "hooks" / "guards" / "quota_guard.py"
        assert hook_script.exists(), f"Expected hook script at {hook_script}"

    def test_generate_hooks_json_includes_quota_hook(self):
        """generate_hooks_json() must include quota_guard.py in PreToolUse and pretty_output_hook.py in PostToolUse."""  # noqa: E501
        from autoskillit.hook_registry import generate_hooks_json

        data = generate_hooks_json()
        pretooluse_commands = [
            hook["command"] for entry in data["hooks"]["PreToolUse"] for hook in entry["hooks"]
        ]
        assert any("quota_guard" in cmd for cmd in pretooluse_commands)
        assert "PostToolUse" in data["hooks"]
        posttooluse_commands = [
            hook["command"] for entry in data["hooks"]["PostToolUse"] for hook in entry["hooks"]
        ]
        assert any("pretty_output_hook" in cmd for cmd in posttooluse_commands)

    def test_install_writes_pretooluse_hooks(self):
        """hooks.json must contain the quota PreToolUse hook via generate_hooks_json()."""
        from autoskillit.hooks import generate_hooks_json

        data = generate_hooks_json()
        pretooluse = data.get("hooks", {}).get("PreToolUse", [])
        matchers = [h.get("matcher", "") for h in pretooluse]
        assert any("run_skill" in m for m in matchers), (
            "PreToolUse hook for run_skill not found in hooks.json"
        )

    def test_remove_clone_guard_script_exists(self):
        """The remove_clone_guard hook script must be present as a runnable module."""
        import autoskillit

        pkg_dir = Path(autoskillit.__file__).parent
        hook_script = pkg_dir / "hooks" / "guards" / "remove_clone_guard.py"
        assert hook_script.exists(), f"Expected hook script at {hook_script}"

    def test_install_registers_remove_clone_guard_hook(self):
        """hooks.json must contain the remove_clone_guard PreToolUse hook."""
        from autoskillit.hooks import generate_hooks_json

        data = generate_hooks_json()
        pretooluse = data.get("hooks", {}).get("PreToolUse", [])
        matchers = [h.get("matcher", "") for h in pretooluse]
        assert any("remove_clone" in m for m in matchers), (
            "PreToolUse hook for remove_clone not found in hooks.json"
        )

    def test_install_remove_clone_guard_hook_idempotent(self):
        """generate_hooks_json() called twice produces identical remove_clone entries."""
        from autoskillit.hooks import generate_hooks_json

        data1 = generate_hooks_json()
        data2 = generate_hooks_json()
        pretooluse1 = data1.get("hooks", {}).get("PreToolUse", [])
        pretooluse2 = data2.get("hooks", {}).get("PreToolUse", [])
        remove_clone_1 = [h for h in pretooluse1 if "remove_clone" in h.get("matcher", "")]
        remove_clone_2 = [h for h in pretooluse2 if "remove_clone" in h.get("matcher", "")]
        assert len(remove_clone_1) == 1, (
            f"Expected exactly 1 remove_clone hook entry, got {len(remove_clone_1)}"
        )
        assert remove_clone_1 == remove_clone_2


def test_install_claudecode_guard_returns_deferred_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """install() returns a deferred result when the CLAUDECODE guard fires."""
    import importlib as _importlib

    from autoskillit.cli.install._marketplace import install as _install

    _app_mod = _importlib.import_module("autoskillit.cli.install._marketplace")
    monkeypatch.setattr(_app_mod, "is_git_worktree", lambda path: False)
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    from autoskillit.cli.install._install_contract import InstallOutcome

    result = _install(request=_direct_request())
    assert result.outcome is InstallOutcome.DEFERRED


def test_app_install_deferred_result_exits_without_next_steps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """app.install() preserves the typed deferred outcome at the process boundary."""

    import autoskillit.cli._init_helpers as _init_helpers_mod
    import autoskillit.cli.install._marketplace as _mkt_mod
    from autoskillit.cli.install._install_contract import (
        InstallOutcome,
        InstallProcessStatus,
        InstallResult,
    )

    next_steps_called: list[dict] = []
    monkeypatch.setattr(
        _init_helpers_mod, "_print_next_steps", lambda **kw: next_steps_called.append(kw)
    )
    monkeypatch.setattr(
        _mkt_mod,
        "install",
        lambda **kw: InstallResult(outcome=InstallOutcome.DEFERRED),
    )
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    from autoskillit.cli.app import install as app_install

    with pytest.raises(SystemExit) as exc_info:
        app_install(scope="user")
    assert exc_info.value.code == InstallProcessStatus.DEFERRED
    assert not next_steps_called, "_print_next_steps must not be called for deferred installs"


def test_app_install_constructs_direct_request_and_prints_only_after_completion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The direct command records its obligation and consumes typed completion."""
    import autoskillit.cli._init_helpers as _init_helpers_mod
    import autoskillit.cli.install._install_contract as _contract_mod
    import autoskillit.cli.install._marketplace as _mkt_mod
    from autoskillit import __version__

    real_request = _contract_mod.InstallRequest
    requests: list[_contract_mod.InstallRequest] = []
    installed_requests: list[_contract_mod.InstallRequest] = []
    next_steps_called: list[dict] = []

    def capture_request(**kwargs):
        request = real_request(**kwargs)
        requests.append(request)
        return request

    def capture_install(**kwargs):
        installed_requests.append(kwargs["request"])
        return _contract_mod.InstallResult(outcome=_contract_mod.InstallOutcome.COMPLETED)

    monkeypatch.setattr(_contract_mod, "InstallRequest", capture_request)
    monkeypatch.setattr(_mkt_mod, "install", capture_install)
    monkeypatch.setattr(
        _init_helpers_mod, "_print_next_steps", lambda **kw: next_steps_called.append(kw)
    )

    from autoskillit.cli.app import install as app_install

    app_install(scope="project")

    expected_request = real_request(
        scope="project",
        mode=_contract_mod.InstallMode.DIRECT,
        require_registered_plugin=True,
        expected_version=__version__,
    )
    assert requests == [expected_request]
    assert installed_requests == [expected_request]
    assert next_steps_called == [{"context": "install"}]


def test_app_install_rejects_maintenance_update_without_expected_version() -> None:
    from autoskillit.cli.app import install as app_install

    with pytest.raises(ValueError, match="--maintenance-update requires --expected-version"):
        app_install(maintenance_update=True)


@pytest.mark.parametrize(
    "maintenance_only_kwargs",
    [
        {"require_registered_plugin": True},
        {"expected_version": "1.2.3"},
    ],
    ids=["require-registered-plugin", "expected-version"],
)
def test_app_install_rejects_maintenance_only_fields_in_direct_mode(
    maintenance_only_kwargs: dict[str, object],
) -> None:
    from autoskillit.cli.app import install as app_install

    with pytest.raises(
        ValueError,
        match="--require-registered-plugin and --expected-version require --maintenance-update",
    ):
        app_install(**maintenance_only_kwargs)


def test_app_install_not_required_succeeds_without_next_steps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A typed no-op result remains successful but does not claim completion."""
    import autoskillit.cli._init_helpers as _init_helpers_mod
    import autoskillit.cli.install._marketplace as _mkt_mod
    from autoskillit.cli.install._install_contract import InstallOutcome, InstallResult

    next_steps_called: list[dict] = []
    monkeypatch.setattr(
        _init_helpers_mod, "_print_next_steps", lambda **kw: next_steps_called.append(kw)
    )
    monkeypatch.setattr(
        _mkt_mod,
        "install",
        lambda **kw: InstallResult(outcome=InstallOutcome.NOT_REQUIRED),
    )

    from autoskillit.cli.app import install as app_install

    app_install(scope="user")
    assert not next_steps_called


@pytest.mark.parametrize(
    (
        "outcome_name",
        "failure_name",
        "expected_status",
        "diagnostic",
        "expect_next_steps",
    ),
    [
        ("COMPLETED", None, 0, "completed diagnostic", True),
        ("NOT_REQUIRED", None, 0, "not-required diagnostic", False),
        ("DECLINED", None, 10, "declined diagnostic", False),
        ("DEFERRED", None, 11, "deferred diagnostic", False),
        ("FAILED", "PREFLIGHT", 20, "preflight diagnostic", False),
        ("FAILED", "CHILD", 21, "child diagnostic", False),
        ("FAILED", "POSTCONDITION", 22, "postcondition diagnostic", False),
        (
            "RECOVERY_REQUIRED",
            "ROLLBACK",
            23,
            "recovery-required diagnostic",
            False,
        ),
        ("INDETERMINATE", None, 24, "indeterminate diagnostic", False),
    ],
)
def test_registered_install_boundary_reports_every_outcome_and_suppresses_next_steps(
    outcome_name: str,
    failure_name: str | None,
    expected_status: int,
    diagnostic: str,
    expect_next_steps: bool,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Every typed outcome crosses the registered process boundary truthfully."""
    import autoskillit.cli._init_helpers as _init_helpers_mod
    import autoskillit.cli.install._marketplace as _mkt_mod
    from autoskillit.cli.install._install_contract import (
        InstallFailureKind,
        InstallOutcome,
    )

    outcome = InstallOutcome[outcome_name]
    failure_kind = InstallFailureKind[failure_name] if failure_name is not None else None
    next_steps_called: list[dict] = []
    monkeypatch.setattr(
        _init_helpers_mod, "_print_next_steps", lambda **kw: next_steps_called.append(kw)
    )

    def typed_result(**_kwargs):
        return _mkt_mod._typed_result(
            outcome,
            failure_kind=failure_kind,
            findings=(diagnostic,),
        )

    monkeypatch.setattr(
        _mkt_mod,
        "install",
        typed_result,
    )

    from autoskillit.cli.app import app

    with pytest.raises(SystemExit) as exc_info:
        app(["install"])

    assert exc_info.value.code == expected_status
    output = capsys.readouterr().out
    assert diagnostic in output
    assert bool(next_steps_called) is expect_next_steps
    if not expect_next_steps:
        assert "Plugin installed:" not in output


def test_install_sweeps_all_scopes_for_orphans(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """install() evicts orphaned autoskillit hooks from non-target scopes."""
    import json as _json

    from autoskillit.cli._hooks import sweep_all_scopes_for_orphans

    home = tmp_path / "home"
    project = tmp_path / "project"
    home.mkdir()
    project.mkdir()

    project_settings = project / ".claude" / "settings.json"
    project_settings.parent.mkdir(parents=True)
    project_settings.write_text(
        _json.dumps(
            {
                "hooks": {
                    "PostToolUse": [
                        {
                            "matcher": "mcp__.*autoskillit.*",
                            "hooks": [
                                {"type": "command", "command": "python3 /stale/pretty_output.py"}
                            ],
                        }
                    ]
                }
            }
        )
    )

    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.chdir(project)

    sweep_all_scopes_for_orphans(project)

    data = _json.loads(project_settings.read_text())
    for event_hooks in data.get("hooks", {}).values():
        for entry in event_hooks:
            for hook in entry.get("hooks", []):
                assert "pretty_output.py" not in hook["command"], (
                    "Orphaned hook was not evicted from project scope"
                )


def test_install_creates_autoskillit_gitignore(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After install(), .autoskillit/.gitignore must exist (ensure_project_temp was called)."""
    import importlib as _importlib

    from autoskillit.cli.install._marketplace import install as _install

    _app_mod = _importlib.import_module("autoskillit.cli.install._marketplace")
    monkeypatch.setattr(_app_mod, "is_git_worktree", lambda path: False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(Path, "cwd", lambda: tmp_path)
    monkeypatch.setattr("shutil.which", lambda _cmd, *, path=None: "/usr/bin/claude")
    monkeypatch.setattr(
        "subprocess.run",
        _successful_claude_run(tmp_path),
    )
    monkeypatch.setattr(
        "autoskillit.cli.install._marketplace.evict_direct_mcp_entry", lambda _: False
    )
    monkeypatch.setattr("autoskillit.cli._hooks._evict_stale_autoskillit_hooks", lambda _: None)
    monkeypatch.setattr(
        "autoskillit.cli.install._marketplace.write_generated_hooks_json", lambda _root: None
    )
    monkeypatch.setattr("autoskillit.cli.install._marketplace.atomic_write", lambda *a, **kw: None)
    (tmp_path / ".autoskillit").mkdir()
    _install(request=_direct_request())

    assert (tmp_path / ".autoskillit" / ".gitignore").exists(), (
        ".autoskillit/.gitignore must be created by install(), not just by init()"
    )


def test_install_calls_upgrade_when_scripts_dir_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """install() must migrate .autoskillit/scripts/ → .autoskillit/recipes/ if scripts/ exists."""
    import importlib as _importlib

    from autoskillit.cli.install._marketplace import install as _install

    _app_mod = _importlib.import_module("autoskillit.cli.install._marketplace")
    monkeypatch.setattr(_app_mod, "is_git_worktree", lambda path: False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(Path, "cwd", lambda: tmp_path)
    monkeypatch.setattr("shutil.which", lambda _cmd, *, path=None: "/usr/bin/claude")
    monkeypatch.setattr(
        "subprocess.run",
        _successful_claude_run(tmp_path),
    )
    monkeypatch.setattr(
        "autoskillit.cli.install._marketplace.evict_direct_mcp_entry", lambda _: False
    )
    monkeypatch.setattr("autoskillit.cli._hooks._evict_stale_autoskillit_hooks", lambda _: None)
    monkeypatch.setattr(
        "autoskillit.cli.install._marketplace.write_generated_hooks_json", lambda _root: None
    )
    monkeypatch.setattr("autoskillit.cli.install._marketplace.atomic_write", lambda *a, **kw: None)
    scripts_dir = tmp_path / ".autoskillit" / "scripts"
    scripts_dir.mkdir(parents=True)

    _install(request=_direct_request())

    assert (tmp_path / ".autoskillit" / "recipes").exists(), (
        "install() must migrate scripts/ to recipes/ when scripts/ exists"
    )
    assert not scripts_dir.exists(), "scripts/ must be renamed away"
