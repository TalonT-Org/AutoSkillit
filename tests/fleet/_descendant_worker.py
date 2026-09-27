"""Shared funnel-spawned worker script + identity helpers for owner-scope tests.

Used by test_dispatch_descendant_settlement.py and
test_fleet_e2e_codex_dispatch_identity.py: both drive a stand-in process
(a fake ``claude``/``codex`` binary) that funnel-spawns this worker into an
inherited owner scope, then verify owner-scope settlement reaps it. Identity
is recorded and compared via raw ``/proc`` tick counts (boot_id +
starttime_ticks), never a wall-clock-derived value such as
``psutil.Process().create_time()`` — the latter drifts under this host's
clock corrections and produces false "already dead" reads for a still-live
process sampled minutes apart.
"""

from __future__ import annotations

import json
from pathlib import Path

import psutil

from autoskillit.core import is_session_alive

_WORKER_SCRIPT = """\
import json
import os
import time

from autoskillit.core import read_boot_id, read_starttime_ticks


def _identity_payload():
    pid = os.getpid()
    return json.dumps(
        {"pid": pid, "boot_id": read_boot_id(), "starttime_ticks": read_starttime_ticks(pid)}
    )


def _publish(path, payload):
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(payload)
    os.replace(tmp, path)


def _heartbeat(seconds=120.0):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        time.sleep(1.0)


test_dir = os.environ["DESCENDANT_TEST_DIR"]
_publish(os.path.join(test_dir, "worker_identity.json"), _identity_payload())

child = os.fork()
if child == 0:
    _publish(os.path.join(test_dir, "grandchild_identity.json"), _identity_payload())
    _heartbeat()
    raise SystemExit(0)

_heartbeat()
"""


def write_descendant_worker_script(bin_dir: Path) -> Path:
    bin_dir.mkdir(parents=True, exist_ok=True)
    script_path = bin_dir / "_descendant_worker.py"
    script_path.write_text(_WORKER_SCRIPT, encoding="utf-8")
    return script_path


def read_identity(path: Path) -> tuple[int, str, int] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return int(data["pid"]), str(data["boot_id"]), int(data["starttime_ticks"])


def is_identity_alive(identity: tuple[int, str, int]) -> bool:
    pid, boot_id, starttime_ticks = identity
    return is_session_alive(pid, boot_id, starttime_ticks)


def kill_identity_fenced(path: Path) -> None:
    identity = read_identity(path)
    if identity is None or not is_identity_alive(identity):
        return
    try:
        proc = psutil.Process(identity[0])
        proc.kill()
        proc.wait(timeout=2)
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.TimeoutExpired):
        pass
