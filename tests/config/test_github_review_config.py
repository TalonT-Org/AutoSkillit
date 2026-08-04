"""Configuration contract for bounded GitHub review batches."""

from __future__ import annotations

import pytest

from autoskillit.config import AutomationConfig, load_config
from autoskillit.config._config_loader import _make_dynaconf
from autoskillit.config.settings import GitHubConfig

pytestmark = [pytest.mark.layer("config"), pytest.mark.small]


def test_review_comment_cap_has_safe_positive_default() -> None:
    assert GitHubConfig().review_comment_cap == 50
    assert AutomationConfig().github.review_comment_cap == 50


def test_review_comment_cap_loads_from_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AUTOSKILLIT_GITHUB__REVIEW_COMMENT_CAP", "17")
    config = AutomationConfig.from_dynaconf(_make_dynaconf())
    assert config.github.review_comment_cap == 17


def test_defaults_yaml_declares_review_comment_cap(tmp_path) -> None:
    assert load_config(tmp_path).github.review_comment_cap == 50


@pytest.mark.parametrize("invalid", [True, False, 0, -1, 1.5, "10", None])
def test_review_comment_cap_rejects_non_positive_or_non_integer_values(
    invalid: object,
) -> None:
    with pytest.raises(ValueError, match="review_comment_cap"):
        GitHubConfig(review_comment_cap=invalid)  # type: ignore[arg-type]


@pytest.mark.parametrize("valid", [1, 17, 50, 100])
def test_review_comment_cap_accepts_positive_integers(valid: int) -> None:
    assert GitHubConfig(review_comment_cap=valid).review_comment_cap == valid
