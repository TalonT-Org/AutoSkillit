"""REQ-CONFIG-IDENT-001..010: dict identity preserved across facade re-exports.

The config decomposition uses ``from X import Y as Y`` so callers that
``monkeypatch.setitem`` against the facade reference still reach the same
underlying dict object. This contract catches accidental re-binding (e.g.
``Y = dict(Y)``) that would silently break tests using ``monkeypatch``.
"""

from __future__ import annotations

import importlib

import pytest


@pytest.mark.parametrize(
    "module_path,attribute",
    [
        ("autoskillit.config.settings", "_YAML_KEY_ALIASES"),
        ("autoskillit.config.settings", "_FIELD_OVERRIDES"),
        ("autoskillit.config.settings", "_SECTION_PREPROCESSORS"),
        ("autoskillit.config.settings", "_SECTION_BUILDERS"),
        ("autoskillit.config._config_dataclasses", "_SECRETS_ONLY_KEYS"),
        ("autoskillit.config._config_dataclasses", "_METADATA_KEYS"),
    ],
)
def test_facade_attribute_is_same_object_as_owner(module_path: str, attribute: str) -> None:
    """`from X import Y as Y` in the facade must NOT rebind Y to a copy.

    `tests/config/test_subconfig_builder.py` and friends use `monkeypatch.setitem`
    against the facade reference; that mutation must reach the owner module.
    """
    facade_mod = importlib.import_module(module_path)
    facade_obj = getattr(facade_mod, attribute)
    candidates = [
        "autoskillit.config._coercion",
        "autoskillit.config._dataclasses_shared",
        "autoskillit.config._validation",
    ]
    found_match = False
    for owner_path in candidates:
        try:
            owner_mod = importlib.import_module(owner_path)
        except ImportError:
            continue
        if hasattr(owner_mod, attribute):
            owner_obj = getattr(owner_mod, attribute)
            assert owner_obj is facade_obj, (
                f"{module_path}.{attribute} is not identity-equal to {owner_path}.{attribute}; "
                "facade must re-export without rebinding"
            )
            found_match = True
            break
    assert found_match, (
        f"No owner module contains {attribute}; facade exposes a symbol nobody owns"
    )


def test_automation_config_imports_coherence_gate_with_identity() -> None:
    """from_dynaconf must call the same gate object the tests check."""
    import autoskillit.config._automation_config as auto_mod
    import autoskillit.config._coherence as coh_mod
    import autoskillit.config.settings as settings_mod

    assert auto_mod._timeout_coherence_gate is coh_mod._timeout_coherence_gate
    assert auto_mod._timeout_coherence_gate is settings_mod._timeout_coherence_gate
    assert auto_mod._process_tether_coherence_gate is coh_mod._process_tether_coherence_gate
    assert auto_mod._process_tether_coherence_gate is settings_mod._process_tether_coherence_gate


def test_coercion_lambda_resolves_command_unset_identity() -> None:
    """The `_FIELD_OVERRIDES[("test_check", "command")]` lambda body closes over
    `_COMMAND_UNSET` from `_dataclasses_test_gating`. After facade rewrite the lambda
    must still resolve the same sentinel object."""
    import autoskillit.config._coercion as coerc_mod

    # Pass an empty section (no `command` key) so the lambda returns the
    # sentinel via the `else` branch.
    sentinel = coerc_mod._FIELD_OVERRIDES[("test_check", "command")]({}, {})
    # The lambda returns `_COMMAND_UNSET` (mutable list) when value is None.
    # Confirm by identity against the owner's module-level name.
    import autoskillit.config._dataclasses_test_gating as gating_mod

    assert sentinel is gating_mod._COMMAND_UNSET
