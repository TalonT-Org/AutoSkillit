"""Env-layer key validation (#4707, S12).

Before S12, a misspelled nested environment variable
(``AUTOSKILLIT_MODEL__MODEL_OVERRIDE``) was silently dropped —
``_YAML_KEY_ALIASES`` maps the correct spelling (``AUTOSKILLIT_MODEL__OVERRIDE``)
to ``model.override``, and Dynaconf simply never read the misspelled key.
``validate_env_layer_keys`` closes the same bug class the rest of this plan
closes for ``run_skill`` parameter names, applied to the configuration layer:
a spelling an operator reasonably believes is accepted, never read, with no
error.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from autoskillit.config import AutomationConfig, load_config
from autoskillit.config.settings import (
    _CONFIG_SCHEMA,
    _YAML_KEY_ALIASES,
    ConfigSchemaError,
    validate_env_layer_keys,
)
from autoskillit.core import AUTOSKILLIT_PRIVATE_ENV_VARS

pytestmark = [pytest.mark.layer("config"), pytest.mark.medium]


def test_unknown_nested_env_key_raises_with_hint(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reproduces the correction notice: the issue's named variable spelling
    is non-functional, and must now fail loudly rather than silently."""
    monkeypatch.setenv("AUTOSKILLIT_MODEL__MODEL_OVERRIDE", "zzz")
    with pytest.raises(ConfigSchemaError) as excinfo:
        validate_env_layer_keys()
    message = str(excinfo.value)
    assert "model.model_override" in message
    assert "override" in message


def test_unknown_env_section_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTOSKILLIT_NOT_A_REAL_SECTION__SOMEKEY", "zzz")
    with pytest.raises(ConfigSchemaError, match="not_a_real_section"):
        validate_env_layer_keys()


@pytest.mark.parametrize(("section", "field_name"), sorted(_YAML_KEY_ALIASES))
def test_every_alias_spelling_behaves(
    section: str, field_name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The YAML spelling is accepted; the Python field spelling is rejected."""
    yaml_key = _YAML_KEY_ALIASES[(section, field_name)]

    monkeypatch.setenv(f"AUTOSKILLIT_{section.upper()}__{yaml_key.upper()}", "zzz")
    validate_env_layer_keys()  # must not raise

    monkeypatch.delenv(f"AUTOSKILLIT_{section.upper()}__{yaml_key.upper()}")
    monkeypatch.setenv(f"AUTOSKILLIT_{section.upper()}__{field_name.upper()}", "zzz")
    with pytest.raises(ConfigSchemaError):
        validate_env_layer_keys()


@pytest.mark.parametrize("var_name", sorted(AUTOSKILLIT_PRIVATE_ENV_VARS))
def test_ambient_plumbing_env_vars_do_not_raise(
    var_name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """This is the regression guard for S12: session-plumbing variables that
    happen to contain `__` (e.g. AUTOSKILLIT_AGENT_BACKEND__BACKEND,
    AUTOSKILLIT_QUOTA_GUARD__DISABLED) must never be mistaken for a
    misspelled config key."""
    monkeypatch.setenv(var_name, "some-value")
    validate_env_layer_keys()  # must not raise


def test_deep_nested_env_override_still_resolves(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AUTOSKILLIT_MODEL__RECIPE_OVERRIDES__r__s must still resolve to
    model.recipe_overrides == {'r': {'s': 'zzz'}} and must not raise — a
    naive split-on-first-`__` would reject this real, working feature."""
    monkeypatch.setenv("AUTOSKILLIT_MODEL__RECIPE_OVERRIDES__myrecipe__mystep", "zzz")
    validate_env_layer_keys()  # must not raise

    # Isolate from the real ~/.autoskillit/config.yaml, which may carry its
    # own model.recipe_overrides entries that would otherwise merge in here.
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path / "home")
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    cfg = load_config(project_dir)
    assert cfg.model.recipe_overrides == {"myrecipe": {"mystep": "zzz"}}


# agent_backend.backend is intentionally excluded: AUTOSKILLIT_AGENT_BACKEND__BACKEND
# is in AUTOSKILLIT_PRIVATE_ENV_VARS (the flat/nested carve-out below), so it takes
# the skip path rather than exercising the section/key split this test targets. A
# sibling field in the same section (auto_provision_exploration) is used instead so
# the underscore-containing *section name* is still genuinely validated.
_UNDERSCORED_SECTION_SAMPLE_KEYS: tuple[tuple[str, str], ...] = tuple(
    sorted(
        (section, sorted(_CONFIG_SCHEMA[section])[0])
        for section in _CONFIG_SCHEMA
        if "_" in section
    )
)


@pytest.mark.parametrize(("section", "key"), _UNDERSCORED_SECTION_SAMPLE_KEYS)
def test_underscored_section_names_validate(
    section: str, key: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(f"AUTOSKILLIT_{section.upper()}__{key.upper()}", "zzz")
    validate_env_layer_keys()  # must not raise


def test_flat_agent_backend_env_var_still_works(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pins the deliberate pop/restore carve-out (_config_loader.py:176-196):
    the flat AUTOSKILLIT_AGENT_BACKEND var must survive a load_config() call
    unchanged in the environment, and must not be treated as a nested
    section.key override (it has no `__`)."""
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path / "home")
    monkeypatch.setenv("AUTOSKILLIT_AGENT_BACKEND", "claude-code")

    project_dir = tmp_path / "project"
    project_dir.mkdir()
    cfg = load_config(project_dir)

    assert isinstance(cfg, AutomationConfig)
    assert os.environ["AUTOSKILLIT_AGENT_BACKEND"] == "claude-code"
