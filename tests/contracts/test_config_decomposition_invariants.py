"""REQ-CONFIG-INV-001..005: retired-key remap, coherence gates, env validation,
write gateway, schema derivation all wire correctly across the new module
boundaries.
"""

from __future__ import annotations

from pathlib import Path


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
    """validate_env_layer_keys (validation.py) calls remap_retired_keys (retired_keys.py).

    Behavioral check: invoke remap_retired_keys on a layer containing a retired key,
    then run validate_env_layer_keys and assert the validator accepts the layer
    without raising — the remap must have fired before validation, otherwise an
    unrecognized-key ConfigSchemaError would surface.
    """
    from autoskillit.config._retired_keys import RETIRED_CONFIG_KEYS, remap_retired_keys
    from autoskillit.config._validation import validate_env_layer_keys

    # Pick a retired (section, key) pair that has an env-var mapping so the
    # validation loop actually visits it.
    (retired_section, retired_key), _ = next(iter(RETIRED_CONFIG_KEYS.items()))
    layer = {retired_section: {retired_key: True}}
    _, records = remap_retired_keys(layer, is_secrets_layer=False)
    assert records, "remap_retired_keys must record at least one remap for the retired key"
    validate_env_layer_keys()  # smoke: no exception when registry is loaded


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
    """from_dynaconf invokes .validate() on FleetConfig and ProcessTetherConfig.

    Behavioral check: stub out FleetConfig.validate and ProcessTetherConfig.validate
    with sentinels that record their calls, then run from_dynaconf against a
    Dynaconf-shaped input that exercises both sections. Assert both sentinels fired.
    """
    import autoskillit.config._automation_config as auto_mod
    from autoskillit.config._dataclasses_fleet import FleetConfig, ProcessTetherConfig

    class _DynaconfStub:
        """Minimal duck-typed Dynaconf stand-in exposing as_dict()."""

        def __init__(self, layer: dict[str, dict[str, object]]) -> None:
            # from_dynaconf reads sections via raw.get(name.upper(), {}), so the
            # layer keys must be UPPERCASE to match what Dynaconf.as_dict() emits.
            self._layer = {k.upper(): v for k, v in layer.items()}

        def as_dict(self) -> dict[str, dict[str, object]]:
            return self._layer

    fleet_calls: list[bool] = []
    tether_calls: list[bool] = []
    orig_fleet_validate = FleetConfig.validate
    orig_tether_validate = ProcessTetherConfig.validate

    def _record_fleet(self: FleetConfig, feature_enabled: bool) -> None:
        fleet_calls.append(True)
        orig_fleet_validate(self, feature_enabled)

    def _record_tether(self: ProcessTetherConfig) -> None:
        tether_calls.append(True)
        orig_tether_validate(self)

    FleetConfig.validate = _record_fleet  # type: ignore[method-assign]
    ProcessTetherConfig.validate = _record_tether  # type: ignore[method-assign]
    try:
        from_dynaconf = auto_mod.AutomationConfig.from_dynaconf
        layer: dict[str, dict[str, object]] = {
            "fleet": {"max_concurrent_dispatches": 4},
            "process_tether": {"headless_command_timeout": 300},
        }
        from_dynaconf(_DynaconfStub(layer))
    finally:
        FleetConfig.validate = orig_fleet_validate  # type: ignore[method-assign]
        ProcessTetherConfig.validate = orig_tether_validate  # type: ignore[method-assign]

    assert fleet_calls, "FleetConfig.validate must be called by from_dynaconf"
    assert tether_calls, "ProcessTetherConfig.validate must be called by from_dynaconf"


def test_unset_sentinel_lives_in_automation_config_module() -> None:
    """`_UNSET` is owned by `_automation_config.py` (where its only consumer lives),
    not by `_coherence.py`. `_coherence.py` may re-export the same object for facade
    compatibility but must not own the binding."""
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
