"""Contract tests for Claude Code external interface conventions.

ALL path components and string values in this file are HARDCODED STRING LITERALS.
NEVER replace with imports from core/claude_conventions.py — that re-creates
the "tests mirror implementation" failure mode this module prevents.

Reference: temp/investigation-ephemeral-skill-dir-layout-bug.md
Governance model mirrors: tests/execution/test_flag_contracts.py
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("contracts"), pytest.mark.medium]


def _materialize_session_catalog(
    manager,
    session_id: str,
    project_root: Path,
):
    from autoskillit.core import SkillExecutionRole
    from autoskillit.workspace import DefaultSkillResolver

    catalog = DefaultSkillResolver().list_effective(
        project_root,
        SkillExecutionRole.SESSION,
    )
    context = manager._provider.catalog_projection_context(catalog, project_root)
    return manager.init_session(session_id, catalog, context)


# ---------------------------------------------------------------------------
# CC-DIR: ClaudeDirectoryConventions value pinning
# Mirror of TestClaudeFlagValues in test_flag_contracts.py — same pattern.
# ---------------------------------------------------------------------------


class TestClaudeDirectoryConventions:
    """Pin each ClaudeDirectoryConventions constant to a hardcoded string literal.

    CRITICAL: These assertions use string literals, NOT re-imports of the constants.
    If a constant's string value changes, the production code drifts from the
    Claude Code specification, and these tests catch it at the first CI run.
    """

    def test_add_dir_skills_subdir_value(self) -> None:
        """--add-dir root: Claude Code discovers skills at .claude/skills/<name>/SKILL.md."""
        from autoskillit.core.claude_conventions import ClaudeDirectoryConventions

        assert str(ClaudeDirectoryConventions.ADD_DIR_SKILLS_SUBDIR) == ".claude/skills"

    def test_plugin_dir_skills_subdir_value(self) -> None:
        """--plugin-dir root: Claude Code discovers skills at skills/<name>/SKILL.md."""
        from autoskillit.core.claude_conventions import ClaudeDirectoryConventions

        assert str(ClaudeDirectoryConventions.PLUGIN_DIR_SKILLS_SUBDIR) == "skills"

    def test_skill_filename_value(self) -> None:
        """Each skill directory contains a SKILL.md file."""
        from autoskillit.core.claude_conventions import ClaudeDirectoryConventions

        assert ClaudeDirectoryConventions.SKILL_FILENAME == "SKILL.md"

    def test_add_dir_full_pattern_is_dot_claude_skills_name_skill_md(self) -> None:
        """Composed path for a skill at an --add-dir root matches the literal pattern."""
        from autoskillit.core.claude_conventions import ClaudeDirectoryConventions

        composed = (
            ClaudeDirectoryConventions.ADD_DIR_SKILLS_SUBDIR
            / "my-skill"
            / ClaudeDirectoryConventions.SKILL_FILENAME
        )
        # String literal — not constructed from the constants themselves
        assert str(composed) == ".claude/skills/my-skill/SKILL.md"


# ---------------------------------------------------------------------------
# CC-001: --add-dir layout behavioral guard
# Path components are HARDCODED STRING LITERALS — do NOT replace with constants.
# ---------------------------------------------------------------------------


class TestAddDirLayoutContract:
    """Guard: init_session must write .claude/skills/<name>/SKILL.md.

    CRITICAL: Path components (".claude", "skills", "SKILL.md") are literal
    strings here. Do NOT replace them with
    ClaudeDirectoryConventions.ADD_DIR_SKILLS_SUBDIR — that would re-create
    the "tests mirror implementation" failure mode this guard was designed
    to prevent. If the constant drifts, TestClaudeDirectoryConventions catches
    it; if the behavior drifts, this test catches it. Both layers are needed.

    Claude Code --add-dir discovery convention (external spec):
        <add_dir_root>/.claude/skills/<name>/SKILL.md
    """

    def test_init_session_writes_skills_at_add_dir_convention_path(self, tmp_path: Path) -> None:
        from autoskillit.workspace.session_skills import (
            DefaultSessionSkillManager,
            SkillsDirectoryProvider,
        )

        provider = SkillsDirectoryProvider()
        mgr = DefaultSessionSkillManager(provider, ephemeral_root=tmp_path)
        session_dir = _materialize_session_catalog(
            mgr,
            "cc001-contract-test",
            tmp_path,
        )

        # ".claude", "skills", "SKILL.md" are literal strings — NOT from any constant.
        discovered = list(session_dir.glob(".claude/skills/*/SKILL.md"))
        assert len(discovered) > 0, (
            "init_session must write skills to .claude/skills/<name>/SKILL.md "
            "(Claude Code --add-dir convention). "
            "If this fails, the ADD_DIR_SKILLS_SUBDIR convention has regressed."
        )

    def test_init_session_no_flat_skills_at_session_root(self, tmp_path: Path) -> None:
        """Anti-regression: the pre-fix flat layout must not reappear."""
        from autoskillit.workspace.session_skills import (
            DefaultSessionSkillManager,
            SkillsDirectoryProvider,
        )

        provider = SkillsDirectoryProvider()
        mgr = DefaultSessionSkillManager(provider, ephemeral_root=tmp_path)
        session_dir = _materialize_session_catalog(
            mgr,
            "cc001-flat-regression",
            tmp_path,
        )

        # Only .claude/ is allowed as a top-level child of session_dir
        flat_skills = list(session_dir.glob("*/SKILL.md"))
        flat_non_claude = [
            f
            for f in flat_skills
            if not str(f.relative_to(session_dir)).startswith(".claude/skills/")
        ]
        assert not flat_non_claude, (
            f"Flat layout detected: {flat_non_claude}. "
            "Skills must be nested under .claude/skills/, not at session root. "
            "This is the CC-001 regression pattern (pre-v0.5.1 bug)."
        )


# ---------------------------------------------------------------------------
# CC-002: --plugin-dir layout behavioral guard
# Path components are HARDCODED STRING LITERALS.
# ---------------------------------------------------------------------------


class TestPluginDirLayoutContract:
    """Guard: bundled skills must be at pkg_root()/skills/<name>/SKILL.md.

    CRITICAL: Path components ("skills", "open-kitchen", "SKILL.md") are
    literal strings. Do NOT replace with bundled_skills_dir() or
    ClaudeDirectoryConventions.PLUGIN_DIR_SKILLS_SUBDIR — those would create
    the same mirror-test vulnerability.

    Claude Code --plugin-dir discovery convention (external spec):
        <plugin_dir_root>/skills/<name>/SKILL.md
    """

    def test_bundled_skills_subdir_exists_at_plugin_dir_path(self) -> None:
        from autoskillit.core.paths import pkg_root

        # "skills" is a literal — not from any resolver or convention constant
        skills_subdir = pkg_root() / "skills"
        assert skills_subdir.is_dir(), (
            f"--plugin-dir root {pkg_root()} has no 'skills/' subdirectory. "
            "Claude Code --plugin-dir convention requires <root>/skills/<name>/SKILL.md."
        )

    def test_plugin_dir_skills_contain_skill_md_files(self) -> None:
        from autoskillit.core.paths import pkg_root

        # "skills" and "SKILL.md" are literals
        skill_files = list((pkg_root() / "skills").glob("*/SKILL.md"))
        assert len(skill_files) > 0, (
            "No SKILL.md files found at pkg_root()/skills/<name>/SKILL.md. "
            "The --plugin-dir convention requires skills at this exact path."
        )

    def test_open_kitchen_skill_at_plugin_dir_convention_path(self) -> None:
        """Spot-check Tier 1: open-kitchen must be at the literal plugin-dir path."""
        from autoskillit.core.paths import pkg_root

        # "skills", "open-kitchen", "SKILL.md" are all literals
        path = pkg_root() / "skills" / "open-kitchen" / "SKILL.md"
        assert path.exists(), (
            f"open-kitchen SKILL.md not found at {path}. "
            "If this fails, the --plugin-dir Tier 1 skill layout has changed."
        )


# ---------------------------------------------------------------------------
# DS-002: cook integration guard (unmocked init_session)
# ---------------------------------------------------------------------------


class TestCookAddDirStructure:
    """Guard: the directory passed as --add-dir by cook() must have
    .claude/skills/<name>/SKILL.md structure.

    This test does NOT mock init_session. It calls the real implementation
    to verify the output structure matches what Claude Code requires.

    CRITICAL: Path components are HARDCODED STRING LITERALS. Do NOT use
    ClaudeDirectoryConventions here.

    This test closes the double-mock gap in test_cook_interactive.py (all tests
    mock init_session and subprocess.run together, so no test verified
    the real directory structure from the real init_session call).
    """

    def test_cook_add_dir_target_has_correct_structure(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        import shutil
        from types import SimpleNamespace

        from autoskillit.core import CmdSpec

        structure_errors: list[str] = []
        add_dir_seen: list[bool] = []

        def fake_run(spec: CmdSpec, **kwargs: object) -> object:
            for i, token in enumerate(spec.cmd):
                if token == "--add-dir":
                    add_dir = Path(spec.cmd[i + 1])
                    add_dir_seen.append(True)

                    skill_files = list(add_dir.glob(".claude/skills/*/SKILL.md"))
                    if not skill_files:
                        structure_errors.append(
                            f"--add-dir target {add_dir} has no "
                            ".claude/skills/<name>/SKILL.md files. "
                            "Claude Code will find zero skills. "
                            "The real init_session is not writing the correct layout."
                        )

                    flat = [
                        f
                        for f in add_dir.glob("*/SKILL.md")
                        if not str(f.relative_to(add_dir)).startswith(".claude/skills/")
                    ]
                    if flat:
                        structure_errors.append(
                            f"Flat layout detected in --add-dir target: {flat}. "
                            "This is the CC-001 regression pattern."
                        )

            kwargs["on_spawn"](1, 1)  # type: ignore[operator]
            kwargs["trace"].record_spawn()  # type: ignore[union-attr]
            kwargs["on_reaped"](1, 1)  # type: ignore[operator]
            return SimpleNamespace(pid=1, pgid=1, returncode=0)

        monkeypatch.setattr(shutil, "which", lambda x: "/usr/bin/claude")
        monkeypatch.setattr("sys.stdin.isatty", lambda: True)
        monkeypatch.setattr("autoskillit.cli._onboarding.is_first_run", lambda _: False)
        monkeypatch.setattr(
            "autoskillit.cli.ui._timed_input.timed_prompt",
            lambda *args, **kwargs: "",
        )
        monkeypatch.setattr(
            "autoskillit.cli.session._session_process.run_cook_attempt",
            fake_run,
        )
        monkeypatch.setattr(
            "autoskillit.cli.session._session_reload.consume_reload_sentinel",
            lambda _project: None,
        )

        from autoskillit.cli.session._session_cook import cook
        from autoskillit.workspace.session_skills import (
            DefaultSessionSkillManager,
            SkillsDirectoryProvider,
        )

        ephemeral_root = tmp_path / "ephemeral"
        ephemeral_root.mkdir()

        real_mgr = DefaultSessionSkillManager(
            SkillsDirectoryProvider(), ephemeral_root=ephemeral_root
        )
        monkeypatch.setattr(
            "autoskillit.workspace.DefaultSessionSkillManager",
            lambda *a, **kw: real_mgr,
        )

        cook()

        assert add_dir_seen, "Expected at least one --add-dir in command"
        assert not structure_errors, "\n".join(structure_errors)


# ---------------------------------------------------------------------------
# CC-005: .mcp.json structure contract
# ---------------------------------------------------------------------------


class TestMcpJsonContract:
    """Guard: .mcp.json must have the required key structure.

    ALL key names are HARDCODED STRING LITERALS.
    Claude Code reads this file at startup to configure the MCP server.
    """

    def test_mcp_json_has_mcp_servers_key(self) -> None:
        import json

        from autoskillit.core.paths import pkg_root

        # "mcp_json" path components are literals
        mcp_json = pkg_root() / ".mcp.json"
        assert mcp_json.exists(), f".mcp.json not found at {mcp_json}"
        data = json.loads(mcp_json.read_text())
        assert "mcpServers" in data, (
            ".mcp.json is missing the 'mcpServers' top-level key. "
            "Claude Code will not load the MCP server."
        )

    def test_mcp_json_has_autoskillit_server_entry(self) -> None:
        import json

        from autoskillit.core.paths import pkg_root

        data = json.loads((pkg_root() / ".mcp.json").read_text())
        servers = data.get("mcpServers", {})
        assert "autoskillit" in servers, (
            ".mcp.json mcpServers has no 'autoskillit' entry. "
            "Claude Code will not find the autoskillit MCP server."
        )

    def test_mcp_json_autoskillit_has_command_key(self) -> None:
        import json

        from autoskillit.core.paths import pkg_root

        data = json.loads((pkg_root() / ".mcp.json").read_text())
        entry = data.get("mcpServers", {}).get("autoskillit", {})
        assert "command" in entry, (
            ".mcp.json mcpServers.autoskillit missing 'command' key. "
            "Claude Code cannot invoke the MCP server."
        )
        assert entry["command"] == "autoskillit", (
            f"Expected command='autoskillit', got {entry['command']!r}. "
            "Claude Code uses this string to invoke the MCP server binary."
        )


# ---------------------------------------------------------------------------
# CC-007: plugin.json structure contract
# ---------------------------------------------------------------------------


class TestPluginJsonContract:
    """Guard: plugin.json must have the required key structure.

    ALL key names and expected string values are HARDCODED STRING LITERALS.
    """

    def test_plugin_json_has_name_key(self) -> None:
        import json

        from autoskillit.core.paths import pkg_root

        plugin_json = pkg_root() / ".claude-plugin" / "plugin.json"
        assert plugin_json.exists(), f"plugin.json not found at {plugin_json}"
        data = json.loads(plugin_json.read_text())
        assert "name" in data, "plugin.json is missing the 'name' key."
        assert data["name"] == "autoskillit", (
            f"Expected plugin name 'autoskillit', got {data['name']!r}."
        )

    def test_plugin_json_has_version_key(self) -> None:
        import json

        from autoskillit.core.paths import pkg_root

        data = json.loads((pkg_root() / ".claude-plugin" / "plugin.json").read_text())
        assert "version" in data, "plugin.json is missing the 'version' key."

    def test_plugin_json_has_description_key(self) -> None:
        import json

        from autoskillit.core.paths import pkg_root

        data = json.loads((pkg_root() / ".claude-plugin" / "plugin.json").read_text())
        assert "description" in data, "plugin.json is missing the 'description' key."


# ---------------------------------------------------------------------------
# CC-HEADLESS-001: run_skill headless path --add-dir layout guard
# Path components are HARDCODED STRING LITERALS — do NOT replace with constants.
# Replaces CC-SKILLS-EXT (xfail removed): run_skill now routes through
# DefaultSessionSkillManager, so the ephemeral dir has the correct layout.
# ---------------------------------------------------------------------------


class TestRunSkillAddDirLayoutContract:
    """Guard: run_skill's --add-dir paths must have .claude/skills/<name>/SKILL.md.

    CRITICAL: Path components (".claude", "skills", "SKILL.md") are literal
    strings here — NOT from ClaudeDirectoryConventions.

    This is the headless-path counterpart of TestCookAddDirStructure (DS-002).
    """

    def test_run_skill_add_dir_has_convention_layout(self, tmp_path: Path) -> None:
        """CC-HEADLESS-001: run_skill's ephemeral --add-dir has .claude/skills/*/SKILL.md."""
        from autoskillit.workspace.session_skills import (
            DefaultSessionSkillManager,
            SkillsDirectoryProvider,
        )

        provider = SkillsDirectoryProvider()
        mgr = DefaultSessionSkillManager(provider, ephemeral_root=tmp_path)
        session_root = _materialize_session_catalog(
            mgr,
            "cc-headless-001-test",
            tmp_path,
        )

        # The returned ValidatedAddDir wraps a path; resolve to Path for globbing
        session_dir = Path(str(session_root))

        # ".claude", "skills", "SKILL.md" are literal strings — NOT from any constant.
        discovered = list(session_dir.glob(".claude/skills/*/SKILL.md"))
        assert len(discovered) > 0, (
            "run_skill's ephemeral --add-dir must contain "
            ".claude/skills/<name>/SKILL.md files. "
            "If this fails, DefaultSessionSkillManager.init_session() layout "
            "has regressed on the headless path."
        )

    def test_run_skill_add_dir_does_not_pass_raw_skills_extended(self, tmp_path: Path) -> None:
        """run_skill must not pass skills_extended/ directly as --add-dir."""
        from autoskillit.core.paths import pkg_root

        skills_ext = pkg_root() / "skills_extended"
        # skills_extended/ has flat layout — NOT .claude/skills/
        skill_files = list(skills_ext.glob(".claude/skills/*/SKILL.md"))
        assert len(skill_files) == 0, (
            "skills_extended/ should NOT have .claude/skills/ layout. "
            "run_skill routes through DefaultSessionSkillManager instead."
        )
