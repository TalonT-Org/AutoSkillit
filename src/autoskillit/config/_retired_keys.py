"""Retired-config-key registry and remap pipeline.

Owns:
  - ``RetiredConfigKeyDef`` (NamedTuple describing one retired sub-key).
  - ``RETIRED_CONFIG_KEYS`` (the append-only mapping of retired sub-keys to
    their definitions; wrapped in ``MappingProxyType`` for immutability).
  - The two invariants ``_NON_LOWER_RETIRED_KEYS`` and ``_NON_LOWER_NEW_KEYS``
    that run at module load to catch accidentally-uppercased entries.
  - ``RemappedConfigKey`` (NamedTuple describing one remap operation result).
  - ``remap_retired_keys`` (pure rewrite of retired sub-keys in a layer dict).

The ``_NON_LOWER_RETIRED_PROFILE_KEYS`` invariant lives next to
``RETIRED_PROFILE_KEYS`` in ``_dataclasses_providers.py`` because that registry
is owned by the providers module — keeping the invariant co-located ensures
any module that imports the registry also runs the check.
"""

from __future__ import annotations

import types
from collections.abc import Mapping
from typing import Any, NamedTuple, cast


class RetiredConfigKeyDef(NamedTuple):
    """One config sub-key this tool renamed, and where its value now lives.

    ``~/.autoskillit/config.yaml`` and project ``.autoskillit/config.yaml``
    persist across years of releases while ``_CONFIG_SCHEMA`` is derived from
    the *current* dataclasses. Renaming a field without an entry here makes
    every pre-existing config fail ``validate_layer_keys`` at startup — the
    outage this registry exists to prevent.
    """

    new_key: str
    retired_in: str
    note: str


# (section, retired_sub_key) -> where the value goes now. Append-only:
#   * DO NOT REMOVE entries.
#   * A retired name may NEVER be reused for a future field (guarded by T1a).
#   * Entries must always point at the *current* name. A chained rename
#     (old1 -> old2 -> new) gets a direct old1 -> new entry (guarded by T1b/T1d).
#   * The (section, key) shape covers SUB-KEY renames only. A top-level
#     *section* rename is rejected earlier, in validate_layer_keys, and would
#     need a dotted-path extension of this registry — it is deliberately not
#     built speculatively.
#   * Both halves of every entry use the **YAML key spelling**, not the Python
#     dataclass field name. These differ only in the ``model`` section today
#     (``_YAML_KEY_ALIASES``: default_model -> "default", model_override ->
#     "override"). ``_CONFIG_SCHEMA`` is built from YAML spellings and
#     ``_build_subconfig`` reads by YAML key, so a Python-field spelling here
#     would both fail T1b and write a key nothing reads.
#   * Every entry must have a migrations/*.yaml note whose ``detect.key``
#     matches (guarded by T2).
RETIRED_CONFIG_KEYS: Mapping[tuple[str, str], RetiredConfigKeyDef] = types.MappingProxyType(
    {
        ("diagnostics", "post_run_analysis"): RetiredConfigKeyDef(
            new_key="pipeline_health",
            retired_in="0.10.885",
            note=(
                "diagnostics.post_run_analysis was renamed to "
                "diagnostics.pipeline_health in 0.10.885."
            ),
        ),
        ("quota_guard", "threshold"): RetiredConfigKeyDef(
            new_key="short_window_threshold",
            retired_in="0.8.39",
            note=(
                "quota_guard.threshold was split into quota_guard.short_window_threshold "
                "and quota_guard.long_window_threshold in 0.8.39. The old value is carried "
                "onto short_window_threshold; long_window_threshold keeps its default "
                "(95.0) unless set explicitly."
            ),
        ),
        ("features", "franchise"): RetiredConfigKeyDef(
            new_key="fleet",
            retired_in="0.9.135",
            note="features.franchise was renamed to features.fleet in 0.9.135.",
        ),
        ("agent_backend", "force_claude_agent_teams_inactive"): RetiredConfigKeyDef(
            new_key="force_inactive_agent_teams",
            retired_in="0.10.987",
            note=(
                "agent_backend.force_claude_agent_teams_inactive was renamed to "
                "agent_backend.force_inactive_agent_teams in 0.10.987."
            ),
        ),
    }
)

_NON_LOWER_RETIRED_KEYS = sorted(
    (s, k) for s, k in RETIRED_CONFIG_KEYS if s != s.lower() or k != k.lower()
)
if _NON_LOWER_RETIRED_KEYS:
    raise AssertionError(
        "RETIRED_CONFIG_KEYS (section, old_key) pairs must be lowercase. "
        f"Offending: {_NON_LOWER_RETIRED_KEYS}"
    )
del _NON_LOWER_RETIRED_KEYS

_NON_LOWER_NEW_KEYS = sorted(
    d.new_key for d in RETIRED_CONFIG_KEYS.values() if d.new_key != d.new_key.lower()
)
if _NON_LOWER_NEW_KEYS:
    raise AssertionError(
        f"RETIRED_CONFIG_KEYS new_key values must be lowercase. Offending: {_NON_LOWER_NEW_KEYS}"
    )
del _NON_LOWER_NEW_KEYS


class RemappedConfigKey(NamedTuple):
    section: str
    old_key: str
    new_key: str
    value_moved: bool
    definition: RetiredConfigKeyDef


def remap_retired_keys(
    layer_dict: dict[str, Any],
    *,
    is_secrets_layer: bool,
) -> tuple[dict[str, Any], list[RemappedConfigKey]]:
    """Return (layer_dict with retired sub-keys rewritten, remap records).

    Pure: never mutates ``layer_dict``. Returns the input object unchanged
    when there is nothing to do. Secrets layers are returned untouched.

    Reads ``RETIRED_CONFIG_KEYS`` via the ``autoskillit.config.settings``
    facade (rather than the local module binding) so tests that monkeypatch
    ``settings_mod.RETIRED_CONFIG_KEYS`` continue to see the patched value.
    """
    # Late-bound lookup keeps the test ``monkeypatch.setattr(settings_mod,
    # 'RETIRED_CONFIG_KEYS', synthetic)`` patch path working: the function
    # reads from the facade module's CURRENT binding at call time, not from
    # the snapshot captured at import time.
    import autoskillit.config.settings as _settings_facade

    registry = cast(
        "Mapping[tuple[str, str], RetiredConfigKeyDef]",
        _settings_facade.RETIRED_CONFIG_KEYS,
    )

    if is_secrets_layer:
        return layer_dict, []

    hits: list[tuple[str, str, RetiredConfigKeyDef]] = []
    for (section, old_key), defn in sorted(registry.items()):
        section_dict = layer_dict.get(section)
        if isinstance(section_dict, dict) and old_key in section_dict:
            hits.append((section, old_key, defn))

    if not hits:
        return layer_dict, []

    result = dict(layer_dict)
    records: list[RemappedConfigKey] = []
    for section, old_key, defn in hits:
        if result[section] is layer_dict[section]:
            result[section] = dict(result[section])
        section_dict = result[section]
        value = section_dict.pop(old_key)
        moved = defn.new_key not in section_dict
        if moved:
            section_dict[defn.new_key] = value
        records.append(
            RemappedConfigKey(
                section=section,
                old_key=old_key,
                new_key=defn.new_key,
                value_moved=moved,
                definition=defn,
            )
        )

    return result, records
