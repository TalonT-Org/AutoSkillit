"""Config error class and schema-validation primitives.

Owns:
  - ``ConfigSchemaError`` (the single error class config raises for schema mismatches).
  - ``_SECRETS_ONLY_KEYS`` (frozen set of dotted keys allowed only in ``.secrets.yaml``).
  - ``_METADATA_KEYS`` (frozen set of top-level keys carried in every layer regardless
    of section membership).

All other config modules that need to raise the error or check against these sets
import from here. ``_config_dataclasses.py`` and ``settings.py`` re-export the
symbols via ``from X import Y as Y`` so call sites using ``autoskillit.config.<...>``
keep working unchanged.
"""

from __future__ import annotations


class ConfigSchemaError(ValueError):
    """Raised when a config YAML layer contains unrecognized or misplaced keys."""


_SECRETS_ONLY_KEYS: frozenset[str] = frozenset({"github.token"})
_METADATA_KEYS: frozenset[str] = frozenset({"version"})
