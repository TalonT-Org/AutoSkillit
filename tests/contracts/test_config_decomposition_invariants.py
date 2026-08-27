"""REQ-CONFIG-INV-001..005: retired-key remap, coherence gates, env validation,
write gateway, schema derivation all wire correctly across the new module
boundaries.
"""

from __future__ import annotations

from pathlib import Path
from typing import cast


def test_retired_key_remap_across_modules(tmp_path: Path) -> None:
    """`RETIRED_CONFIG_KEYS` (settings/_retired_keys.py) + remap + write still wire."""
    from autoskillit.config import (
        remap_retired_keys,
        write_config_layer,
    )

    layer = {"diagnostics": {"post_run_analysis": True}}
    remapped = remap_retired_keys(layer, is_secrets_layer=False)
    assert remapped[0]["diagnostics"]["pipeline_health"] is True
    write_config_layer(tmp_path / "config.yaml", {"diagnostics": {"pipeline_health": True}})


def test_env_layer_validation_uses_retired_remap() -> None:
    """validate_env_layer_keys (validation.py) calls remap_retired_keys (retired_keys.py)."""
    import autoskillit.config._validation as val_mod

    src = Path(cast(str, val_mod.__file__)).read_text()
    assert "remap_retired_keys" in src


def test_schema_built_from_automation_config_fields() -> None:
    """_CONFIG_SCHEMA (validation.py) is derived from AutomationConfig fields."""
    import dataclasses

    from autoskillit.config import AutomationConfig
    from autoskillit.config.settings import _CONFIG_SCHEMA

    field_sections = {
        f.name
        for f in dataclasses.fields(AutomationConfig)
        if f.name not in {"features", "experimental_enabled"}
    }
    for section in field_sections:
        assert section in _CONFIG_SCHEMA, f"_CONFIG_SCHEMA missing section {section}"
        assert _CONFIG_SCHEMA[section], f"_CONFIG_SCHEMA[{section}] empty"


def test_fleet_and_process_tether_validate_called_in_from_dynaconf() -> None:
    """from_dynaconf invokes .validate() on FleetConfig and ProcessTetherConfig."""
    import autoskillit.config._automation_config as auto_mod

    src_text = Path(cast(str, auto_mod.__file__)).read_text()
    assert "fleet.validate" in src_text
    assert "process_tether.validate" in src_text


def test_unset_sentinel_lives_in_automation_config_module() -> None:
    """`_UNSET` is owned by `_automation_config.py` (where its only consumer lives),
    not by `_coherence.py`. The facade re-exports it under both names for internal
    import-path stability across the existing test reach-ins, but the OWNER module
    is unambiguous."""
    import autoskillit.config._automation_config as auto_mod

    assert hasattr(auto_mod, "_UNSET"), "_UNSET must be defined in _automation_config.py"
    # And `_coherence.py` does NOT define `_UNSET` (it had no business owning it).
    import autoskillit.config._coherence as coh_mod

    # _coherence MAY re-export for facade compat; it must NOT own the binding.
    if hasattr(coh_mod, "_UNSET"):
        assert coh_mod._UNSET is auto_mod._UNSET, (
            "_coherence.py re-exports _UNSET but identity drifted"
        )


def test_retired_profile_keys_registry_loaded() -> None:
    """`RETIRED_PROFILE_KEYS` lives in `_dataclasses_providers.py` so it loads with
    the providers config section."""
    import autoskillit.config._dataclasses_providers as prov_mod

    assert hasattr(prov_mod, "RETIRED_PROFILE_KEYS")
    assert isinstance(prov_mod.RETIRED_PROFILE_KEYS, frozenset)
    for key in prov_mod.RETIRED_PROFILE_KEYS:
        assert isinstance(key, str)
        assert key == key.lower()
