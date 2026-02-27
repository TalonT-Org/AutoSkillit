"""Tests for headless_runner.py extracted helpers."""

import pytest

from autoskillit.config import AutomationConfig, ModelConfig


@pytest.fixture
def make_config():
    """Factory fixture that creates an AutomationConfig with custom model settings."""

    def _make(model_override=None, model_default=None):
        cfg = AutomationConfig()
        cfg.model = ModelConfig(default=model_default, override=model_override)
        return cfg

    return _make


def test_ensure_skill_prefix_prepends_use_for_slash_commands():
    from autoskillit.headless_runner import _ensure_skill_prefix

    assert _ensure_skill_prefix("/investigate foo") == "Use /investigate foo"


def test_ensure_skill_prefix_leaves_plain_text_unchanged():
    from autoskillit.headless_runner import _ensure_skill_prefix

    assert _ensure_skill_prefix("just a plain prompt") == "just a plain prompt"


def test_inject_completion_directive_appends_marker():
    from autoskillit.headless_runner import _inject_completion_directive

    result = _inject_completion_directive("/investigate foo", "%%DONE%%")
    assert result.endswith("%%DONE%%")
    assert "/investigate foo" in result


def test_resolve_model_prefers_override(make_config):
    from autoskillit.headless_runner import _resolve_model

    cfg = make_config(model_override="opus")
    assert _resolve_model("sonnet", cfg) == "opus"


def test_resolve_model_uses_step_model_when_no_override(make_config):
    from autoskillit.headless_runner import _resolve_model

    cfg = make_config(model_override=None, model_default=None)
    assert _resolve_model("haiku", cfg) == "haiku"


def test_resolve_model_uses_config_default_when_step_empty(make_config):
    from autoskillit.headless_runner import _resolve_model

    cfg = make_config(model_override=None, model_default="sonnet")
    assert _resolve_model("", cfg) == "sonnet"


def test_resolve_model_returns_none_when_all_empty(make_config):
    from autoskillit.headless_runner import _resolve_model

    cfg = make_config(model_override=None, model_default=None)
    assert _resolve_model("", cfg) is None
