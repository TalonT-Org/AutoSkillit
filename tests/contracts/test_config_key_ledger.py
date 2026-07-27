"""G3 config key ledger — the forcing function for silent schema drift (issue #4303).

``tests/contracts/config_key_ledger.txt`` is a sorted, deduplicated snapshot of
every dotted ``section.key`` string ``_CONFIG_SCHEMA`` has ever accepted (plus
the 3 currently-retired old keys). This test compares that frozen ledger
against the *live* ``_CONFIG_SCHEMA`` in both directions:

  * every live schema key must be on the ledger (a silent addition — a new key
    nobody wrote a ledger line for)
  * every ledger key must be explained by something live: the current schema,
    RETIRED_CONFIG_KEYS (a documented rename), or RETIRED_FEATURES (a removed
    feature flag) — a silent removal, i.e. a key that vanished from the schema
    with no rename record, fails here

A key falling out of the schema with no RETIRED_CONFIG_KEYS entry is exactly
the bug this whole registry exists to prevent: it means a pre-existing
config.yaml carrying that key will crash at startup with no upgrade path.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from autoskillit.config.settings import _CONFIG_SCHEMA, RETIRED_CONFIG_KEYS
from autoskillit.core.types._type_constants_features import RETIRED_FEATURES

pytestmark = [pytest.mark.layer("contracts"), pytest.mark.small]

_LEDGER_PATH = Path(__file__).parent / "config_key_ledger.txt"

_DOTTED_PAIR_RE = re.compile(r"^[a-z_][a-z0-9_]*\.[a-z_][a-z0-9_]*$")


def _read_ledger_lines() -> list[str]:
    lines: list[str] = []
    for raw_line in _LEDGER_PATH.read_text().splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        lines.append(stripped)
    return lines


def _schema_dotted_keys() -> set[str]:
    return {f"{section}.{key}" for section, keys in _CONFIG_SCHEMA.items() for key in keys}


_LEDGER_LINES = _read_ledger_lines()
_LEDGER_SET = set(_LEDGER_LINES)
_SCHEMA_DOTTED = _schema_dotted_keys()
_RETIRED_DOTTED = {f"{s}.{k}" for (s, k) in RETIRED_CONFIG_KEYS}


def test_no_silent_schema_additions() -> None:
    missing = sorted(_SCHEMA_DOTTED - _LEDGER_SET)
    assert not missing, (
        f"_CONFIG_SCHEMA has keys not present in the ledger: {missing}. "
        "Append these lines to tests/contracts/config_key_ledger.txt."
    )


def test_no_silent_schema_removals() -> None:
    offending = []
    for ledger_key in _LEDGER_LINES:
        if ledger_key in _SCHEMA_DOTTED:
            continue
        if ledger_key in _RETIRED_DOTTED:
            continue
        section, _, name = ledger_key.partition(".")
        if section == "features" and name in RETIRED_FEATURES:
            continue
        offending.append(ledger_key)
    offending.sort()
    assert not offending, (
        f"Ledger key(s) no longer explained by anything live: {offending}. "
        "A key that disappeared from _CONFIG_SCHEMA with no successor must get a "
        "RETIRED_CONFIG_KEYS entry (with its successor key and a migration note "
        "in src/autoskillit/migrations/*.yaml), or — for a removed feature.<name> "
        "line — must be added to RETIRED_FEATURES."
    )


def test_ledger_is_sorted_and_deduplicated() -> None:
    assert len(_LEDGER_LINES) == len(set(_LEDGER_LINES)), (
        "tests/contracts/config_key_ledger.txt contains duplicate lines."
    )
    assert _LEDGER_LINES == sorted(_LEDGER_LINES), (
        "tests/contracts/config_key_ledger.txt lines must be sorted."
    )
    non_matching = [line for line in _LEDGER_LINES if not _DOTTED_PAIR_RE.match(line)]
    assert not non_matching, (
        f"tests/contracts/config_key_ledger.txt line(s) do not match the expected "
        f"'word.word' dotted-pair shape: {non_matching}"
    )
