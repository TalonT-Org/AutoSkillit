"""Integration tests for the factory -> guards contamination path.

Tests the two-stage contamination path that unit tests missed:
  1. set AUTOSKILLIT_PROVIDER_PROFILE / AUTOSKILLIT_PROJECT_DIR in env
  2. call make_context()
  3. assert the result is NOT contaminated by the ambient env var
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


def test_ambient_provider_profile_does_not_contaminate_tier4_default(
    monkeypatch, tmp_path
) -> None:
    """Ambient AUTOSKILLIT_PROVIDER_PROFILE must not reach default_provider.

    Primary assertion: checks the contamination target (the config object) directly.
    Secondary assertion: verifies Tier 4 downstream behavior of _resolve_provider_profile.
    """
    from autoskillit.config import AutomationConfig
    from autoskillit.server._factory import make_context
    from autoskillit.server._guards import _resolve_provider_profile

    monkeypatch.setenv("AUTOSKILLIT_PROVIDER_PROFILE", "minimax")
    config = AutomationConfig()
    config.providers.profiles = {"minimax": {"api_key_env": "MINIMAX_KEY"}}
    config.providers.default_provider = None

    ctx = make_context(config, runner=None, project_dir=tmp_path)

    assert ctx.config.providers.default_provider != "minimax", (
        "make_context() must not read AUTOSKILLIT_PROVIDER_PROFILE from os.environ — "
        "ambient env contaminated config.providers.default_provider"
    )

    result = _resolve_provider_profile("plan", "my_recipe", ctx.config.providers)
    assert result[0] != "minimax", "Tier 4 fallback must not use the ambient env profile"
    assert result == ("anthropic", {}), f"Expected ('anthropic', {{}}) but got {result!r}"


def test_ambient_project_dir_does_not_contaminate_context(monkeypatch, tmp_path) -> None:
    """AUTOSKILLIT_PROJECT_DIR in ambient env must not reach ctx.project_dir.

    make_context() must not read AUTOSKILLIT_PROJECT_DIR from os.environ.
    The CLI entry point reads it and passes it explicitly.
    """
    from autoskillit.config import AutomationConfig
    from autoskillit.server._factory import make_context

    monkeypatch.setenv("AUTOSKILLIT_PROJECT_DIR", "/tmp/wrong-project")
    config = AutomationConfig()

    ctx = make_context(config, runner=None, project_dir=tmp_path)

    assert ctx.project_dir != Path("/tmp/wrong-project"), (
        "make_context() must not read AUTOSKILLIT_PROJECT_DIR from os.environ — "
        "ambient env contaminated ctx.project_dir"
    )
