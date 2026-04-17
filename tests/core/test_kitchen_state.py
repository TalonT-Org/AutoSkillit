"""Tests for KitchenMarker hash field support."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

pytestmark = [pytest.mark.layer("core"), pytest.mark.small]


def test_kitchen_marker_has_hash_fields():
    from autoskillit.core.kitchen_state import KitchenMarker

    marker = KitchenMarker(
        session_id="s",
        opened_at=datetime.now(UTC),
        recipe_name="r",
        content_hash="sha256:abc",
        composite_hash="sha256:def",
    )
    assert marker.content_hash == "sha256:abc"
    assert marker.composite_hash == "sha256:def"


def test_marker_roundtrip_with_hashes(tmp_path, monkeypatch):
    from autoskillit.core.kitchen_state import read_marker, write_marker

    monkeypatch.setenv("AUTOSKILLIT_STATE_DIR", str(tmp_path))
    write_marker("sess1", "recipe", content_hash="sha256:a", composite_hash="sha256:b")
    marker = read_marker("sess1")
    assert marker is not None
    assert marker.content_hash == "sha256:a"
    assert marker.composite_hash == "sha256:b"


def test_marker_backward_compat_no_hashes(tmp_path, monkeypatch):
    from autoskillit.core.kitchen_state import read_marker

    monkeypatch.setenv("AUTOSKILLIT_STATE_DIR", str(tmp_path))
    state_dir = tmp_path / "kitchen_state"
    state_dir.mkdir()
    (state_dir / "old.json").write_text(
        json.dumps(
            {
                "session_id": "old",
                "opened_at": datetime.now(UTC).isoformat(),
                "recipe_name": "r",
                "marker_version": 1,
            }
        )
    )
    marker = read_marker("old")
    assert marker is not None
    assert marker.content_hash == ""
    assert marker.composite_hash == ""


def test_kitchen_marker_hash_defaults():
    from autoskillit.core.kitchen_state import KitchenMarker

    marker = KitchenMarker(
        session_id="s",
        opened_at=datetime.now(UTC),
        recipe_name="r",
    )
    assert marker.content_hash == ""
    assert marker.composite_hash == ""
