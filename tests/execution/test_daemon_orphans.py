"""Narrow registered-stdio daemon orphan detection and reap tests."""

from __future__ import annotations

import sys

import pytest

from autoskillit.core import ProcessCleanupResult
from autoskillit.execution import (
    OrphanedAutoSkillitDaemon,
    find_orphaned_autoskillit_daemons,
    reap_orphaned_autoskillit_daemons,
)
from autoskillit.execution.process import _daemon_orphans as subject

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]

_PID = 444
_LAUNCH = "0123456789abcdef"
_ROOT = "/tmp/project"
_BOOT = "12345678-1234-1234-1234-123456789abc"


def _row() -> dict[str, object]:
    return {
        "owner_pid": 333,
        "owner_boot_id": _BOOT,
        "owner_starttime_ticks": 22,
    }


@pytest.fixture
def valid_boundaries(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(subject.sys, "platform", "linux")
    monkeypatch.setattr(subject, "_iter_proc_pids", lambda: [_PID])
    monkeypatch.setattr(subject, "_read_cmdline", lambda _pid: ("/usr/bin/autoskillit",))
    monkeypatch.setattr(subject, "_read_uid", lambda _pid: 1000)
    monkeypatch.setattr(subject.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(subject, "_read_ppid", lambda _pid: 1)
    monkeypatch.setattr(
        subject,
        "_read_environ",
        lambda _pid: {
            "AUTOSKILLIT_LAUNCH_ID": _LAUNCH,
            "AUTOSKILLIT_STATE_ROOT": _ROOT,
        },
    )
    monkeypatch.setattr(subject, "read_registry", lambda _root: {_LAUNCH: _row()})
    monkeypatch.setattr(subject, "_owner_is_dead", lambda *_args: True)
    monkeypatch.setattr(subject, "read_boot_id", lambda: _BOOT)
    monkeypatch.setattr(subject, "read_starttime_ticks", lambda _pid: 99)


def test_find_requires_complete_join_and_affirmative_owner_death(valid_boundaries) -> None:
    assert find_orphaned_autoskillit_daemons() == [
        OrphanedAutoSkillitDaemon(
            pid=_PID,
            launch_id=_LAUNCH,
            state_root=_ROOT,
            boot_id=_BOOT,
            starttime_ticks=99,
            owner_pid=333,
            owner_boot_id=_BOOT,
            owner_starttime_ticks=22,
        )
    ]


@pytest.mark.parametrize(
    ("boundary", "value"),
    [
        ("_read_cmdline", ("autoskillit", "--transport", "http")),
        ("_read_uid", 1001),
        ("_read_ppid", 2),
        ("_read_environ", None),
        ("_owner_is_dead", False),
        ("_owner_is_dead", None),
        ("read_boot_id", None),
        ("read_starttime_ticks", None),
    ],
)
def test_find_rejects_each_process_or_identity_gap(
    valid_boundaries, monkeypatch: pytest.MonkeyPatch, boundary: str, value: object
) -> None:
    monkeypatch.setattr(subject, boundary, lambda *_args: value)
    assert find_orphaned_autoskillit_daemons() == []


@pytest.mark.parametrize(
    "environment",
    [
        {},
        {"AUTOSKILLIT_LAUNCH_ID": "bad", "AUTOSKILLIT_STATE_ROOT": _ROOT},
        {"AUTOSKILLIT_LAUNCH_ID": _LAUNCH},
        {"AUTOSKILLIT_LAUNCH_ID": _LAUNCH, "AUTOSKILLIT_STATE_ROOT": "relative"},
    ],
)
def test_find_rejects_invalid_environment_join(
    valid_boundaries, monkeypatch: pytest.MonkeyPatch, environment: dict[str, str]
) -> None:
    monkeypatch.setattr(subject, "_read_environ", lambda _pid: environment)
    assert find_orphaned_autoskillit_daemons() == []


@pytest.mark.parametrize(
    "row",
    [
        None,
        {},
        {"owner_pid": 333, "owner_boot_id": _BOOT},
        {"owner_pid": "333", "owner_boot_id": _BOOT, "owner_starttime_ticks": 22},
    ],
)
def test_find_rejects_absent_or_incomplete_registry_identity(
    valid_boundaries, monkeypatch: pytest.MonkeyPatch, row: object
) -> None:
    monkeypatch.setattr(subject, "read_registry", lambda _root: {_LAUNCH: row})
    assert find_orphaned_autoskillit_daemons() == []


def test_non_linux_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(subject.sys, "platform", "darwin")
    monkeypatch.setattr(subject, "_iter_proc_pids", lambda: pytest.fail("must not scan"))
    assert find_orphaned_autoskillit_daemons() == []


def test_owner_probe_distinguishes_live_dead_and_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(subject, "read_boot_id", lambda: _BOOT)
    monkeypatch.setattr(subject, "read_starttime_ticks", lambda _pid: 22)
    assert subject._owner_is_dead(333, _BOOT, 22) is False
    assert subject._owner_is_dead(333, "malformed", 22) is None
    assert subject._owner_is_dead(333, "abcdefab-cdef-cdef-cdef-abcdefabcdef", 22) is True

    monkeypatch.setattr(subject, "read_starttime_ticks", lambda _pid: 23)
    assert subject._owner_is_dead(333, _BOOT, 22) is True

    monkeypatch.setattr(subject, "read_starttime_ticks", lambda _pid: None)
    monkeypatch.setattr(
        subject.os,
        "kill",
        lambda *_args: (_ for _ in ()).throw(ProcessLookupError),
    )
    assert subject._owner_is_dead(333, _BOOT, 22) is True
    monkeypatch.setattr(subject.os, "kill", lambda *_args: (_ for _ in ()).throw(PermissionError))
    assert subject._owner_is_dead(333, _BOOT, 22) is None


def test_reap_revalidates_every_predicate_before_signaling(
    valid_boundaries, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidate = find_orphaned_autoskillit_daemons()[0]
    monkeypatch.setattr(subject, "_read_ppid", lambda _pid: 2)
    monkeypatch.setattr(subject, "kill_process_tree", lambda *_a, **_k: pytest.fail("signaled"))
    assert reap_orphaned_autoskillit_daemons([candidate])[0].action == "skipped"


@pytest.mark.parametrize(
    ("cleanup", "action"),
    [
        (
            ProcessCleanupResult(root_pid=_PID, observation_complete=True),
            "terminated",
        ),
        (
            ProcessCleanupResult(root_pid=_PID, observation_complete=True, identity_refused=True),
            "skipped",
        ),
        (
            ProcessCleanupResult(root_pid=_PID, observation_complete=True, survivor_pids=(_PID,)),
            "incomplete",
        ),
        (
            ProcessCleanupResult(
                root_pid=_PID, observation_complete=True, access_denied_pids=(_PID,)
            ),
            "incomplete",
        ),
    ],
)
def test_reap_classifies_verified_cleanup(
    valid_boundaries,
    monkeypatch: pytest.MonkeyPatch,
    cleanup: ProcessCleanupResult,
    action: str,
) -> None:
    candidate = find_orphaned_autoskillit_daemons()[0]
    calls: list[tuple[int, dict[str, object]]] = []

    def fake_kill(pid: int, **kwargs: object) -> ProcessCleanupResult:
        calls.append((pid, kwargs))
        return cleanup

    monkeypatch.setattr(subject, "kill_process_tree", fake_kill)
    result = reap_orphaned_autoskillit_daemons([candidate])[0]
    assert result.action == action
    assert calls == [
        (
            _PID,
            {"expected_boot_id": _BOOT, "expected_starttime_ticks": 99},
        )
    ]


def test_reap_logs_identity_refusal(valid_boundaries, monkeypatch: pytest.MonkeyPatch) -> None:
    candidate = find_orphaned_autoskillit_daemons()[0]
    events: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setattr(
        subject,
        "kill_process_tree",
        lambda *_a, **_k: ProcessCleanupResult(root_pid=_PID, identity_refused=True),
    )
    monkeypatch.setattr(
        subject.logger, "info", lambda event, **fields: events.append((event, fields))
    )

    assert reap_orphaned_autoskillit_daemons([candidate])[0].action == "skipped"
    assert events == [("daemon_orphan_reap_skipped", {"pid": _PID})]


def test_incomplete_candidate_remains_discoverable(
    valid_boundaries, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidate = find_orphaned_autoskillit_daemons()[0]
    monkeypatch.setattr(
        subject,
        "kill_process_tree",
        lambda *_a, **_k: ProcessCleanupResult(root_pid=_PID, survivor_pids=(_PID,)),
    )
    assert reap_orphaned_autoskillit_daemons([candidate])[0].action == "incomplete"
    assert find_orphaned_autoskillit_daemons() == [candidate]


def test_registered_stdio_command_shape_is_exact() -> None:
    assert subject._is_registered_stdio_command(("/usr/bin/autoskillit",))
    assert subject._is_registered_stdio_command((sys.executable, "/usr/bin/autoskillit"))
    assert subject._is_registered_stdio_command(("/usr/bin/python3.12", "/usr/bin/autoskillit"))
    assert not subject._is_registered_stdio_command(("autoskillit", "serve"))
    assert not subject._is_registered_stdio_command((sys.executable, "-m", "autoskillit"))
    assert not subject._is_registered_stdio_command(("python-wrapper", "autoskillit"))
