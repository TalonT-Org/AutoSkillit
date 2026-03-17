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
    strings here. Do NOT replace them with _SKILLS_SUBDIR or
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
        session_dir = mgr.init_session("cc001-contract-test", cook_session=True)

        # ".claude", "skills", "SKILL.md" are literal strings — NOT from any constant.
        discovered = list(session_dir.glob(".claude/skills/*/SKILL.md"))
        assert len(discovered) > 0, (
            "init_session must write skills to .claude/skills/<name>/SKILL.md "
            "(Claude Code --add-dir convention). "
            "If this fails, session_skills._SKILLS_SUBDIR has regressed."
        )

    def test_init_session_no_flat_skills_at_session_root(self, tmp_path: Path) -> None:
        """Anti-regression: the pre-fix flat layout must not reappear."""
        from autoskillit.workspace.session_skills import (
            DefaultSessionSkillManager,
            SkillsDirectoryProvider,
        )

        provider = SkillsDirectoryProvider()
        mgr = DefaultSessionSkillManager(provider, ephemeral_root=tmp_path)
        session_dir = mgr.init_session("cc001-flat-regression", cook_session=True)

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
# DS-002: chefs-hat integration guard (unmocked init_session)
# ---------------------------------------------------------------------------


class TestChefsHatAddDirStructure:
    """Guard: the directory passed as --add-dir by chefs_hat() must have
    .claude/skills/<name>/SKILL.md structure.

    This test does NOT mock init_session. It calls the real implementation
    to verify the output structure matches what Claude Code requires.

    CRITICAL: Path components are HARDCODED STRING LITERALS. Do NOT use
    ClaudeDirectoryConventions or _SKILLS_SUBDIR here.

    This test closes the double-mock gap in test_chefs_hat.py (all 7 tests
    mock init_session and subprocess.run together, so no test verified
    the real directory structure from the real init_session call).
    """

    def test_chefs_hat_add_dir_target_has_correct_structure(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        import shutil
        import subprocess

        # Structure checks happen INSIDE fake_run: chefs_hat() deletes skills_dir in
        # its finally block after subprocess.run() returns, so the directory is gone
        # by the time control returns to this test function.
        structure_errors: list[str] = []
        add_dir_seen: list[bool] = []

        def fake_run(cmd: list[str], **kw: object) -> object:
            for i, token in enumerate(cmd):
                if token == "--add-dir":
                    add_dir = Path(cmd[i + 1])
                    add_dir_seen.append(True)

                    # ".claude", "skills", "SKILL.md" are literal strings — not from any constant.
                    skill_files = list(add_dir.glob(".claude/skills/*/SKILL.md"))
                    if not skill_files:
                        structure_errors.append(
                            f"--add-dir target {add_dir} has no "
                            ".claude/skills/<name>/SKILL.md files. "
                            "Claude Code will find zero skills. "
                            "The real init_session is not writing the correct layout."
                        )

                    # Anti-regression: flat layout must not exist at the session root
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

            return type("R", (), {"returncode": 0})()

        monkeypatch.setattr(subprocess, "run", fake_run)
        monkeypatch.setattr(shutil, "which", lambda x: "/usr/bin/claude")
        # chefs_hat() calls input() to confirm launch — mock it to auto-confirm.
        monkeypatch.setattr("builtins.input", lambda _prompt="": "")

        from autoskillit.cli._chefs_hat import chefs_hat

        # Use a fixed ephemeral root so cleanup is deterministic
        from autoskillit.workspace.session_skills import (
            DefaultSessionSkillManager,
            SkillsDirectoryProvider,
        )

        ephemeral_root = tmp_path / "ephemeral"
        ephemeral_root.mkdir()

        real_mgr = DefaultSessionSkillManager(
            SkillsDirectoryProvider(), ephemeral_root=ephemeral_root
        )
        # Verified: _chefs_hat.py imports DefaultSessionSkillManager from
        # autoskillit.workspace inside the chefs_hat() function body (lazy import),
        # so patching autoskillit.workspace.DefaultSessionSkillManager intercepts it.
        # The spy below confirms real_mgr.init_session is actually called.
        init_session_calls: list[bool] = []
        original_init_session = real_mgr.init_session

        def _spy_init_session(*args: object, **kwargs: object) -> object:
            init_session_calls.append(True)
            return original_init_session(*args, **kwargs)

        monkeypatch.setattr(real_mgr, "init_session", _spy_init_session)
        monkeypatch.setattr(
            "autoskillit.workspace.DefaultSessionSkillManager",
            lambda *a, **kw: real_mgr,
        )

        chefs_hat()

        assert init_session_calls, (
            "real_mgr.init_session was never called — "
            "patch target 'autoskillit.workspace.DefaultSessionSkillManager' "
            "did not intercept chefs_hat()'s constructor call."
        )

        assert add_dir_seen, "Expected at least one --add-dir in command"
        assert not structure_errors, "\n".join(structure_errors)

    def test_chefs_hat_add_dir_excludes_tier1_skills(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Tier 1 skills (open-kitchen, close-kitchen) must NOT appear in --add-dir.

        They are already registered via --plugin-dir. Presence in --add-dir
        creates duplicate slash commands in the picker.
        """
        from autoskillit.workspace.session_skills import (
            DefaultSessionSkillManager,
            SkillsDirectoryProvider,
        )

        ephemeral_root = tmp_path / "ephemeral"
        ephemeral_root.mkdir()
        mgr = DefaultSessionSkillManager(SkillsDirectoryProvider(), ephemeral_root=ephemeral_root)
        skills_dir = mgr.init_session("ds002-tier1-check", cook_session=True)

        # "open-kitchen" and "close-kitchen" are literal strings — NOT from any constant.
        for tier1_name in ("open-kitchen", "close-kitchen"):
            assert not (skills_dir / ".claude" / "skills" / tier1_name / "SKILL.md").exists(), (
                f"Tier 1 skill '{tier1_name}' found in --add-dir ephemeral dir. "
                "It is already served by --plugin-dir and must NOT be duplicated."
            )


# ---------------------------------------------------------------------------
# CC-CHANNEL: Multi-channel skill uniqueness contract
# ---------------------------------------------------------------------------


class TestMultiChannelSkillUniqueness:
    """Guard: no skill name may appear in more than one discovery channel.

    Channel 1: --plugin-dir (BUNDLED skills)
    Channel 2: --add-dir (ephemeral session dir, written by init_session)
    Channel 3: CWD auto-discovery (project-local .claude/skills/)

    Overlap between any two channels produces duplicate slash commands.
    """

    @pytest.mark.parametrize("cook_session", [True, False], ids=["cook", "headless"])
    def test_multi_channel_uniqueness_contract(self, tmp_path: Path, cook_session: bool) -> None:
        from autoskillit.core.types import SkillSource
        from autoskillit.workspace.session_skills import (
            DefaultSessionSkillManager,
            SkillsDirectoryProvider,
        )
        from autoskillit.workspace.skills import (
            SkillResolver,
            detect_project_local_overrides,
        )

        # Set up a project dir with one override to exercise Channel 3
        project_dir = tmp_path / "project"
        (project_dir / ".claude" / "skills" / "investigate").mkdir(parents=True)
        (project_dir / ".claude" / "skills" / "investigate" / "SKILL.md").write_text("# custom")

        mgr = DefaultSessionSkillManager(SkillsDirectoryProvider(), tmp_path / "ephemeral")
        skills_dir = mgr.init_session(
            "chan-uniq", cook_session=cook_session, project_dir=project_dir
        )

        # Channel 1: BUNDLED skill names (served by --plugin-dir)
        resolver = SkillResolver()
        channel_1 = {s.name for s in resolver.list_all() if s.source == SkillSource.BUNDLED}

        # Channel 2: skills written to ephemeral dir
        skills_base = skills_dir / ".claude" / "skills"
        channel_2 = (
            {d.name for d in skills_base.iterdir() if d.is_dir()}
            if skills_base.is_dir()
            else set()
        )

        # Channel 3: project-local overrides
        channel_3 = set(detect_project_local_overrides(project_dir))

        overlap_1_2 = channel_1 & channel_2
        overlap_2_3 = channel_2 & channel_3

        assert not overlap_1_2, (
            f"Channel 1 (--plugin-dir) ∩ Channel 2 (--add-dir) overlap: {sorted(overlap_1_2)}"
        )
        assert not overlap_2_3, (
            f"Channel 2 (--add-dir) ∩ Channel 3 (CWD) overlap: {sorted(overlap_2_3)}"
        )


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

    This is the headless-path counterpart of TestChefsHatAddDirStructure (DS-002).
    """

    def test_run_skill_add_dir_has_convention_layout(self, tmp_path: Path) -> None:
        """CC-HEADLESS-001: run_skill's ephemeral --add-dir has .claude/skills/*/SKILL.md."""
        from autoskillit.workspace.session_skills import (
            DefaultSessionSkillManager,
            SkillsDirectoryProvider,
        )

        provider = SkillsDirectoryProvider()
        mgr = DefaultSessionSkillManager(provider, ephemeral_root=tmp_path)
        session_root = mgr.init_session("cc-headless-001-test", cook_session=False)

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
