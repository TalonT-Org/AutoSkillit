"""Backend session layout validation tests."""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]


class TestClaudeCodeLayoutValidation:
    def test_claude_code_valid_layout_returns_empty(self, tmp_path):
        from autoskillit.execution.backends.claude import ClaudeCodeBackend

        skills_dir = tmp_path / ".claude" / "skills"
        skills_dir.mkdir(parents=True)
        (skills_dir / "some-skill").mkdir()
        (skills_dir / "some-skill" / "SKILL.md").write_text("# Some Skill")

        backend = ClaudeCodeBackend()
        errors = backend.validate_session_layout(tmp_path)
        assert errors == []

    def test_claude_code_missing_skills_dir_returns_error(self, tmp_path):
        from autoskillit.execution.backends.claude import ClaudeCodeBackend

        backend = ClaudeCodeBackend()
        errors = backend.validate_session_layout(tmp_path)
        assert len(errors) > 0
        assert any("does not exist" in e for e in errors)

    def test_claude_code_empty_skills_dir_returns_error(self, tmp_path):
        from autoskillit.execution.backends.claude import ClaudeCodeBackend

        skills_dir = tmp_path / ".claude" / "skills"
        skills_dir.mkdir(parents=True)

        backend = ClaudeCodeBackend()
        errors = backend.validate_session_layout(tmp_path)
        assert len(errors) > 0
        assert any("empty" in e for e in errors)

    def test_claude_code_bundled_skill_present_returns_error(self, tmp_path):
        from autoskillit.core import SkillSource
        from autoskillit.execution.backends.claude import ClaudeCodeBackend
        from autoskillit.workspace.skills import DefaultSkillResolver

        skills_dir = tmp_path / ".claude" / "skills"
        skills_dir.mkdir(parents=True)

        resolver = DefaultSkillResolver()
        bundled_skills = [s for s in resolver.list_all() if s.source == SkillSource.BUNDLED]
        if not bundled_skills:
            pytest.skip("No bundled skills available")

        skill_name = bundled_skills[0].name
        skill_path = skills_dir / skill_name
        skill_path.mkdir()
        (skill_path / "SKILL.md").write_text("# Bundled Skill")

        backend = ClaudeCodeBackend()
        errors = backend.validate_session_layout(tmp_path)
        assert any("BUNDLED" in e and skill_name in e for e in errors)


class TestCodexLayoutValidation:
    def test_codex_valid_layout_returns_empty(self, tmp_path):
        from autoskillit.execution.backends.codex import CodexBackend

        skills_dir = tmp_path / "skills"
        skills_dir.mkdir(parents=True)
        (skills_dir / "some-skill").mkdir()
        config_content = "[mcp_servers.autoskillit]\nname = 'autoskillit'\n"
        (tmp_path / "config.toml").write_text(config_content)
        auth_target = tmp_path / "auth-source.json"
        auth_target.write_text("{}")
        (tmp_path / "auth.json").symlink_to(auth_target)
        sessions_target = tmp_path / "sessions-target"
        sessions_target.mkdir()
        (tmp_path / "sessions").symlink_to(sessions_target)

        backend = CodexBackend()
        errors = backend.validate_session_layout(tmp_path)
        assert errors == []

    def test_codex_missing_skills_dir_returns_error(self, tmp_path):
        from autoskillit.execution.backends.codex import CodexBackend

        backend = CodexBackend()
        errors = backend.validate_session_layout(tmp_path)
        assert len(errors) > 0
        assert any("does not exist" in e for e in errors)

    def test_codex_empty_skills_dir_returns_error(self, tmp_path):
        from autoskillit.execution.backends.codex import CodexBackend

        skills_dir = tmp_path / "skills"
        skills_dir.mkdir(parents=True)

        backend = CodexBackend()
        errors = backend.validate_session_layout(tmp_path)
        assert len(errors) > 0
        assert any("empty" in e for e in errors)

    def test_codex_missing_config_toml_returns_error(self, tmp_path):
        from autoskillit.execution.backends.codex import CodexBackend

        skills_dir = tmp_path / "skills"
        skills_dir.mkdir(parents=True)

        backend = CodexBackend()
        errors = backend.validate_session_layout(tmp_path)
        assert len(errors) > 0
        assert any("config.toml" in e for e in errors)

    def test_codex_config_toml_missing_mcp_section_returns_error(self, tmp_path):
        from autoskillit.execution.backends.codex import CodexBackend

        skills_dir = tmp_path / "skills"
        skills_dir.mkdir(parents=True)
        (tmp_path / "config.toml").write_text("[other_section]\nkey = 'value'\n")

        backend = CodexBackend()
        errors = backend.validate_session_layout(tmp_path)
        assert len(errors) > 0
        assert any("[mcp_servers.autoskillit]" in e for e in errors)

    def test_codex_auth_json_regular_file_returns_error(self, tmp_path):
        from autoskillit.execution.backends.codex import CodexBackend

        skills_dir = tmp_path / "skills"
        skills_dir.mkdir(parents=True)
        (tmp_path / "config.toml").write_text("[mcp_servers.autoskillit]\n")
        (tmp_path / "auth.json").write_text("{}")

        backend = CodexBackend()
        errors = backend.validate_session_layout(tmp_path)
        assert len(errors) > 0
        assert any("symlink" in e and "auth.json" in e for e in errors)

    def test_codex_sessions_regular_dir_returns_error(self, tmp_path):
        from autoskillit.execution.backends.codex import CodexBackend

        skills_dir = tmp_path / "skills"
        skills_dir.mkdir(parents=True)
        (tmp_path / "config.toml").write_text("[mcp_servers.autoskillit]\n")
        (tmp_path / "sessions").mkdir()

        backend = CodexBackend()
        errors = backend.validate_session_layout(tmp_path)
        assert len(errors) > 0
        assert any("symlink" in e and "sessions" in e for e in errors)

    def test_codex_sessions_absent_is_ok(self, tmp_path):
        from autoskillit.execution.backends.codex import CodexBackend

        skills_dir = tmp_path / "skills"
        skills_dir.mkdir(parents=True)
        (tmp_path / "config.toml").write_text("[mcp_servers.autoskillit]\n")

        backend = CodexBackend()
        errors = backend.validate_session_layout(tmp_path)
        assert not any("sessions" in e and "symlink" in e for e in errors)
