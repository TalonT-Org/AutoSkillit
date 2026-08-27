"""Schema derivation and layer/env validation.

Owns:
  - ``_build_config_schema`` (derives a ``{section: {valid_yaml_keys}}`` map
    from ``AutomationConfig`` fields, walking the YAML-alias and field-override
    tables so the schema reflects the same key spelling users actually write).
  - ``_CONFIG_SCHEMA`` (the eagerly-built schema dict; module-load).
  - ``validate_layer_keys`` (validates a YAML layer dict against the schema
    plus the secrets-only allowlist).
  - ``validate_env_layer_keys`` (validates ``AUTOSKILLIT_<SECTION>__<KEY>``
    environment variables; the env-layer analogue of ``validate_layer_keys``).

Module-load order matters: this file calls ``_build_config_schema()`` at
import time, which reads ``_YAML_KEY_ALIASES`` and ``_FIELD_OVERRIDES`` from
``_coercion``. The reverse import direction (validation → coercion) is fine
because ``_coercion`` is a leaf.
"""

from __future__ import annotations

import dataclasses
import os
from pathlib import Path
from typing import Any

from autoskillit.config._coercion import _FIELD_OVERRIDES, _YAML_KEY_ALIASES
from autoskillit.config._dataclasses_shared import (
    _METADATA_KEYS,
    _SECRETS_ONLY_KEYS,
    ConfigSchemaError,
)
from autoskillit.config._retired_keys import remap_retired_keys
from autoskillit.core import AUTOSKILLIT_PRIVATE_ENV_VARS, FEATURE_REGISTRY


def _build_config_schema() -> dict[str, frozenset[str]]:
    """Derive a two-level schema map {section: {valid_field_names}} from AutomationConfig."""
    # Local import: AutomationConfig imports _coercion which imports nothing
    # validation needs, so the cycle resolves on first call after all modules
    # are in sys.modules. Deferring here preserves a clean top-down graph.
    from autoskillit.config._automation_config import AutomationConfig

    schema: dict[str, frozenset[str]] = {}
    for f in dataclasses.fields(AutomationConfig):
        if f.name == "features":
            schema["features"] = frozenset(FEATURE_REGISTRY.keys()) | frozenset(
                {"experimental_enabled"}
            )
            continue
        if f.name == "experimental_enabled":
            continue
        sub_type: type | None = None
        if f.default_factory is not dataclasses.MISSING:  # type: ignore[misc]
            factory = f.default_factory  # type: ignore[assignment]
            if dataclasses.is_dataclass(factory):
                sub_type = factory
        elif f.default is not dataclasses.MISSING and dataclasses.is_dataclass(f.default):
            sub_type = type(f.default)
        if sub_type is not None:
            yaml_keys: set[str] = set()
            for sf in dataclasses.fields(sub_type):
                alias = _YAML_KEY_ALIASES.get((f.name, sf.name))
                yaml_keys.add(alias if alias is not None else sf.name)
            # Also include YAML keys from field overrides that use different key names
            for (sec_name, _field_name), _ in _FIELD_OVERRIDES.items():
                if sec_name == f.name:
                    alias = _YAML_KEY_ALIASES.get((sec_name, _field_name))
                    if alias is not None:
                        yaml_keys.add(alias)
            schema[f.name] = frozenset(yaml_keys)
        else:
            schema[f.name] = frozenset()
    return schema


# NOTE: keep this as a module-load expression. Re-binding after the eager build
# would mask late changes to AutomationConfig fields and break the immutable
# contract downstream callers (e.g. validate_layer_keys) rely on.
_CONFIG_SCHEMA: dict[str, frozenset[str]] = _build_config_schema()  # noqa: F841


def validate_layer_keys(
    layer_dict: dict[str, Any],
    layer_path: Path,
    *,
    is_secrets_layer: bool,
) -> None:
    """Validate that all keys in a YAML config layer are recognized and allowed.

    Raises ConfigSchemaError for:
    - Unrecognized top-level section name
    - Unrecognized field name within a known section
    - A _SECRETS_ONLY_KEYS path appearing in a non-secrets layer
    """
    import difflib  # stdlib — safe to import here

    for top_key, value in layer_dict.items():
        if top_key in _METADATA_KEYS:
            continue
        if top_key not in _CONFIG_SCHEMA:
            known = sorted(_CONFIG_SCHEMA.keys())
            close = difflib.get_close_matches(top_key, known, n=1, cutoff=0.6)
            hint = f" did you mean '{close[0]}'?" if close else ""
            raise ConfigSchemaError(
                f"Invalid configuration in {str(layer_path)!r}: "
                f"unrecognized key '{top_key}'.{hint}"
            )
        # Validate sub-keys for all dict-valued sections; empty frozenset means no valid sub-keys
        if isinstance(value, dict):
            for sub_key in value:
                dotted = f"{top_key}.{sub_key}"
                if dotted in _SECRETS_ONLY_KEYS:
                    if not is_secrets_layer:
                        secrets_hint_path = layer_path.parent / ".secrets.yaml"
                        top, sub = dotted.split(".", 1)
                        raise ConfigSchemaError(
                            f"Invalid configuration in {str(layer_path)!r}: "
                            f"'{dotted}' is a secret key that must not appear in config.yaml.\n\n"
                            f"To fix, add the following to {str(secrets_hint_path)!r}:\n\n"
                            f"  {top}:\n"
                            f"    {sub}: <your_token_value>\n\n"
                            f"Then remove the '{dotted}' key from {str(layer_path)!r}."
                        )
                    continue  # secrets-only keys are valid in .secrets.yaml
                if sub_key not in _CONFIG_SCHEMA[top_key]:
                    known_sub = sorted(_CONFIG_SCHEMA[top_key])
                    close = difflib.get_close_matches(sub_key, known_sub, n=1, cutoff=0.6)
                    hint = f" did you mean '{top_key}.{close[0]}'?" if close else ""
                    raise ConfigSchemaError(
                        f"Invalid configuration in {str(layer_path)!r}: "
                        f"unrecognized key '{dotted}' in section '{top_key}'.{hint}"
                    )


def validate_env_layer_keys() -> None:
    """Validate every ``AUTOSKILLIT_<SECTION>__<KEY>`` environment variable
    names a real ``section.key`` — the env-layer analogue of
    ``validate_layer_keys`` for file layers.

    Deliberately does NOT introspect a merged Dynaconf document (``d.as_dict()``):
    that view has no provenance, uppercases env-sourced keys, and would surface
    every ambient ``AUTOSKILLIT_*`` process variable, raising at startup for
    essentially every real session. Instead this enumerates ``os.environ``
    directly and validates only names containing the ``__`` nested separator,
    which unambiguously target a config ``section.key``.

    Only the first two ``__``-separated segments are validated — deeper
    nesting (e.g. ``AUTOSKILLIT_MODEL__RECIPE_OVERRIDES__myrecipe__mystep``)
    is a real, working feature; validating past the second segment would
    reject it (``_CONFIG_SCHEMA`` is only two levels deep).

    ``AUTOSKILLIT_PRIVATE_ENV_VARS`` is a curated allowlist of session-plumbing
    variables (process wiring, not user configuration), not a blanket
    ignore-unknown-vars switch — a genuine typo in a nested config key still
    fails loudly here.

    Validated with ``is_secrets_layer=True``: unlike a ``config.yaml`` file,
    an environment variable is a normal, expected channel for supplying a
    secret (``AUTOSKILLIT_GITHUB__TOKEN``) — rejecting ``_SECRETS_ONLY_KEYS``
    here would reject a legitimate deployment pattern, not catch a mistake.
    """
    source_by_key: dict[tuple[str, str], str] = {}
    for name, value in os.environ.items():
        if name in AUTOSKILLIT_PRIVATE_ENV_VARS:
            continue
        if not name.startswith("AUTOSKILLIT_"):
            continue
        rest = name[len("AUTOSKILLIT_") :]
        if "__" not in rest:
            continue  # flat plumbing var, not a nested section.key override
        section, remainder = rest.split("__", 1)
        key = remainder.split("__", 1)[0]
        normalized_key = (section.lower(), key.lower())
        if previous_name := source_by_key.get(normalized_key):
            raise ConfigSchemaError(
                f"Conflicting environment variables {previous_name!r} and {name!r}: "
                f"both normalize to {normalized_key[0]}.{normalized_key[1]}"
            )
        source_by_key[normalized_key] = name

        candidate = {normalized_key[0]: {normalized_key[1]: value}}
        remapped, _ = remap_retired_keys(candidate, is_secrets_layer=False)
        validate_layer_keys(remapped, Path(name), is_secrets_layer=True)
