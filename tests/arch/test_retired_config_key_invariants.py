"""Architectural invariant tests for RETIRED_CONFIG_KEYS (issue #4303).

``RETIRED_CONFIG_KEYS`` is the registry that lets a pre-existing
``~/.autoskillit/config.yaml`` survive a config key rename: without an entry,
``validate_layer_keys`` raises ``ConfigSchemaError`` at startup for every
installed user still holding the old key. These invariants keep the registry
internally consistent so it can only ever help, never silently mask a bug:

  * a retired name can never be reused for a live field (T1a)
  * every remap target must itself be a currently-valid key, which also
    forbids chained renames (T1b/T1d)
  * a remap can never point at (or come from) a secrets-only key (T1c)
  * the registry cannot express a top-level section rename (T1e)
  * casing and version/note hygiene (T1f/T1g)
"""

from __future__ import annotations

import pytest
from packaging.version import Version

from autoskillit.config.settings import _CONFIG_SCHEMA, _SECRETS_ONLY_KEYS, RETIRED_CONFIG_KEYS

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]


@pytest.mark.parametrize(
    "section,old_key,defn",
    [(s, k, d) for (s, k), d in sorted(RETIRED_CONFIG_KEYS.items())],
)
def test_retired_config_key_invariants(section: str, old_key: str, defn) -> None:
    # (a) A retired name may never be reused for a live field.
    assert old_key not in _CONFIG_SCHEMA.get(section, frozenset()), (
        f"RETIRED_CONFIG_KEYS[{section!r}, {old_key!r}]: old_key {old_key!r} has been "
        f"reused as a live field in section {section!r} — a retired name must never "
        "be reused for a future field."
    )

    # (b) Every remap target is a currently-valid key (forbids chained renames).
    assert section in _CONFIG_SCHEMA, (
        f"RETIRED_CONFIG_KEYS[{section!r}, {old_key!r}]: section {section!r} is not in "
        "_CONFIG_SCHEMA at all."
    )
    assert defn.new_key in _CONFIG_SCHEMA[section], (
        f"RETIRED_CONFIG_KEYS[{section!r}, {old_key!r}]: new_key {defn.new_key!r} is not "
        f"a currently-valid key in section {section!r} (_CONFIG_SCHEMA[{section!r}] = "
        f"{sorted(_CONFIG_SCHEMA[section])}). Either the rename target itself was "
        "since renamed/removed (a chained rename must point directly at the current "
        "name) or this entry is stale."
    )

    # (c) Remap target must never mask the secrets-placement check.
    old_dotted = f"{section}.{old_key}"
    new_dotted = f"{section}.{defn.new_key}"
    assert old_dotted not in _SECRETS_ONLY_KEYS, (
        f"RETIRED_CONFIG_KEYS[{section!r}, {old_key!r}]: old key {old_dotted!r} is a "
        "secrets-only key — a retired config key must never coincide with a "
        "_SECRETS_ONLY_KEYS entry."
    )
    assert new_dotted not in _SECRETS_ONLY_KEYS, (
        f"RETIRED_CONFIG_KEYS[{section!r}, {old_key!r}]: remap target {new_dotted!r} is "
        "a secrets-only key — remapping onto it would mask the secrets-placement "
        "ConfigSchemaError for any pre-existing config.yaml still carrying the old key."
    )

    # (d) No-chain assertion: the remap target must not itself be a retired key.
    assert (section, defn.new_key) not in RETIRED_CONFIG_KEYS, (
        f"RETIRED_CONFIG_KEYS[{section!r}, {old_key!r}]: new_key {defn.new_key!r} is "
        f"itself a retired (section, old_key) pair in RETIRED_CONFIG_KEYS — this is a "
        "chained rename. Point this entry directly at the current, non-retired key."
    )

    # (e) The registry cannot express a top-level section rename.
    assert section in _CONFIG_SCHEMA, (
        f"RETIRED_CONFIG_KEYS[{section!r}, {old_key!r}]: section {section!r} does not "
        "exist in _CONFIG_SCHEMA — a section rename cannot be expressed by this "
        "sub-key registry."
    )

    # (f) section, old_key, new_key are all lowercase str.
    for label, value in (("section", section), ("old_key", old_key), ("new_key", defn.new_key)):
        assert isinstance(value, str), (
            f"RETIRED_CONFIG_KEYS[{section!r}, {old_key!r}]: {label} must be a str, "
            f"got {type(value).__name__!r}: {value!r}"
        )
        assert value == value.lower(), (
            f"RETIRED_CONFIG_KEYS[{section!r}, {old_key!r}]: {label} must be lowercase, "
            f"got {value!r}"
        )

    # (g) retired_in parses as a valid version; note is non-empty.
    try:
        Version(defn.retired_in)
    except Exception as exc:  # noqa: BLE001 - re-raise as an assertion-style failure
        pytest.fail(
            f"RETIRED_CONFIG_KEYS[{section!r}, {old_key!r}]: retired_in={defn.retired_in!r} "
            f"does not parse as a valid packaging.version.Version: {exc}"
        )
    assert defn.note.strip(), (
        f"RETIRED_CONFIG_KEYS[{section!r}, {old_key!r}]: note must be non-empty."
    )
