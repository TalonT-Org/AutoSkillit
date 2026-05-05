"""Tests for BranchingConfig and ReleaseReadinessConfig loading and validation."""

import pytest

pytestmark = [pytest.mark.layer("config"), pytest.mark.small]


class TestReleaseReadinessConfig:
    def test_branching_default_base_branch_is_main(self):
        from autoskillit.core.io import load_yaml
        from autoskillit.core.paths import pkg_root

        defaults = load_yaml(pkg_root() / "config" / "defaults.yaml")
        assert defaults["branching"]["default_base_branch"] == "main"


class TestBranchingConfig:
    def test_branching_config_default_base_branch_is_main(self) -> None:
        """BranchingConfig must default default_base_branch to 'main'."""
        from autoskillit.config.settings import BranchingConfig

        assert BranchingConfig().default_base_branch == "main"

    def test_automation_config_has_branching_field(self) -> None:
        """AutomationConfig must expose a BranchingConfig as .branching."""
        from autoskillit.config.settings import AutomationConfig

        cfg = AutomationConfig()
        assert cfg.branching.default_base_branch == "main"

    def test_branching_config_is_overridable(self) -> None:
        """BranchingConfig.default_base_branch must accept override values."""
        from autoskillit.config.settings import BranchingConfig

        cfg = BranchingConfig(default_base_branch="develop")
        assert cfg.default_base_branch == "develop"

    def test_branching_default_base_branch_matches_defaults_yaml(self) -> None:
        """BranchingConfig Python default must match defaults.yaml."""
        from autoskillit.config.settings import BranchingConfig
        from autoskillit.core.io import load_yaml
        from autoskillit.core.paths import pkg_root

        defaults = load_yaml(pkg_root() / "config" / "defaults.yaml")
        yaml_default = defaults["branching"]["default_base_branch"]
        python_default = BranchingConfig().default_base_branch

        assert python_default == yaml_default, (
            f"BranchingConfig.default_base_branch Python default ({python_default!r}) "
            f"disagrees with defaults.yaml ({yaml_default!r})"
        )

    def test_branching_config_promotion_target_defaults_to_main(self) -> None:
        """BranchingConfig.promotion_target defaults to main (package default)."""
        from autoskillit.config.settings import BranchingConfig

        assert BranchingConfig().promotion_target == "main"

    def test_automation_config_branching_promotion_target_default(self) -> None:
        """AutomationConfig propagates promotion_target default."""
        from autoskillit.config.settings import AutomationConfig

        assert AutomationConfig().branching.promotion_target == "main"

    def test_branching_config_promotion_target_overridable(self) -> None:
        """promotion_target can be set independently of default_base_branch."""
        from autoskillit.config.settings import BranchingConfig

        cfg = BranchingConfig(default_base_branch="develop", promotion_target="main")
        assert cfg.default_base_branch == "develop"
        assert cfg.promotion_target == "main"

    def test_branching_config_promotion_target_defaults_match_yaml(self, tmp_path) -> None:
        """Python default for promotion_target matches defaults.yaml."""
        from autoskillit.config import load_config
        from autoskillit.core.io import load_yaml
        from autoskillit.core.paths import pkg_root

        loaded = load_config(tmp_path / "settings.toml")
        defaults = load_yaml(pkg_root() / "config" / "defaults.yaml")
        expected = defaults["branching"]["promotion_target"]
        assert loaded.branching.promotion_target == expected

    def test_branching_config_promotion_target_env_var_override(
        self, monkeypatch, tmp_path
    ) -> None:
        """AUTOSKILLIT_BRANCHING__PROMOTION_TARGET env var overrides promotion_target."""
        from autoskillit.config import load_config

        monkeypatch.setenv("AUTOSKILLIT_BRANCHING__PROMOTION_TARGET", "stable")
        cfg = load_config(tmp_path)
        assert cfg.branching.promotion_target == "stable"
