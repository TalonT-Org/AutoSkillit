"""Concurrency and schema coverage for ``ManagedJoinRecordStore``.

The store writes every record through a ``fcntl.LOCK_EX | fcntl.LOCK_NB``
acquisition (see ``AGENTS.md`` §3.1 ``Bounded flock acquisition``). This
module exercises the bounded-write contract directly, alongside the load-side
schema and route validation that ``find_verified_context`` relies on.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from tests.server._managed_join_fixtures import isolated_state_dir, sample_attestation

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


def test_path_for_normalizes_session_id_and_lives_under_channel_dir(
    tmp_path: Path,
) -> None:
    from autoskillit.hooks._session_binding import resolve_channel_dir
    from autoskillit.server._managed_join_attestation import ManagedJoinRecordStore

    project_root = isolated_state_dir(tmp_path)
    store = ManagedJoinRecordStore(project_root)
    record_path = store.path_for("abc123")
    expected_path = resolve_channel_dir(project_root) / "managed_join_attestation_abc123.json"

    assert record_path == expected_path


def test_write_then_load_round_trips_attestation(tmp_path: Path) -> None:
    from autoskillit.execution.backends._codex_hooks import (
        managed_codex_route_for_launch_context,
    )
    from autoskillit.server._managed_join_attestation import ManagedJoinRecordStore

    project_root = isolated_state_dir(tmp_path)
    store = ManagedJoinRecordStore(project_root)
    context = sample_attestation("abc123")

    store.write(context, route=managed_codex_route_for_launch_context("interactive"))
    loaded = store.load("abc123")

    assert loaded is not None
    attestation, route = loaded
    assert attestation == context.managed_join_attestation
    assert route == "interactive-parent"


def test_load_returns_none_when_record_is_absent(tmp_path: Path) -> None:
    from autoskillit.server._managed_join_attestation import ManagedJoinRecordStore

    project_root = isolated_state_dir(tmp_path)
    store = ManagedJoinRecordStore(project_root)

    assert store.load("missing") is None


def test_load_returns_none_when_parent_id_mismatches(tmp_path: Path) -> None:
    from autoskillit.execution.backends._codex_hooks import (
        managed_codex_route_for_launch_context,
    )
    from autoskillit.server._managed_join_attestation import ManagedJoinRecordStore

    project_root = isolated_state_dir(tmp_path)
    store = ManagedJoinRecordStore(project_root)
    context = sample_attestation("abc123")
    store.write(context, route=managed_codex_route_for_launch_context("interactive"))

    assert store.load("other-id") is None


def test_load_returns_none_when_route_unknown(tmp_path: Path) -> None:
    from autoskillit.execution.backends._codex_hooks import (
        managed_codex_route_for_launch_context,
    )
    from autoskillit.server._managed_join_attestation import ManagedJoinRecordStore

    project_root = isolated_state_dir(tmp_path)
    store = ManagedJoinRecordStore(project_root)
    context = sample_attestation("abc123")
    store.write(context, route=managed_codex_route_for_launch_context("interactive"))

    record_path = store.path_for("abc123")
    payload = json.loads(record_path.read_text(encoding="utf-8"))
    payload["route"] = "unknown-route"
    record_path.write_text(json.dumps(payload), encoding="utf-8")

    assert store.load("abc123") is None


def test_load_returns_none_when_record_corrupt(tmp_path: Path) -> None:
    from autoskillit.execution.backends._codex_hooks import (
        managed_codex_route_for_launch_context,
    )
    from autoskillit.server._managed_join_attestation import ManagedJoinRecordStore

    project_root = isolated_state_dir(tmp_path)
    store = ManagedJoinRecordStore(project_root)
    context = sample_attestation("abc123")
    store.write(context, route=managed_codex_route_for_launch_context("interactive"))
    record_path = store.path_for("abc123")
    record_path.write_text("{not valid json", encoding="utf-8")

    assert store.load("abc123") is None


def test_load_returns_none_when_attestation_payload_missing(tmp_path: Path) -> None:
    from autoskillit.execution.backends._codex_hooks import (
        managed_codex_route_for_launch_context,
    )
    from autoskillit.server._managed_join_attestation import ManagedJoinRecordStore

    project_root = isolated_state_dir(tmp_path)
    store = ManagedJoinRecordStore(project_root)
    context = sample_attestation("abc123")
    store.write(context, route=managed_codex_route_for_launch_context("interactive"))
    record_path = store.path_for("abc123")
    payload = json.loads(record_path.read_text(encoding="utf-8"))
    payload.pop("attestation")
    record_path.write_text(json.dumps(payload), encoding="utf-8")

    assert store.load("abc123") is None


def test_load_returns_none_when_payload_is_not_object(tmp_path: Path) -> None:
    from autoskillit.execution.backends._codex_hooks import (
        managed_codex_route_for_launch_context,
    )
    from autoskillit.server._managed_join_attestation import ManagedJoinRecordStore

    project_root = isolated_state_dir(tmp_path)
    store = ManagedJoinRecordStore(project_root)
    context = sample_attestation("abc123")
    store.write(context, route=managed_codex_route_for_launch_context("interactive"))
    record_path = store.path_for("abc123")
    record_path.write_text(json.dumps(["not", "an", "object"]), encoding="utf-8")

    assert store.load("abc123") is None


def test_write_raises_when_attestation_is_missing(tmp_path: Path) -> None:
    from autoskillit.core import SemanticAdaptationContext
    from autoskillit.server._managed_join_attestation import ManagedJoinRecordStore

    project_root = isolated_state_dir(tmp_path)
    store = ManagedJoinRecordStore(project_root)

    with pytest.raises(ValueError, match="managed join record requires an attestation"):
        store.write(SemanticAdaptationContext(), route="interactive-parent")


def test_concurrent_writers_for_same_parent_id_one_loses_to_lock_nb(
    tmp_path: Path,
) -> None:
    """Two writers racing for the same record: LOCK_NB ensures one acquires, the other raises."""
    from contextlib import contextmanager
    from autoskillit.execution.backends._codex_hooks import (
        managed_codex_route_for_launch_context,
    )
    from autoskillit.server._managed_join_attestation import ManagedJoinRecordStore

    project_root = isolated_state_dir(tmp_path)
    store = ManagedJoinRecordStore(project_root)

    # The production write is microseconds-fast, so a back-to-back barrier
    # release can let both workers sequentially acquire/release with no
    # contention. Inject a deterministic hold window into the first worker's
    # _write_lock so the second worker's LOCK_NB attempt is guaranteed to land
    # inside the held-lock window.
    lock_held = threading.Event()
    release_lock = threading.Event()
    original_write_lock = store._write_lock

    @contextmanager
    def _slow_write_lock(record_path):  # type: ignore[no-untyped-def]
        with original_write_lock(record_path):
            lock_held.set()
            release_lock.wait(timeout=5)
            yield

    store._write_lock = _slow_write_lock  # type: ignore[method-assign]
    outcomes: list[BaseException | None] = [None, None]

    def _first_worker() -> None:
        try:
            store.write(
                sample_attestation("shared-parent"),
                route=managed_codex_route_for_launch_context("interactive"),
            )
        except BaseException as exc:  # pragma: no cover - propagates via outcomes
            outcomes[0] = exc

    def _second_worker() -> None:
        try:
            lock_held.wait(timeout=5)
            store.write(
                sample_attestation("shared-parent"),
                route=managed_codex_route_for_launch_context("interactive"),
            )
        except BaseException as exc:  # pragma: no cover - propagates via outcomes
            outcomes[1] = exc
        finally:
            release_lock.set()

    t1 = threading.Thread(target=_first_worker)
    t2 = threading.Thread(target=_second_worker)
    t1.start()
    t2.start()
    t1.join(timeout=10)
    t2.join(timeout=10)

    errors = [item for item in outcomes if item is not None]
    successes = [item for item in outcomes if item is None]
    assert len(successes) >= 1, "at least one writer must acquire the LOCK_EX"
    assert len(errors) >= 1, "the contended writer must surface LOCK_NB contention"
    assert all(isinstance(item, BlockingIOError) for item in errors), (
        f"expected BlockingIOError from LOCK_NB, got {[type(item).__name__ for item in errors]}"
    )
    assert store.load("shared-parent") is not None


def test_load_returns_none_when_schema_version_mismatches(tmp_path: Path) -> None:
    """A persisted record with a stale schema_version is rejected at the load boundary."""
    from autoskillit.execution.backends._codex_hooks import (
        managed_codex_route_for_launch_context,
    )
    from autoskillit.server._managed_join_attestation import ManagedJoinRecordStore

    project_root = isolated_state_dir(tmp_path)
    store = ManagedJoinRecordStore(project_root)
    context = sample_attestation("abc123")
    store.write(context, route=managed_codex_route_for_launch_context("interactive"))

    record_path = store.path_for("abc123")
    raw = json.loads(record_path.read_text(encoding="utf-8"))
    raw["schema_version"] = 99
    record_path.write_text(json.dumps(raw), encoding="utf-8")

    assert store.load("abc123") is None


def test_write_then_overwrite_records_latest_attestation(tmp_path: Path) -> None:
    from autoskillit.execution.backends._codex_hooks import (
        managed_codex_route_for_launch_context,
    )
    from autoskillit.server._managed_join_attestation import ManagedJoinRecordStore

    project_root = isolated_state_dir(tmp_path)
    store = ManagedJoinRecordStore(project_root)
    store.write(
        sample_attestation("abc123"),
        route=managed_codex_route_for_launch_context("interactive"),
    )
    store.write(
        sample_attestation("abc123"),
        route=managed_codex_route_for_launch_context("direct"),
    )

    loaded = store.load("abc123")
    assert loaded is not None
    attestation, route = loaded
    assert route == "parent"
    assert attestation.parent_session_id == "abc123"


def test_path_for_rejects_invalid_session_id(tmp_path: Path) -> None:
    from autoskillit.server._managed_join_attestation import ManagedJoinRecordStore

    project_root = isolated_state_dir(tmp_path)
    store = ManagedJoinRecordStore(project_root)

    with pytest.raises(ValueError):
        store.path_for("invalid session!")
