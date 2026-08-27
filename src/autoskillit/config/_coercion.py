"""YAML→dataclass coercion pipeline.

Owns:
  - ``_coerce_value`` (type-driven coercion for primitive / collection fields).
  - ``_field_defaults`` (dataclass-defaults introspection helper).
  - ``_build_subconfig`` (per-section builder: applies YAML key aliases, field
    overrides, and the auto-coercer to produce a typed dataclass instance).
  - ``_preprocess_agent_backend`` (string-or-mapping normalization).
  - ``_YAML_KEY_ALIASES`` (static YAML-key vs Python-field-name mismatch table).
  - ``_FIELD_OVERRIDES`` (per-(section, field) custom builders — notably the
    ``_COMMAND_UNSET`` sentinel fallback for ``test_check.command`` and the
    ``logging.level`` uppercase transform).
  - ``_SECTION_PREPROCESSORS`` and ``_SECTION_BUILDERS`` (mapping section name
    to its pre-coercion normalizer or fully-custom builder).
"""

from __future__ import annotations

import dataclasses
import types
from collections.abc import Callable
from typing import Any, TypeVar, Union, get_args, get_origin, get_type_hints

from autoskillit.config._config_loader import (
    _build_packs_config,
    _build_subsets_config,
    _to_optional_commands,
)
from autoskillit.config._dataclasses_shared import ConfigSchemaError
from autoskillit.config._dataclasses_test_gating import _COMMAND_UNSET

_T = TypeVar("_T")


def _field_defaults(cls: type) -> dict[str, Any]:
    """Extract default values from dataclass fields into a dict keyed by field name."""
    defaults: dict[str, Any] = {}
    for f in dataclasses.fields(cls):  # type: ignore[arg-type]
        if f.default is not dataclasses.MISSING:
            defaults[f.name] = f.default
        elif f.default_factory is not dataclasses.MISSING:  # type: ignore[misc]
            defaults[f.name] = f.default_factory()  # type: ignore[call-arg]
    return defaults


def _coerce_value(value: Any, target_type: type, context: str) -> Any:
    """Coerce a raw config value to target_type based on its type annotation.

    Raises ConfigSchemaError for int/float conversion failures, including context.
    """
    origin = get_origin(target_type)
    args = get_args(target_type)

    if origin is types.UnionType or origin is Union:
        non_none = [a for a in args if a is not type(None)]
        if type(None) in args and len(non_none) == 1:
            inner = non_none[0]
            if inner is bool:
                return bool(value) if value is not None else None
            if inner in (int, float):
                return _coerce_value(value, inner, context) if value is not None else None
            if value is None:
                return None
            if not value:
                return None
            return _coerce_value(value, inner, context)
        return value

    # Dimensional wrapper types (Utf8ByteLimit, etc.): bless raw YAML
    # integers into typed wrappers exactly once at the config boundary.
    from autoskillit.core import Utf8ByteLimit

    if target_type is Utf8ByteLimit:
        if isinstance(value, bool):
            raise ConfigSchemaError(
                f"{context} must be a positive integer for Utf8ByteLimit, got {value!r}"
            )
        if isinstance(value, float) and not value.is_integer():
            raise ConfigSchemaError(
                f"{context} must be a positive integer for Utf8ByteLimit, got {value!r}"
            )
        try:
            coerced = int(value)
        except (TypeError, ValueError) as exc:
            raise ConfigSchemaError(
                f"{context} must be a positive integer for Utf8ByteLimit, got {value!r}"
            ) from exc
        if coerced <= 0:
            raise ConfigSchemaError(
                f"{context} must be a positive integer for Utf8ByteLimit, got {value!r}"
            )
        return Utf8ByteLimit(coerced)
    if target_type is int:
        if isinstance(value, bool):
            raise ConfigSchemaError(f"{context} must be an integer, got {value!r}")
        if isinstance(value, int):
            return value
        try:
            return int(value)
        except (TypeError, ValueError) as exc:
            raise ConfigSchemaError(f"{context} must be an integer, got {value!r}") from exc
    if target_type is float:
        if isinstance(value, bool):
            raise ConfigSchemaError(f"{context} must be a number, got {value!r}")
        if isinstance(value, (int, float)):
            return float(value)
        try:
            return float(value)
        except (TypeError, ValueError) as exc:
            raise ConfigSchemaError(f"{context} must be a number, got {value!r}") from exc
    if target_type is bool:
        return bool(value)
    if target_type is str:
        return str(value)
    if origin is list:
        try:
            return list(value)
        except TypeError as exc:
            raise ConfigSchemaError(f"{context} must be iterable for list, got {value!r}") from exc
    if origin is set:
        try:
            return set(value)
        except TypeError as exc:
            raise ConfigSchemaError(f"{context} must be iterable for set, got {value!r}") from exc
    if origin is dict:
        return value
    return value


# YAML key name differs from Python field name.
# Key: (section_name, field_name), Value: yaml_key_name
_YAML_KEY_ALIASES: dict[tuple[str, str], str] = {
    ("model", "default_model"): "default",
    ("model", "model_override"): "override",
}


# Custom field builders that bypass _coerce_value.
# Signature: (section_dict, defaults_dict) -> coerced_value
# The override is responsible for its own key lookup from section_dict.
_FIELD_OVERRIDES: dict[tuple[str, str], Callable[[dict[str, Any], dict[str, Any]], Any]] = {
    # YAML key "default" with None-means-unset semantic
    ("model", "default_model"): lambda sec, defs: (
        str(sec["default"]) if sec.get("default") is not None else defs["default_model"]
    ),
    # Sentinel for __post_init__ mutual-exclusion check with commands
    ("test_check", "command"): lambda sec, _: (
        list(sec["command"]) if sec.get("command") is not None else _COMMAND_UNSET
    ),
    # Structural validation for nested list shape
    ("test_check", "commands"): lambda sec, defs: _to_optional_commands(
        sec.get("commands", defs.get("commands"))
    ),
    # Uppercase transform
    ("logging", "level"): lambda sec, defs: str(sec.get("level", defs["level"])).upper(),
}


def _preprocess_agent_backend(raw: Any) -> dict[str, Any]:
    """Normalize agent_backend section: string shorthand or lowercased dict."""
    if isinstance(raw, str):
        return {"backend": raw}
    if isinstance(raw, dict):
        return {k.lower(): v for k, v in raw.items()}
    raise ConfigSchemaError(
        f"agent_backend must be a string or mapping, got {type(raw).__name__!r}: {raw!r}"
    )


# Section-level pre-processors applied before _build_subconfig.
_SECTION_PREPROCESSORS: dict[str, Callable[[Any], dict[str, Any]]] = {
    "agent_backend": _preprocess_agent_backend,
}

# Sections with fully custom builders (bypass _build_subconfig entirely).
_SECTION_BUILDERS: dict[str, Callable[[dict[str, Any]], Any]] = {
    "subsets": _build_subsets_config,
    "packs": _build_packs_config,
}


def _build_subconfig(cls: type[_T], section: dict[str, Any], section_name: str) -> _T:
    """Build a sub-config dataclass from a raw Dynaconf section dict.

    Uses dataclass field introspection and type annotations to auto-coerce
    values. Fields listed in _FIELD_OVERRIDES use custom builders. Fields
    listed in _YAML_KEY_ALIASES read from an alternate YAML key name.
    """
    defaults = _field_defaults(cls)
    hints = get_type_hints(cls)
    kwargs: dict[str, Any] = {}

    for f in dataclasses.fields(cls):  # type: ignore[arg-type]
        override_key = (section_name, f.name)
        if override_key in _FIELD_OVERRIDES:
            kwargs[f.name] = _FIELD_OVERRIDES[override_key](section, defaults)
            continue
        yaml_key = _YAML_KEY_ALIASES.get(override_key, f.name)
        raw = section.get(yaml_key, defaults.get(f.name))
        if raw is None and f.name not in defaults:
            raise ConfigSchemaError(
                f"{section_name}.{f.name} is required but absent from config and has no default."
            )
        kwargs[f.name] = _coerce_value(raw, hints[f.name], f"{section_name}.{yaml_key}")

    return cls(**kwargs)  # type: ignore[return-value]


# Re-export mapping type for backward compatibility with the prior public
# surface (tests asserted ``isinstance(RETIRED_CONFIG_KEYS, Mapping)``).
__all__ = [
    "_coerce_value",
    "_field_defaults",
    "_build_subconfig",
    "_preprocess_agent_backend",
    "_YAML_KEY_ALIASES",
    "_FIELD_OVERRIDES",
    "_SECTION_PREPROCESSORS",
    "_SECTION_BUILDERS",
]
