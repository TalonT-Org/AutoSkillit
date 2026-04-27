"""Tests for SkillsConfig loading and validation."""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.layer("config"), pytest.mark.small]


class TestSkillsConfig:
    """SkillsConfig dataclass, tier duplication validation, and AutomationConfig integration."""

    def test_skills_config_dataclass(self) -> None:
        """SkillsConfig has tier1, tier2, tier3 list[str] fields."""
        from autoskillit.config.settings import SkillsConfig

        sc = SkillsConfig(tier1=["a"], tier2=["b"], tier3=["c"])
        assert sc.tier1 == ["a"] and sc.tier2 == ["b"] and sc.tier3 == ["c"]

    def test_skills_config_tier_duplication_raises(self) -> None:
        """Skill in multiple tiers raises ValueError at construction (REQ-TIER-009)."""
        from autoskillit.config.settings import SkillsConfig

        with pytest.raises(ValueError, match="multiple tiers"):
            SkillsConfig(tier1=["open-kitchen"], tier2=["open-kitchen"], tier3=[])

    def test_automation_config_has_skills_field(self) -> None:
        """AutomationConfig.skills is a SkillsConfig with tier lists."""
        from autoskillit.config.settings import AutomationConfig, SkillsConfig

        cfg = AutomationConfig()
        assert isinstance(cfg.skills, SkillsConfig)

    def test_defaults_yaml_skills_section(self) -> None:
        """defaults.yaml has non-empty skills.tier1/tier2/tier3 lists."""
        from autoskillit.core import load_yaml, pkg_root

        defaults = load_yaml(pkg_root() / "config" / "defaults.yaml")
        assert isinstance(defaults, dict)
        skills = defaults.get("skills", {})
        assert "open-kitchen" in skills.get("tier1", [])
        assert len(skills.get("tier2", [])) >= 20
        assert len(skills.get("tier3", [])) >= 10

    def test_load_config_populates_skills_tiers(self, tmp_path) -> None:
        """load_config() produces an AutomationConfig with tier assignments from defaults."""
        from autoskillit.config import load_config

        cfg = load_config(tmp_path)
        assert "open-kitchen" in cfg.skills.tier1
        assert "make-plan" in cfg.skills.tier2
        assert "compose-pr" in cfg.skills.tier3

    def test_skills_config_exported_from_config_package(self) -> None:
        """SkillsConfig is importable from autoskillit.config and has expected fields."""
        from autoskillit.config import SkillsConfig

        cfg = SkillsConfig()
        assert hasattr(cfg, "tier1")
        assert hasattr(cfg, "tier2")
        assert hasattr(cfg, "tier3")
