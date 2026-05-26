"""Format gate tests for session_skills init_session and activate_with_deps.

Tests that skills with invalid SKILL.md frontmatter are rejected
during init_session and _activate_with_deps.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
import structlog.testing

from autoskillit.core import SkillSource
from autoskillit.workspace.session_skills import (
    DefaultSessionSkillManager,
    SkillsDirectoryProvider,
)
from tests._helpers import make_skills_config, make_subsetsconfig, make_test_config

pytestmark = [pytest.mark.layer("workspace"), pytest.mark.small]


class TestInitSessionFormatGate:
    def test_init_session_skips_skill_with_missing_description(self, tmp_path: Path) -> None:
        provider = MagicMock(spec=SkillsDirectoryProvider)
        skill_info = MagicMock()
        skill_info.name = "bad-skill"
        skill_info.source = SkillSource.BUNDLED_EXTENDED
        skill_info.categories = []
        provider.list_skills.return_value = [skill_info]
        provider.get_skill_content.return_value = "---\nname: bad-skill\n---\nBody"

        manager = DefaultSessionSkillManager(provider, tmp_path)
        config = make_test_config(
            skills=make_skills_config(tier1=[], tier2=[], tier3=[]),
            subsets=make_subsetsconfig(),
        )
        with structlog.testing.capture_logs() as captured:
            result = manager.init_session("sess1", config=config)

        skill_dir = Path(result.path) / ".claude" / "skills" / "bad-skill"
        assert not skill_dir.exists()
        assert provider.list_skills.called
        format_events = [e for e in captured if e.get("event") == "skill_format_validation"]
        assert any(e.get("skill") == "bad-skill" for e in format_events)

    def test_init_session_logs_warning_on_invalid_frontmatter(self, tmp_path: Path) -> None:
        provider = MagicMock(spec=SkillsDirectoryProvider)
        skill_info = MagicMock()
        skill_info.name = "bad-skill"
        skill_info.source = SkillSource.BUNDLED_EXTENDED
        skill_info.categories = []
        provider.list_skills.return_value = [skill_info]
        provider.get_skill_content.return_value = "---\nname: bad-skill\n---\nBody"

        manager = DefaultSessionSkillManager(provider, tmp_path)
        config = make_test_config(
            skills=make_skills_config(tier1=[], tier2=[], tier3=[]),
            subsets=make_subsetsconfig(),
        )
        with structlog.testing.capture_logs() as captured:
            manager.init_session("sess1", config=config)

        format_events = [e for e in captured if e.get("event") == "skill_format_validation"]
        assert len(format_events) >= 1
        assert any(e.get("skill") == "bad-skill" for e in format_events)

    def test_init_session_writes_skill_with_valid_frontmatter(self, tmp_path: Path) -> None:
        provider = MagicMock(spec=SkillsDirectoryProvider)
        skill_info = MagicMock()
        skill_info.name = "good-skill"
        skill_info.source = SkillSource.BUNDLED_EXTENDED
        skill_info.categories = []
        provider.list_skills.return_value = [skill_info]
        provider.get_skill_content.return_value = (
            "---\nname: good-skill\ndescription: A good skill\n---\nBody"
        )

        manager = DefaultSessionSkillManager(provider, tmp_path)
        config = make_test_config(
            skills=make_skills_config(tier1=[], tier2=[], tier3=[]),
            subsets=make_subsetsconfig(),
        )
        result = manager.init_session("sess1", config=config)

        skill_md = Path(result.path) / ".claude" / "skills" / "good-skill" / "SKILL.md"
        assert skill_md.exists()

    def test_init_session_format_rejected_does_not_trigger_allow_only_error(
        self, tmp_path: Path
    ) -> None:
        provider = MagicMock(spec=SkillsDirectoryProvider)
        skill_info = MagicMock()
        skill_info.name = "bad-skill"
        skill_info.source = SkillSource.BUNDLED_EXTENDED
        skill_info.categories = []
        provider.list_skills.return_value = [skill_info]
        provider.get_skill_content.return_value = "---\nname: bad-skill\n---\nBody"

        manager = DefaultSessionSkillManager(provider, tmp_path)
        config = make_test_config(
            skills=make_skills_config(tier1=[], tier2=[], tier3=[]),
            subsets=make_subsetsconfig(),
        )
        # Should not raise RuntimeError
        manager.init_session(
            "sess1",
            config=config,
            allow_only=frozenset({"bad-skill"}),
        )


class TestActivateWithDepsFormatGate:
    def test_activate_with_deps_skips_format_invalid_skill(self, tmp_path: Path) -> None:
        provider = MagicMock(spec=SkillsDirectoryProvider)
        skill_info = MagicMock()
        skill_info.name = "bad-skill"
        skill_info.source = SkillSource.BUNDLED_EXTENDED
        skill_info.categories = []
        provider.list_skills.return_value = [skill_info]
        provider.get_skill_content.return_value = "---\nname: bad-skill\n---\nBody"
        provider.resolver.resolve.return_value = skill_info

        manager = DefaultSessionSkillManager(provider, tmp_path)
        # Pre-create the session dir with no skills
        sess_dir = tmp_path / "sess1" / ".claude" / "skills"
        sess_dir.mkdir(parents=True)

        result = manager.activate_skill_deps("sess1", "bad-skill")
        assert result is False

    def test_activate_with_deps_logs_warning_on_invalid_frontmatter(self, tmp_path: Path) -> None:
        provider = MagicMock(spec=SkillsDirectoryProvider)
        skill_info = MagicMock()
        skill_info.name = "bad-skill"
        skill_info.source = SkillSource.BUNDLED_EXTENDED
        skill_info.categories = []
        provider.list_skills.return_value = [skill_info]
        provider.get_skill_content.return_value = "---\nname: bad-skill\n---\nBody"
        provider.resolver.resolve.return_value = skill_info

        manager = DefaultSessionSkillManager(provider, tmp_path)
        sess_dir = tmp_path / "sess1" / ".claude" / "skills"
        sess_dir.mkdir(parents=True)

        with structlog.testing.capture_logs() as captured:
            manager.activate_skill_deps("sess1", "bad-skill")

        format_events = [e for e in captured if e.get("event") == "skill_format_validation"]
        assert len(format_events) >= 1
        assert any(e.get("skill") == "bad-skill" for e in format_events)
