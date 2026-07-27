"""G2 bidirectional binding: RETIRED_CONFIG_KEYS <-> migrations/*.yaml detect.key notes.

``RETIRED_CONFIG_KEYS`` (src/autoskillit/config/settings.py) is the runtime
registry that lets a pre-existing config.yaml survive a key rename silently.
The migration notes under ``migrations/*.yaml`` are the human-facing
documentation of the same rename, discovered by ``detect.key``. Nothing
enforces that the two stay in sync on their own — this file is that
enforcement, in both directions:

  * every registry entry must have a matching note (a registry entry with no
    note leaves the user with no upgrade instructions)
  * every documented key rename must be registered (a note with no registry
    entry means the corresponding config.yaml would still crash at startup)

This file is purely additive: it does not modify or replace the existing
per-rename note tests in tests/migration/ (test_fleet_migration_note.py,
test_vis_lens_rename_migration_note.py, test_pipeline_health_migration_note.py).
"""

from __future__ import annotations

import pytest
import yaml

from autoskillit.config.settings import RETIRED_CONFIG_KEYS
from autoskillit.core import load_yaml, pkg_root

pytestmark = [pytest.mark.layer("contracts"), pytest.mark.small]


def _load_note(note_path) -> dict:
    try:
        return load_yaml(note_path) or {}
    except yaml.YAMLError as exc:
        pytest.fail(f"Malformed YAML in {note_path.name}: {exc}")


def _all_changes_with_detect_key() -> list[tuple[str, dict]]:
    """Return (note_filename, change) for every change with a detect.key field."""
    out: list[tuple[str, dict]] = []
    for note_path in sorted((pkg_root() / "migrations").glob("*.yaml")):
        data = _load_note(note_path)
        for change in data.get("changes", []):
            if "key" in change.get("detect", {}):
                out.append((note_path.name, change))
    return out


_NOTE_CHANGES = _all_changes_with_detect_key()
note_keys = {change["detect"]["key"] for _, change in _NOTE_CHANGES}
registry_keys = {f"{s}.{k}" for (s, k) in RETIRED_CONFIG_KEYS}


def test_every_registry_entry_has_a_migration_note() -> None:
    missing = sorted(registry_keys - note_keys)
    assert not missing, (
        "RETIRED_CONFIG_KEYS entries with no migrations/*.yaml detect.key note: "
        f"{missing}. Add a migration note (with instruction/example_before/"
        "example_after) whose detect.key matches, so users see upgrade "
        "instructions for this rename."
    )


def test_every_documented_key_rename_is_registered() -> None:
    extra = sorted(note_keys - registry_keys)
    assert not extra, (
        f"migrations/*.yaml note(s) documenting detect.key {extra} with no matching "
        "entry in RETIRED_CONFIG_KEYS (src/autoskillit/config/settings.py). A "
        "pre-existing config.yaml using this key will still crash at startup with "
        "ConfigSchemaError. Either add a RETIRED_CONFIG_KEYS entry for it, or if it "
        "documents a key removed with no successor (or a top-level section rename) — "
        "which this registry cannot express — file a follow-up rather than deleting "
        "the note's detect.key."
    )


@pytest.mark.parametrize(
    "note_name,change",
    [(n, c) for n, c in _NOTE_CHANGES if c["detect"]["key"] in registry_keys],
    ids=[
        f"{n}:{c['detect']['key']}"
        for n, c in _NOTE_CHANGES
        if c["detect"]["key"] in registry_keys
    ],
)
def test_matched_notes_have_actionable_content(note_name: str, change: dict) -> None:
    for field in ("instruction", "example_before", "example_after"):
        assert field in change, f"{note_name} ({change['detect']['key']}): missing {field!r}"
        assert change[field].strip(), (
            f"{note_name} ({change['detect']['key']}): {field!r} is empty"
        )
