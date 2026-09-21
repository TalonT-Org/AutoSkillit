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

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


def _sample_attestation(parent_session_id: str = "abc123"):
    """Build a valid ``ManagedJoinAttestation`` for one managed parent."""
    from autoskillit.core import (
        MANAGED_JOIN_ATTESTATION_SCHEMA_VERSION,
        ManagedJoinAttestation,
        SemanticAdaptationContext,
    )

    attestation = ManagedJoinAttestation(
        schema_version=MANAGED_JOIN_ATTESTATION_SCHEMA_VERSION,
        backend="codex",
        launch_context="interactive",
        parent_session_id=parent_session_id,
        activation_epoch=0,
        direct_tool_mode=True,
        resolved_model="gpt-5.6-luna",
        resolved_reasoning_effort="high",
        codex_catalog_digest="a" * 64,
        fixed_batch_tool_registry_digest="b" * 64,
        hook_registry_digest="c" * 64,
        skill_load_applies=True,
        guards_apply=True,
        provenance="autoskillit-server",
    )
    return SemanticAdaptationContext(managed_join_attestation=attestation)


def _isolated_state_root(tmp_path: Path) -> Path:
    """Return a project root whose ``.autoskillit`` directory is unique per test."""
    (tmp_path / ".autoskillit").mkdir(parents=True, exist_ok=True)
    return tmp_path


def test_path_for_normalizes_session_id_and_lives_under_channel_dir(
    tmp_path: Path,
) -> None:
    from autoskillit.hooks._session_binding import resolve_channel_dir
    from autoskillit.server._managed_join_attestation import ManagedJoinRecordStore

    project_root = _isolated_state_root(tmp_path)
    store = ManagedJoinRecordStore(project_root)
    record_path = store.path_for("abc123")
    expected_path = resolve_channel_dir(project_root) / "managed_join_attestation_abc123.json"

    assert record_path == expected_path


def test_write_then_load_round_trips_attestation(tmp_path: Path) -> None:
    from autoskillit.execution.backends._codex_hooks import (
        managed_codex_route_for_launch_context,
    )
    from autoskillit.server._managed_join_attestation import ManagedJoinRecordStore

    project_root = _isolated_state_root(tmp_path)
    store = ManagedJoinRecordStore(project_root)
    context = _sample_attestation("abc123")

    store.write(context, route=managed_codex_route_for_launch_context("interactive"))
    loaded = store.load("abc123")

    assert loaded is not None
    attestation, route = loaded
    assert attestation == context.managed_join_attestation
    assert route == "interactive-parent"


def test_load_returns_none_when_record_is_absent(tmp_path: Path) -> None:
    from autoskillit.server._managed_join_attestation import ManagedJoinRecordStore

    project_root = _isolated_state_root(tmp_path)
    store = ManagedJoinRecordStore(project_root)

    assert store.load("missing") is None


def test_load_returns_none_when_parent_id_mismatches(tmp_path: Path) -> None:
    from autoskillit.execution.backends._codex_hooks import (
        managed_codex_route_for_launch_context,
    )
    from autoskillit.server._managed_join_attestation import ManagedJoinRecordStore

    project_root = _isolated_state_root(tmp_path)
    store = ManagedJoinRecordStore(project_root)
    context = _sample_attestation("abc123")
    store.write(context, route=managed_codex_route_for_launch_context("interactive"))

    assert store.load("other-id") is None


def test_load_returns_none_when_route_unknown(tmp_path: Path) -> None:
    from autoskillit.execution.backends._codex_hooks import (
        managed_codex_route_for_launch_context,
    )
    from autoskillit.server._managed_join_attestation import ManagedJoinRecordStore

    project_root = _isolated_state_root(tmp_path)
    store = ManagedJoinRecordStore(project_root)
    context = _sample_attestation("abc123")
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

    project_root = _isolated_state_root(tmp_path)
    store = ManagedJoinRecordStore(project_root)
    context = _sample_attestation("abc123")
    store.write(context, route=managed_codex_route_for_launch_context("interactive"))
    record_path = store.path_for("abc123")
    record_path.write_text("{not valid json", encoding="utf-8")

    assert store.load("abc123") is None


def test_load_returns_none_when_attestation_payload_missing(tmp_path: Path) -> None:
    from autoskillit.execution.backends._codex_hooks import (
        managed_codex_route_for_launch_context,
    )
    from autoskillit.server._managed_join_attestation import ManagedJoinRecordStore

    project_root = _isolated_state_root(tmp_path)
    store = ManagedJoinRecordStore(project_root)
    context = _sample_attestation("abc123")
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

    project_root = _isolated_state_root(tmp_path)
    store = ManagedJoinRecordStore(project_root)
    context = _sample_attestation("abc123")
    store.write(context, route=managed_codex_route_for_launch_context("interactive"))
    record_path = store.path_for("abc123")
    record_path.write_text(json.dumps(["not", "an", "object"]), encoding="utf-8")

    assert store.load("abc123") is None


def test_write_raises_when_attestation_is_missing(tmp_path: Path) -> None:
    from autoskillit.core import SemanticAdaptationContext
    from autoskillit.server._managed_join_attestation import ManagedJoinRecordStore

    project_root = _isolated_state_root(tmp_path)
    store = ManagedJoinRecordStore(project_root)

    with pytest.raises(ValueError, match="managed join record requires an attestation"):
        store.write(SemanticAdaptationContext(), route="interactive-parent")


def test_concurrent_writers_for_same_parent_id_one_loses_to_lock_nb(
    tmp_path: Path,
) -> None:
    """Two writers racing for the same record: LOCK_NB ensures one acquires, the other raises."""
    from autoskillit.execution.backends._codex_hooks import (
        managed_codex_route_for_launch_context,
    )
    from autoskillit.server._managed_join_attestation import ManagedJoinRecordStore

    project_root = _isolated_state_root(tmp_path)
    store = ManagedJoinRecordStore(project_root)
    barrier = threading.Barrier(2)
    outcomes: list[BaseException | None] = [None, None]

    def _worker(slot: int) -> None:
        try:
            barrier.wait(timeout=5)
            context = _sample_attestation("shared-parent")
            store.write(context, route=managed_codex_route_for_launch_context("interactive"))
        except BaseException as exc:  # pragma: no cover - propagates via outcomes
            outcomes[slot] = exc

    t1 = threading.Thread(target=_worker, args=(0,))
    t2 = threading.Thread(target=_worker, args=(1,))
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

    project_root = _isolated_state_root(tmp_path)
    store = ManagedJoinRecordStore(project_root)
    context = _sample_attestation("abc123")
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

    project_root = _isolated_state_root(tmp_path)
    store = ManagedJoinRecordStore(project_root)
    store.write(
        _sample_attestation("abc123"),
        route=managed_codex_route_for_launch_context("interactive"),
    )
    store.write(
        _sample_attestation("abc123"),
        route=managed_codex_route_for_launch_context("direct"),
    )

    loaded = store.load("abc123")
    assert loaded is not None
    attestation, route = loaded
    assert route == "parent"
    assert attestation.parent_session_id == "abc123"


def test_path_for_rejects_invalid_session_id(tmp_path: Path) -> None:
    from autoskillit.server._managed_join_attestation import ManagedJoinRecordStore

    project_root = _isolated_state_root(tmp_path)
    store = ManagedJoinRecordStore(project_root)

    with pytest.raises(ValueError):
        store.path_for("invalid session!")
