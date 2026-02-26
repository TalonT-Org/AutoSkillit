"""Tests for failure_store.py — FS1 through FS11 and FS-IM1 through FS-IM4."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from autoskillit.failure_store import (
    FailureStore,
    MigrationFailure,
    default_store_path,
    record_from_skill,
)


# ---------------------------------------------------------------------------
# FS1: load() returns {} when failures.json absent
# ---------------------------------------------------------------------------
def test_empty_when_no_file(tmp_path: Path) -> None:
    store = FailureStore(tmp_path / "failures.json")
    assert store.load() == {}


# ---------------------------------------------------------------------------
# FS2: record() creates failures.json and all parent dirs
# ---------------------------------------------------------------------------
def test_record_creates_file_and_dirs(tmp_path: Path) -> None:
    store_path = tmp_path / "deep" / "nested" / "failures.json"
    store = FailureStore(store_path)
    store.record("my-recipe", Path("/some/file.yaml"), "recipe", "some error", 3)
    assert store_path.exists()


# ---------------------------------------------------------------------------
# FS3: All MigrationFailure fields survive serialization roundtrip
# ---------------------------------------------------------------------------
def test_record_and_load_roundtrip(tmp_path: Path) -> None:
    store_path = tmp_path / "failures.json"
    store = FailureStore(store_path)
    store.record("my-recipe", Path("/path/to/recipe.yaml"), "recipe", "bad schema", 2)
    failures = store.load()
    assert "my-recipe" in failures
    f = failures["my-recipe"]
    assert isinstance(f, MigrationFailure)
    assert f.name == "my-recipe"
    assert f.file_path == "/path/to/recipe.yaml"
    assert f.file_type == "recipe"
    assert f.error == "bad schema"
    assert f.retries_attempted == 2
    assert f.timestamp  # non-empty ISO timestamp


# ---------------------------------------------------------------------------
# FS4: Second record() adds entry; first entry unchanged
# ---------------------------------------------------------------------------
def test_record_multiple_failures(tmp_path: Path) -> None:
    store_path = tmp_path / "failures.json"
    store = FailureStore(store_path)
    store.record("alpha", Path("/a.yaml"), "recipe", "err-a", 1)
    store.record("beta", Path("/b.yaml"), "recipe", "err-b", 3)
    failures = store.load()
    assert len(failures) == 2
    assert failures["alpha"].error == "err-a"
    assert failures["beta"].error == "err-b"


# ---------------------------------------------------------------------------
# FS5: Recording same name twice keeps only latest entry
# ---------------------------------------------------------------------------
def test_overwrite_existing_failure(tmp_path: Path) -> None:
    store_path = tmp_path / "failures.json"
    store = FailureStore(store_path)
    store.record("dup", Path("/dup.yaml"), "recipe", "first error", 1)
    store.record("dup", Path("/dup.yaml"), "recipe", "second error", 3)
    failures = store.load()
    assert len(failures) == 1
    assert failures["dup"].error == "second error"
    assert failures["dup"].retries_attempted == 3


# ---------------------------------------------------------------------------
# FS6: clear(name) removes the named entry
# ---------------------------------------------------------------------------
def test_clear_removes_failure(tmp_path: Path) -> None:
    store_path = tmp_path / "failures.json"
    store = FailureStore(store_path)
    store.record("alpha", Path("/a.yaml"), "recipe", "err-a", 2)
    store.record("beta", Path("/b.yaml"), "recipe", "err-b", 2)
    store.clear("alpha")
    failures = store.load()
    assert "alpha" not in failures
    assert "beta" in failures


# ---------------------------------------------------------------------------
# FS7: clear() when name not present does not raise or corrupt
# ---------------------------------------------------------------------------
def test_clear_noop_when_absent(tmp_path: Path) -> None:
    store_path = tmp_path / "failures.json"
    store = FailureStore(store_path)
    store.record("existing", Path("/e.yaml"), "recipe", "err", 1)
    store.clear("nonexistent")  # must not raise
    failures = store.load()
    assert "existing" in failures


# ---------------------------------------------------------------------------
# FS8: has_failure(name) returns True after recording
# ---------------------------------------------------------------------------
def test_has_failure_true(tmp_path: Path) -> None:
    store_path = tmp_path / "failures.json"
    store = FailureStore(store_path)
    store.record("present", Path("/p.yaml"), "recipe", "err", 3)
    assert store.has_failure("present") is True


# ---------------------------------------------------------------------------
# FS9: has_failure(name) returns False when no record exists
# ---------------------------------------------------------------------------
def test_has_failure_false(tmp_path: Path) -> None:
    store_path = tmp_path / "failures.json"
    store = FailureStore(store_path)
    assert store.has_failure("absent") is False


# ---------------------------------------------------------------------------
# FS10: record_from_skill() records to cwd/.autoskillit/temp/migrations/failures.json
# ---------------------------------------------------------------------------
def test_record_from_skill_helper(tmp_path: Path) -> None:
    with patch("autoskillit.failure_store.Path.cwd", return_value=tmp_path):
        record_from_skill(
            name="my-pipeline",
            file_path="/abs/path/my-pipeline.yaml",
            file_type="recipe",
            error="validation failed",
            retries_attempted=3,
        )
    expected_path = tmp_path / ".autoskillit" / "temp" / "migrations" / "failures.json"
    assert expected_path.exists()
    store = FailureStore(expected_path)
    failures = store.load()
    assert "my-pipeline" in failures
    f = failures["my-pipeline"]
    assert f.file_path == "/abs/path/my-pipeline.yaml"
    assert f.retries_attempted == 3


# ---------------------------------------------------------------------------
# FS11: default_store_path(project_dir) returns correct path
# ---------------------------------------------------------------------------
def test_default_store_path(tmp_path: Path) -> None:
    result = default_store_path(tmp_path)
    expected = tmp_path / ".autoskillit" / "temp" / "migrations" / "failures.json"
    assert result == expected


# ---------------------------------------------------------------------------
# FS-IM1: has_failure() reads _state, not disk
# ---------------------------------------------------------------------------
def test_has_failure_reads_in_memory_state(tmp_path: Path) -> None:
    """After record(), removing the file does not fool has_failure()."""
    store_path = tmp_path / "failures.json"
    store = FailureStore(store_path)
    store.record("alpha", Path("/a.yaml"), "recipe", "err", 1)
    store_path.unlink()  # kill the file — old impl would return False
    assert store.has_failure("alpha") is True  # _state still knows


# ---------------------------------------------------------------------------
# FS-IM2: load() returns a copy of _state, not a fresh disk read
# ---------------------------------------------------------------------------
def test_load_returns_in_memory_copy(tmp_path: Path) -> None:
    """load() reflects in-memory state even when the backing file is gone."""
    store_path = tmp_path / "failures.json"
    store = FailureStore(store_path)
    store.record("beta", Path("/b.yaml"), "recipe", "err", 2)
    store_path.unlink()
    result = store.load()
    assert "beta" in result


# ---------------------------------------------------------------------------
# FS-IM3: disk write still occurs; new instance reads persisted data
# ---------------------------------------------------------------------------
def test_record_persists_to_disk(tmp_path: Path) -> None:
    """record() updates _state AND writes to disk — separate instance sees it."""
    store_path = tmp_path / "failures.json"
    store_a = FailureStore(store_path)
    store_a.record("gamma", Path("/g.yaml"), "recipe", "err", 1)
    # Fresh instance bootstraps _state from disk
    store_b = FailureStore(store_path)
    assert store_b.has_failure("gamma") is True


# ---------------------------------------------------------------------------
# FS-IM4: failed disk write does not corrupt in-memory state
# ---------------------------------------------------------------------------
def test_record_does_not_mutate_state_on_disk_failure(tmp_path: Path) -> None:
    """If _atomic_write raises, _state remains unchanged."""
    store = FailureStore(tmp_path / "failures.json")
    with patch("autoskillit.failure_store._atomic_write", side_effect=OSError("disk full")):
        with pytest.raises(OSError):
            store.record("alpha", Path("/a.yaml"), "recipe", "err", 1)
    assert not store.has_failure("alpha")
