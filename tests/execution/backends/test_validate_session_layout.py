"""Backend session layout validation tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.core import SESSION_ADD_DIR_SUBDIR

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]


def _write_codex_catalog(session_home: Path, *, skill_name: str = "some-skill") -> Path:
    catalog = session_home / SESSION_ADD_DIR_SUBDIR / "skills"
    skill_dir = catalog / skill_name
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("# Skill\n")
    return catalog


class TestClaudeCodeLayoutValidation:
    def test_claude_conventions_have_no_profile_skills_source(self):
        from autoskillit.execution.backends.claude import ClaudeCodeBackend

        assert ClaudeCodeBackend().conventions.profile_skills_source is None

    def test_claude_code_valid_layout_returns_empty(self, tmp_path):
        from autoskillit.execution.backends.claude import ClaudeCodeBackend

        skills_dir = tmp_path / SESSION_ADD_DIR_SUBDIR / ".claude" / "skills"
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

        skills_dir = tmp_path / SESSION_ADD_DIR_SUBDIR / ".claude" / "skills"
        skills_dir.mkdir(parents=True)

        backend = ClaudeCodeBackend()
        errors = backend.validate_session_layout(tmp_path)
        assert len(errors) > 0
        assert any("empty" in e for e in errors)

    def test_claude_code_bundled_skill_present_returns_error(self, tmp_path):
        from autoskillit.core import SkillSource
        from autoskillit.execution.backends.claude import ClaudeCodeBackend
        from autoskillit.workspace.skills import DefaultSkillResolver

        skills_dir = tmp_path / SESSION_ADD_DIR_SUBDIR / ".claude" / "skills"
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
    def test_codex_catalog_relative_path(self):
        from autoskillit.execution.backends.codex import CodexBackend

        assert SESSION_ADD_DIR_SUBDIR == "add-dir"
        assert str(Path(SESSION_ADD_DIR_SUBDIR) / CodexBackend().conventions.skills_subdir) == (
            "add-dir/skills"
        )

    def test_codex_conventions_expose_the_injected_profile_skills_source(self, tmp_path):
        from autoskillit.execution.backends.codex import CodexBackend

        source_home = tmp_path / "source-codex-home"

        assert (
            CodexBackend(source_codex_home=source_home).conventions.profile_skills_source
            == source_home / "skills"
        )

    def test_codex_valid_layout_returns_empty(self, tmp_path):
        from autoskillit.execution.backends.codex import CodexBackend

        _write_codex_catalog(tmp_path)
        (tmp_path / "skills").symlink_to("add-dir/skills")
        config_content = "[mcp_servers.autoskillit]\nname = 'autoskillit'\n"
        (tmp_path / "config.toml").write_text(config_content)
        auth_target = tmp_path / "auth-source.json"
        auth_target.write_text("{}")
        (tmp_path / "auth.json").symlink_to(auth_target)
        sessions_target = tmp_path / "sessions-target"
        sessions_target.mkdir()
        (tmp_path / "sessions").symlink_to(sessions_target)
        archived_target = tmp_path / "archived-sessions-target"
        archived_target.mkdir()
        (tmp_path / "archived_sessions").symlink_to(archived_target)

        backend = CodexBackend()
        errors = backend.validate_session_layout(tmp_path, project_dir=tmp_path)
        assert errors == []

    def test_codex_layout_rejects_real_discovery_directory(self, tmp_path):
        from autoskillit.execution.backends.codex import CodexBackend

        catalog = _write_codex_catalog(tmp_path)
        discovery = tmp_path / "skills"
        discovery.mkdir()
        (discovery / "some-skill").symlink_to(catalog / "some-skill")

        errors = CodexBackend().validate_session_layout(tmp_path)

        assert any("skills must be a symlink" in error for error in errors)

    def test_codex_layout_rejects_missing_discovery_alias(self, tmp_path):
        from autoskillit.execution.backends.codex import CodexBackend

        _write_codex_catalog(tmp_path)

        errors = CodexBackend().validate_session_layout(tmp_path)

        assert any("skills must be a symlink" in error for error in errors)

    def test_codex_layout_rejects_wrong_discovery_alias(self, tmp_path):
        from autoskillit.execution.backends.codex import CodexBackend

        catalog = _write_codex_catalog(tmp_path)
        (tmp_path / "skills").symlink_to(catalog)

        errors = CodexBackend().validate_session_layout(tmp_path)

        assert any("add-dir/skills" in error for error in errors)

    def test_codex_layout_rejects_catalog_with_only_native_system_skills(self, tmp_path):
        from autoskillit.execution.backends.codex import CodexBackend

        native_skill = tmp_path / SESSION_ADD_DIR_SUBDIR / "skills" / ".system" / "native"
        native_skill.mkdir(parents=True)
        (native_skill / "SKILL.md").write_text("# Native Skill\n")
        (tmp_path / "skills").symlink_to("add-dir/skills")

        errors = CodexBackend().validate_session_layout(tmp_path)

        assert any("catalog has no managed skills" in error for error in errors)

    def test_codex_layout_rejects_symlinked_managed_catalog_entry(self, tmp_path):
        from autoskillit.execution.backends.codex import CodexBackend

        catalog = _write_codex_catalog(tmp_path)
        (catalog / "linked-skill").symlink_to(catalog / "some-skill")
        (tmp_path / "skills").symlink_to("add-dir/skills")

        errors = CodexBackend().validate_session_layout(tmp_path)

        assert any("real skill directories" in error for error in errors)

    def test_codex_missing_skills_dir_returns_error(self, tmp_path):
        from autoskillit.execution.backends.codex import CodexBackend

        backend = CodexBackend()
        errors = backend.validate_session_layout(tmp_path)
        assert len(errors) > 0
        assert any("catalog" in e for e in errors)

    def test_codex_empty_skills_dir_returns_error(self, tmp_path):
        from autoskillit.execution.backends.codex import CodexBackend

        skills_dir = tmp_path / SESSION_ADD_DIR_SUBDIR / "skills"
        skills_dir.mkdir(parents=True)

        backend = CodexBackend()
        errors = backend.validate_session_layout(tmp_path)
        assert len(errors) > 0
        assert any("catalog has no managed skills" in e for e in errors)

    def test_codex_missing_config_toml_returns_error(self, tmp_path):
        from autoskillit.execution.backends.codex import CodexBackend

        skills_dir = tmp_path / SESSION_ADD_DIR_SUBDIR / "skills"
        skills_dir.mkdir(parents=True)
        (skills_dir / "some-skill").mkdir()

        backend = CodexBackend()
        errors = backend.validate_session_layout(tmp_path)
        assert len(errors) > 0
        assert any("config.toml" in e for e in errors)

    def test_codex_config_toml_missing_mcp_section_returns_error(self, tmp_path):
        from autoskillit.execution.backends.codex import CodexBackend

        skills_dir = tmp_path / SESSION_ADD_DIR_SUBDIR / "skills"
        skills_dir.mkdir(parents=True)
        (tmp_path / "config.toml").write_text("[other_section]\nkey = 'value'\n")

        backend = CodexBackend()
        errors = backend.validate_session_layout(tmp_path)
        assert len(errors) > 0
        assert any("[mcp_servers.autoskillit]" in e for e in errors)

    def test_codex_auth_json_regular_file_returns_error(self, tmp_path):
        from autoskillit.execution.backends.codex import CodexBackend

        skills_dir = tmp_path / SESSION_ADD_DIR_SUBDIR / "skills"
        skills_dir.mkdir(parents=True)
        (tmp_path / "config.toml").write_text("[mcp_servers.autoskillit]\n")
        (tmp_path / "auth.json").write_text("{}")

        backend = CodexBackend()
        errors = backend.validate_session_layout(tmp_path)
        assert len(errors) > 0
        assert any("symlink" in e and "auth.json" in e for e in errors)

    def test_codex_sessions_regular_dir_returns_error(self, tmp_path):
        from autoskillit.execution.backends.codex import CodexBackend

        skills_dir = tmp_path / SESSION_ADD_DIR_SUBDIR / "skills"
        skills_dir.mkdir(parents=True)
        (tmp_path / "config.toml").write_text("[mcp_servers.autoskillit]\n")
        (tmp_path / "sessions").mkdir()

        backend = CodexBackend()
        errors = backend.validate_session_layout(tmp_path)
        assert len(errors) > 0
        assert any("symlink" in e and "sessions" in e for e in errors)

    def test_codex_sessions_absent_returns_error(self, tmp_path):
        from autoskillit.execution.backends.codex import CodexBackend

        skills_dir = tmp_path / SESSION_ADD_DIR_SUBDIR / "skills"
        skills_dir.mkdir(parents=True)
        (tmp_path / "config.toml").write_text("[mcp_servers.autoskillit]\n")

        backend = CodexBackend()
        errors = backend.validate_session_layout(tmp_path)
        assert any("sessions" in e and ("missing" in e or "symlink" in e) for e in errors)

    def test_codex_layout_validation_never_runs_native_probe(self, tmp_path, monkeypatch):
        import subprocess

        from autoskillit.execution.backends.codex import CodexBackend

        _write_codex_catalog(tmp_path)
        (tmp_path / "skills").symlink_to("add-dir/skills")
        (tmp_path / "config.toml").write_text("[mcp_servers.autoskillit]\n")
        for name in ("sessions", "archived_sessions"):
            target = tmp_path / f".inert-{name}"
            target.mkdir()
            (tmp_path / name).symlink_to(target)

        def fail_if_called(*args, **kwargs):
            raise AssertionError("layout validation must be filesystem-only")

        monkeypatch.setattr(subprocess, "run", fail_if_called)

        assert CodexBackend().validate_session_layout(tmp_path, project_dir=tmp_path) == []

    @pytest.mark.parametrize("public_name", ["sessions", "archived_sessions"])
    def test_codex_layout_rejects_rollout_link_outside_generated_home(self, tmp_path, public_name):
        from autoskillit.execution.backends.codex import CodexBackend

        generated_home = tmp_path / "generated-home"
        skills_dir = generated_home / SESSION_ADD_DIR_SUBDIR / "skills"
        skills_dir.mkdir(parents=True)
        (skills_dir / "some-skill").mkdir()
        (generated_home / "config.toml").write_text("[mcp_servers.autoskillit]\n")
        external_store = tmp_path / "canonical" / public_name
        external_store.mkdir(parents=True)

        for name in ("sessions", "archived_sessions"):
            target = external_store if name == public_name else generated_home / f".inert-{name}"
            target.mkdir(exist_ok=True)
            (generated_home / name).symlink_to(target)

        errors = CodexBackend().validate_session_layout(generated_home, project_dir=tmp_path)

        assert any(public_name in error and "generated home" in error for error in errors)

    @pytest.mark.parametrize("public_name", ["sessions", "archived_sessions"])
    def test_codex_layout_rejects_nonempty_inert_rollout_target(self, tmp_path, public_name):
        from autoskillit.execution.backends.codex import CodexBackend

        skills_dir = tmp_path / SESSION_ADD_DIR_SUBDIR / "skills"
        skills_dir.mkdir(parents=True)
        (skills_dir / "some-skill").mkdir()
        (tmp_path / "config.toml").write_text("[mcp_servers.autoskillit]\n")
        for name in ("sessions", "archived_sessions"):
            target = tmp_path / f".inert-{name}"
            target.mkdir()
            (tmp_path / name).symlink_to(target)
        (tmp_path / f".inert-{public_name}" / "unexpected.jsonl").write_text("{}")

        errors = CodexBackend().validate_session_layout(tmp_path, project_dir=tmp_path)

        assert any(public_name in error and "empty" in error for error in errors)

    def test_codex_profile_only_discovery_root_is_a_valid_session_layout(self, tmp_path):
        from autoskillit.execution.backends.codex import CodexBackend

        profile_skill = (
            _write_codex_catalog(tmp_path, skill_name="my-profile-skill") / "my-profile-skill"
        )
        (profile_skill / "SKILL.md").write_text(
            "---\nname: my-profile-skill\ndescription: Profile skill.\n---\n# MY PROFILE SKILL\n"
        )
        (tmp_path / "skills").symlink_to("add-dir/skills")
        (tmp_path / "config.toml").write_text("[mcp_servers.autoskillit]\n")
        for name in ("sessions", "archived_sessions"):
            target = tmp_path / f".inert-{name}"
            target.mkdir()
            (tmp_path / name).symlink_to(target.name)

        assert CodexBackend().validate_session_layout(tmp_path, project_dir=tmp_path) == []
