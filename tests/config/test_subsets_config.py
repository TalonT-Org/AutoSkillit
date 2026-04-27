"""Tests for SubsetsConfig loading and validation."""

from __future__ import annotations

import pytest

from autoskillit.config import load_config

pytestmark = [pytest.mark.layer("config"), pytest.mark.small]


class TestBuildSubsetsConfigCustomTagsValidation:
    """CC-F2: _build_subsets_config must raise ValueError for non-dict custom_tags."""

    @pytest.mark.parametrize("bad_value", [["list", "not", "dict"], "oops", 42])
    def test_build_subsets_config_raises_for_non_dict_custom_tags(self, bad_value: object) -> None:
        """Non-dict custom_tags must raise ValueError, not silently coerce to {}."""
        from autoskillit.config.settings import _build_subsets_config

        with pytest.raises(ValueError, match="custom_tags"):
            _build_subsets_config({"custom_tags": bad_value})

    def test_build_subsets_config_dict_custom_tags_accepted(self):
        """Valid dict custom_tags must not raise."""
        from autoskillit.config.settings import _build_subsets_config

        result = _build_subsets_config({"custom_tags": {"my_tag": ["skill-a"]}})
        assert result.custom_tags == {"my_tag": ["skill-a"]}

    def test_build_subsets_config_empty_dict_custom_tags_accepted(self):
        """Empty dict custom_tags must not raise."""
        from autoskillit.config.settings import _build_subsets_config

        result = _build_subsets_config({"custom_tags": {}})
        assert result.custom_tags == {}


class TestSubsetsConfig:
    # T1 — SubsetsConfig defaults

    def test_subsets_config_default_disabled_is_empty_list(self) -> None:
        from autoskillit.config import SubsetsConfig

        cfg = SubsetsConfig()
        assert cfg.disabled == []

    def test_subsets_config_default_custom_tags_is_empty_dict(self) -> None:
        from autoskillit.config import SubsetsConfig

        cfg = SubsetsConfig()
        assert cfg.custom_tags == {}

    def test_automation_config_has_subsets_field(self) -> None:
        from autoskillit.config import AutomationConfig, SubsetsConfig

        cfg = AutomationConfig()
        assert isinstance(cfg.subsets, SubsetsConfig)
        assert cfg.subsets.disabled == []
        assert cfg.subsets.custom_tags == {}

    # T2 — load_config with subsets.disabled

    def test_load_config_subsets_disabled(self, tmp_path) -> None:
        config_dir = tmp_path / ".autoskillit"
        config_dir.mkdir()
        (config_dir / "config.yaml").write_text("subsets:\n  disabled:\n    - github\n    - ci\n")
        cfg = load_config(tmp_path)
        assert cfg.subsets.disabled == ["github", "ci"]

    def test_load_config_subsets_disabled_absent_means_empty(self, tmp_path) -> None:
        cfg = load_config(tmp_path)
        assert cfg.subsets.disabled == []

    # T3 — load_config with subsets.custom_tags

    def test_load_config_subsets_custom_tags(self, tmp_path) -> None:
        config_dir = tmp_path / ".autoskillit"
        config_dir.mkdir()
        yaml_text = (
            "subsets:\n"
            "  custom_tags:\n"
            "    my-team-tools:\n"
            "      - investigate\n"
            "      - make-plan\n"
        )
        (config_dir / "config.yaml").write_text(yaml_text)
        cfg = load_config(tmp_path)
        assert cfg.subsets.custom_tags == {"my-team-tools": ["investigate", "make-plan"]}

    def test_load_config_subsets_custom_tags_absent_means_empty(self, tmp_path) -> None:
        cfg = load_config(tmp_path)
        assert cfg.subsets.custom_tags == {}

    # T4 — Unknown category warning, no crash

    def test_load_config_unknown_disabled_category_logs_warning_not_crash(self, tmp_path) -> None:
        import logging

        config_dir = tmp_path / ".autoskillit"
        config_dir.mkdir()
        (config_dir / "config.yaml").write_text(
            "subsets:\n  disabled:\n    - totally-unknown-category\n"
        )
        # Attach a handler directly to the logger to capture warnings reliably.
        # caplog is unreliable here because the structlog capture_logs() autouse
        # fixture can intercept the handler chain under xdist worker ordering.
        captured: list[logging.LogRecord] = []
        handler = logging.Handler()
        handler.emit = captured.append  # type: ignore[assignment]
        logger = logging.getLogger("autoskillit.config.settings")  # noqa: TID251
        logger.addHandler(handler)
        try:
            cfg = load_config(tmp_path)
        finally:
            logger.removeHandler(handler)
        assert cfg.subsets.disabled == ["totally-unknown-category"]  # preserved as-is
        assert any("totally-unknown-category" in r.getMessage() for r in captured)

    def test_load_config_known_disabled_category_no_warning(self, tmp_path, caplog) -> None:
        import logging

        config_dir = tmp_path / ".autoskillit"
        config_dir.mkdir()
        (config_dir / "config.yaml").write_text("subsets:\n  disabled:\n    - github\n")
        with caplog.at_level(logging.WARNING):
            load_config(tmp_path)
        assert not any("github" in r.message for r in caplog.records)

    def test_load_config_custom_tag_in_disabled_is_valid(self, tmp_path, caplog) -> None:
        """Custom tags defined in custom_tags can also appear in disabled."""
        import logging

        config_dir = tmp_path / ".autoskillit"
        config_dir.mkdir()
        yaml_text = (
            "subsets:\n"
            "  disabled:\n"
            "    - experimental\n"
            "  custom_tags:\n"
            "    experimental:\n"
            "      - write-recipe\n"
        )
        (config_dir / "config.yaml").write_text(yaml_text)
        with caplog.at_level(logging.WARNING):
            load_config(tmp_path)
        assert not any("experimental" in r.message for r in caplog.records)

    # T5 — SubsetsConfig exported from config package

    def test_subsets_config_importable_from_config_package(self) -> None:
        from autoskillit.config import SubsetsConfig

        cfg = SubsetsConfig()
        assert hasattr(cfg, "disabled")
        assert hasattr(cfg, "custom_tags")
