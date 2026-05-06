"""Tests for ReviewConfig and local_review_rounds wiring."""

from pathlib import Path

import pytest
import yaml

import autoskillit.config
from autoskillit.config import AutomationConfig, load_config

pytestmark = [pytest.mark.layer("config"), pytest.mark.small]


class TestReviewConfigDefaults:
    def test_review_config_defaults(self) -> None:
        """T1.1: AutomationConfig().review.local_review_rounds == 3."""
        cfg = AutomationConfig()
        assert cfg.review.local_review_rounds == 3


class TestReviewConfigFromYaml:
    def test_review_config_from_yaml(self, tmp_path) -> None:
        """T1.2: YAML with review.local_review_rounds: 5 loads correctly."""
        config_dir = tmp_path / ".autoskillit"
        config_dir.mkdir()
        (config_dir / "config.yaml").write_text("review:\n  local_review_rounds: 5\n")
        cfg = load_config(tmp_path)
        assert cfg.review.local_review_rounds == 5

    def test_review_config_env_var_override(self, tmp_path, monkeypatch) -> None:
        """T1.3: AUTOSKILLIT_REVIEW__LOCAL_REVIEW_ROUNDS env var overrides default."""
        config_dir = tmp_path / ".autoskillit"
        config_dir.mkdir()
        (config_dir / "config.yaml").write_text("review:\n  local_review_rounds: 5\n")
        monkeypatch.setenv("AUTOSKILLIT_REVIEW__LOCAL_REVIEW_ROUNDS", "0")
        cfg = load_config(tmp_path)
        assert cfg.review.local_review_rounds == 0


class TestReviewConfigDefaultsYaml:
    def test_review_config_defaults_yaml_coherence(self) -> None:
        """T1.4: defaults.yaml review.local_review_rounds == 3."""
        pkg_root = Path(autoskillit.config.__file__).parent
        defaults_file = pkg_root / "defaults.yaml"
        with open(defaults_file) as f:
            data = yaml.safe_load(f)
        assert data["review"]["local_review_rounds"] == 3
