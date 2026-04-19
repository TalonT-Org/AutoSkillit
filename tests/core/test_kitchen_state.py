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


# --- Group P-1: Kitchen state namespacing ---


def test_get_state_dir_namespaces_by_campaign_id(tmp_path, monkeypatch):
    """get_state_dir returns campaign-scoped subdirectory when AUTOSKILLIT_CAMPAIGN_ID is set."""
    from autoskillit.core.kitchen_state import get_state_dir

    monkeypatch.delenv("AUTOSKILLIT_STATE_DIR", raising=False)
    monkeypatch.setenv("AUTOSKILLIT_CAMPAIGN_ID", "camp-42")
    monkeypatch.chdir(tmp_path)
    result = get_state_dir()
    assert result == tmp_path / ".autoskillit" / "temp" / "kitchen_state" / "camp-42"


def test_get_state_dir_no_campaign_id_flat(tmp_path, monkeypatch):
    """get_state_dir returns flat path when AUTOSKILLIT_CAMPAIGN_ID is absent."""
    from autoskillit.core.kitchen_state import get_state_dir

    monkeypatch.delenv("AUTOSKILLIT_STATE_DIR", raising=False)
    monkeypatch.delenv("AUTOSKILLIT_CAMPAIGN_ID", raising=False)
    monkeypatch.chdir(tmp_path)
    result = get_state_dir()
    assert result == tmp_path / ".autoskillit" / "temp" / "kitchen_state"


def test_concurrent_campaigns_disjoint_dirs(tmp_path, monkeypatch):
    """Two different campaign_ids produce completely disjoint marker directories."""
    from autoskillit.core.kitchen_state import get_state_dir

    monkeypatch.delenv("AUTOSKILLIT_STATE_DIR", raising=False)
    monkeypatch.chdir(tmp_path)

    monkeypatch.setenv("AUTOSKILLIT_CAMPAIGN_ID", "campaign-a")
    dir_a = get_state_dir()

    monkeypatch.setenv("AUTOSKILLIT_CAMPAIGN_ID", "campaign-b")
    dir_b = get_state_dir()

    assert dir_a != dir_b
    assert "campaign-a" in str(dir_a)
    assert "campaign-b" in str(dir_b)


def test_state_dir_override_takes_precedence_over_campaign(tmp_path, monkeypatch):
    """AUTOSKILLIT_STATE_DIR override still takes priority even when campaign_id is set."""
    from autoskillit.core.kitchen_state import get_state_dir

    monkeypatch.setenv("AUTOSKILLIT_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("AUTOSKILLIT_CAMPAIGN_ID", "camp-42")
    result = get_state_dir()
    # Override path does NOT include campaign_id — it's a test-isolation override
    assert result == tmp_path / "kitchen_state"


def test_marker_roundtrip_with_campaign_namespace(tmp_path, monkeypatch):
    """write_marker + read_marker work correctly under campaign namespacing."""
    from autoskillit.core.kitchen_state import read_marker, write_marker

    monkeypatch.setenv("AUTOSKILLIT_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("AUTOSKILLIT_CAMPAIGN_ID", "camp-99")
    write_marker("sess-1", "my-recipe")
    marker = read_marker("sess-1")
    assert marker is not None
    assert marker.session_id == "sess-1"
    assert marker.recipe_name == "my-recipe"


def test_sweep_stale_markers_scoped_to_namespace(tmp_path, monkeypatch):
    """sweep_stale_markers only sweeps within its own namespace."""
    import json
    from datetime import UTC, datetime, timedelta

    from autoskillit.core.kitchen_state import sweep_stale_markers

    monkeypatch.delenv("AUTOSKILLIT_STATE_DIR", raising=False)
    monkeypatch.chdir(tmp_path)

    # Create stale marker in campaign-a namespace
    monkeypatch.setenv("AUTOSKILLIT_CAMPAIGN_ID", "campaign-a")
    from autoskillit.core.kitchen_state import get_state_dir

    dir_a = get_state_dir()
    dir_a.mkdir(parents=True, exist_ok=True)
    stale_time = (datetime.now(UTC) - timedelta(hours=25)).isoformat()
    (dir_a / "stale.json").write_text(
        json.dumps(
            {
                "session_id": "stale",
                "opened_at": stale_time,
                "recipe_name": None,
                "marker_version": 1,
            }
        )
    )

    # Create fresh marker in campaign-b namespace
    monkeypatch.setenv("AUTOSKILLIT_CAMPAIGN_ID", "campaign-b")
    dir_b = get_state_dir()
    dir_b.mkdir(parents=True, exist_ok=True)
    fresh_time = datetime.now(UTC).isoformat()
    (dir_b / "fresh.json").write_text(
        json.dumps(
            {
                "session_id": "fresh",
                "opened_at": fresh_time,
                "recipe_name": None,
                "marker_version": 1,
            }
        )
    )

    # Sweep in campaign-a scope — should only delete from campaign-a
    monkeypatch.setenv("AUTOSKILLIT_CAMPAIGN_ID", "campaign-a")
    deleted = sweep_stale_markers()
    assert deleted == 1

    # campaign-b marker untouched
    assert (dir_b / "fresh.json").exists()
