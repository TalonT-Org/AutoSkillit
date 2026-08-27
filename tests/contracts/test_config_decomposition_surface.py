"""REQ-CONFIG-SURFACE-001..053 / REQ-CONFIG-SURFACE-PRIV-001..016:

Lock-in for the #4859 config decomposition. Verifies that:

1. Every public symbol on ``autoskillit.config.__all__`` is still importable.
2. Every private symbol that tests reach for (e.g. ``_YAML_KEY_ALIASES``,
   ``_build_subsets_config``, ``_MAX_CONCURRENT_DISPATCHES``) remains
   resolvable from at least one of the three legacy import paths:
   ``autoskillit.config.settings`` / ``autoskillit.config._config_dataclasses`` /
   ``autoskillit.config._config_loader``.
"""

from __future__ import annotations

import importlib

import pytest

from autoskillit.config import __all__ as PUBLIC_SYMBOLS

pytestmark = [pytest.mark.layer("contracts"), pytest.mark.small]


@pytest.mark.parametrize("symbol", sorted(PUBLIC_SYMBOLS))
def test_public_symbol_importable_from_package(symbol: str) -> None:
    module = importlib.import_module("autoskillit.config")
    assert hasattr(module, symbol), f"{symbol} missing from autoskillit.config"


@pytest.mark.parametrize(
    "symbol",
    [
        # settings.py facade reach
        "_SECRETS_ONLY_KEYS",
        "_YAML_KEY_ALIASES",
        "_FIELD_OVERRIDES",
        "_CONFIG_SCHEMA",
        "_SECTION_PREPROCESSORS",
        "_SECTION_BUILDERS",
        "_build_subsets_config",
        "_build_packs_config",
        "_process_tether_coherence_gate",
        "_claude_mcp_timeout_coherence_gate",
        "_codex_mcp_timeout_coherence_gate",
        "_timeout_coherence_gate",
        "_coerce_value",
        "_field_defaults",
        "_build_subconfig",
        "_preprocess_agent_backend",
        "_build_config_schema",
        "_UNSET",
        "_CI_WATCH_DEFAULT",
        "_MERGE_QUEUE_DEFAULT",
        "_MERGE_QUEUE_RECIPE_MAX",
        # Transitively-imported autoskillit.core re-exports.
        "FEATURE_REGISTRY",
        "FeatureLifecycle",
        "SkillVisibilitySpec",
        "atomic_write",
        "dump_yaml_str",
        "is_dev_install",
        "is_feature_enabled",
        # _config_dataclasses.py facade reach
        "_MAX_CONCURRENT_DISPATCHES",
        "_METADATA_KEYS",
        "_COMMAND_UNSET",
        # Transitively-imported autoskillit.core re-exports.
        "DRY_WALKTHROUGH_VERIFIED_MARKER",
        "IssueLabelState",
        "KNOWN_BACKEND_NAMES",
        "LABEL_LIFECYCLE_REGISTRY",
        "OutputFormat",
        "RECIPE_RESPONSE_DEFAULT_BYTES",
        "RECIPE_RESPONSE_MAX_UTF8_BYTES",
        "RECIPE_SECTION_RESPONSE_FLOOR_BYTES",
        "Utf8ByteLimit",
        # _config_loader.py reach
        "_to_optional_commands",
        "_make_dynaconf",
    ],
)
def test_private_symbol_still_resolvable_at_legacy_path(symbol: str) -> None:
    """Tests and downstream code reach into private symbols; the facade must re-export them."""
    settings_mod = importlib.import_module("autoskillit.config.settings")
    dataclasses_mod = importlib.import_module("autoskillit.config._config_dataclasses")
    loader_mod = importlib.import_module("autoskillit.config._config_loader")
    assert (
        hasattr(settings_mod, symbol)
        or hasattr(dataclasses_mod, symbol)
        or hasattr(loader_mod, symbol)
    ), f"{symbol} must remain reachable from settings / _config_dataclasses / _config_loader"
