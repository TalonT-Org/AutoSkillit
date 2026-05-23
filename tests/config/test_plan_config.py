"""Tests for PlanConfig and adversarial_review_level wiring."""

from pathlib import Path

import pytest
import yaml

import autoskillit.config
from autoskillit.config import AutomationConfig, load_config

pytestmark = [pytest.mark.layer("config"), pytest.mark.small]


class TestPlanConfigDefaults:
    def test_plan_config_defaults(self) -> None:
        """T1.1: AutomationConfig().plan.adversarial_review_level == 'auto'."""
        cfg = AutomationConfig()
        assert cfg.plan.adversarial_review_level == "auto"


class TestPlanConfigFromYaml:
    def test_plan_config_from_yaml(self, tmp_path) -> None:
        """T1.2: YAML with plan.adversarial_review_level: full loads correctly."""
        config_dir = tmp_path / ".autoskillit"
        config_dir.mkdir()
        (config_dir / "config.yaml").write_text("plan:\n  adversarial_review_level: full\n")
        cfg = load_config(tmp_path)
        assert cfg.plan.adversarial_review_level == "full"

    def test_plan_config_env_var_override(self, tmp_path, monkeypatch) -> None:
        """T1.3: AUTOSKILLIT_PLAN__ADVERSARIAL_REVIEW_LEVEL env var overrides default."""
        config_dir = tmp_path / ".autoskillit"
        config_dir.mkdir()
        (config_dir / "config.yaml").write_text("plan:\n  adversarial_review_level: full\n")
        monkeypatch.setenv("AUTOSKILLIT_PLAN__ADVERSARIAL_REVIEW_LEVEL", "none")
        cfg = load_config(tmp_path)
        assert cfg.plan.adversarial_review_level == "none"


class TestPlanConfigDefaultsYaml:
    def test_plan_config_defaults_yaml_coherence(self) -> None:
        """T1.4: defaults.yaml plan.adversarial_review_level == 'auto'."""
        pkg_root = Path(autoskillit.config.__file__).parent
        defaults_file = pkg_root / "defaults.yaml"
        with open(defaults_file) as f:
            data = yaml.safe_load(f)
        assert data["plan"]["adversarial_review_level"] == "auto"


class TestPlanConfigValidation:
    def test_plan_config_rejects_invalid_level(self) -> None:
        """T1.5: PlanConfig rejects invalid adversarial_review_level values."""
        from autoskillit.config import PlanConfig

        with pytest.raises(ValueError, match="adversarial_review_level must be one of"):
            PlanConfig(adversarial_review_level="invalid")
