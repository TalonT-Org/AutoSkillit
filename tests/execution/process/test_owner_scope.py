"""Tests for owner-scope registration and settlement.

See ``execution/process/_lifecycle/owner_scope.py`` and
``_lifecycle/owned_group.py``'s ``_resolve_owner_scope``/``_register_tether``:
a scope binds every funnel-spawned descendant of an owner to a token, so the
owner settles exactly its own tree by registration rather than by ancestry,
process group, or an environment scan.
"""

from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
import textwrap
import threading
import time
import uuid
from contextlib import suppress
from pathlib import Path
from typing import NamedTuple

import psutil
import pytest
import structlog.testing

import autoskillit.execution.process._lifecycle.owner_scope as _patch_owner_scope
from autoskillit.core import (
    OWNER_SCOPE_DIR_ENV_VAR,
    OWNER_SCOPE_ENV_VAR,
    read_boot_id,
    read_starttime_ticks,
)
from autoskillit.core.types import RetryReason, SkillResult
from autoskillit.execution.process import (
    OwnerScope,
    OwnerScopeSealedError,
    OwnerScopeSettlement,
    TetherSpec,
    dispatch_scope_tokens,
    is_owner_scope_sealed,
    new_dispatch_owner_scope_token,
    seal_owner_scope,
    settle_owner_scope,
    spawn_owned_process,
    sweep_orphaned_tethers,
)
from autoskillit.execution.process._lifecycle import owned_group
from autoskillit.execution.process._process_tether import (
    DEFAULT_TETHER_CEILING_SECONDS,
    TetherRecord,
    sealed_scope_dir,
    update_tether_workload,
    write_tether,
)
from tests.conftest import production_interpreter_env
from tests.execution._process_group_helpers import (
    GROUP_ESCAPING_DESCENDANTS_SCRIPT,
    _cleanup_process_identities,
)

pytestmark = [
    pytest.mark.layer("execution"),
    pytest.mark.medium,
    pytest.mark.skipif(sys.platform != "linux", reason="Linux only"),
]


def _sleeper_cmd(seconds: float = 30.0) -> list[str]:
    return [sys.executable, "-c", f"import time; time.sleep({seconds})"]


def _scoped_env(token: str, tether_dir: Path, **extra: str) -> dict[str, str]:
    env = production_interpreter_env()
    env[OWNER_SCOPE_ENV_VAR] = token
    env[OWNER_SCOPE_DIR_ENV_VAR] = str(tether_dir)
    env.update(extra)
    return env


def _env_without_scope() -> dict[str, str]:
    env = production_interpreter_env()
    env.pop(OWNER_SCOPE_ENV_VAR, None)
    env.pop(OWNER_SCOPE_DIR_ENV_VAR, None)
    return env


def _read_single_tether(tether_dir: Path) -> dict[str, object]:
    files = list(tether_dir.glob("*.json"))
    assert len(files) == 1, f"expected exactly one tether under {tether_dir}, found {files}"
    return json.loads(files[0].read_text())


def _wait_for_death(pid: int, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while psutil.pid_exists(pid) and time.monotonic() < deadline:
        time.sleep(0.1)
    assert not psutil.pid_exists(pid), f"pid {pid} should be dead"


def _wait_for_ready_json(path: Path, timeout: float = 5.0) -> dict[str, object]:
    deadline = time.monotonic() + timeout
    while not path.is_file() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert path.is_file(), f"{path} was never written"
    return json.loads(path.read_text())


def _write_scoped_tether(
    tether_dir: Path,
    token: str,
    *,
    child_pid: int,
    child_starttime_ticks: int | None = None,
    workload_pid: int | None = None,
    workload_starttime_ticks: int | None = None,
    not_after: float | None = None,
) -> Path:
    record = TetherRecord(
        child_pid=child_pid,
        child_pgid=child_pid,
        child_starttime_ticks=(
            child_starttime_ticks
            if child_starttime_ticks is not None
            else (read_starttime_ticks(child_pid) or 0)
        ),
        boot_id=read_boot_id() or "",
        spawner_pid=os.getpid(),
        spawner_starttime_ticks=read_starttime_ticks(os.getpid()) or 0,
        spawned_at_ns=time.time_ns(),
        not_after=not_after if not_after is not None else time.time() + 3600.0,
        origin="test",
        workload_pid=workload_pid,
        workload_starttime_ticks=workload_starttime_ticks,
        owner_scope=token,
    )
    return write_tether(record, tether_dir)


class _DaemonIdentity(NamedTuple):
    pid: int
    create_time: float
    starttime_ticks: int


_DOUBLE_FORK_DAEMON_SCRIPT = textwrap.dedent("""\
    import json, os, sys, time
    from pathlib import Path

    import psutil

    from autoskillit.core import read_starttime_ticks

    ready_path = Path(sys.argv[1])
    seconds = float(sys.argv[2])
    if os.fork() > 0:
        sys.exit(0)
    os.setsid()
    if os.fork() > 0:
        sys.exit(0)
    record = json.dumps({
        "pid": os.getpid(),
        "create_time": psutil.Process().create_time(),
        "starttime_ticks": read_starttime_ticks(os.getpid()),
    })
    temporary_path = ready_path.with_name(f"{ready_path.name}.tmp")
    temporary_path.write_text(record)
    temporary_path.replace(ready_path)
    time.sleep(seconds)
""")


def _spawn_double_forked_daemon(work_dir: Path, *, seconds: float = 60.0) -> _DaemonIdentity:
    """Spawn a process reparented to init: ppid-detached, with no funnel tether.

    *work_dir* holds only this helper's own script and ready file — callers that
    also glob a tether directory for ``*.json`` files must pass a dedicated
    subdirectory, not that same tether directory.
    """
    work_dir.mkdir(parents=True, exist_ok=True)
    unique = uuid.uuid4().hex
    script = work_dir / f"double_fork_daemon_{unique}.py"
    script.write_text(_DOUBLE_FORK_DAEMON_SCRIPT)
    ready_path = work_dir / f"double-fork-ready-{unique}.json"
    launcher = subprocess.Popen(
        [sys.executable, str(script), str(ready_path), str(seconds)],
        env=production_interpreter_env(),
    )
    launcher.wait(timeout=5)
    record = _wait_for_ready_json(ready_path)
    return _DaemonIdentity(
        pid=int(record["pid"]),
        create_time=float(record["create_time"]),
        starttime_ticks=int(record["starttime_ticks"]),
    )


class TestScopedSpawnWinsOverTetherSpecDir:
    def test_scoped_spawn_writes_into_scope_dir_ignoring_tether_spec(self, tmp_path: Path) -> None:
        token = new_dispatch_owner_scope_token("scope-wins")
        scope_dir = tmp_path / "scope"
        other_dir = tmp_path / "other"
        owner = spawn_owned_process(
            _sleeper_cmd(),
            start_new_session=True,
            env=_scoped_env(token, scope_dir),
            tether=TetherSpec(origin="test", ceiling_seconds=60.0, tether_dir=other_dir),
        )
        try:
            data = _read_single_tether(scope_dir)
            assert data["owner_scope"] == token
            assert data["child_pid"] == owner.pid
            assert not other_dir.exists()
        finally:
            owner.settle_evidence()


class TestAmbientScopeInheritance:
    @pytest.mark.parametrize(
        "use_explicit_env", [False, True], ids=["env-none", "env-dict-without-token"]
    )
    def test_spawn_inherits_ambient_scope_when_child_env_lacks_token(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, use_explicit_env: bool
    ) -> None:
        token = new_dispatch_owner_scope_token("ambient")
        scope_dir = tmp_path / "scope"
        monkeypatch.setenv(OWNER_SCOPE_ENV_VAR, token)
        monkeypatch.setenv(OWNER_SCOPE_DIR_ENV_VAR, str(scope_dir))

        env = _env_without_scope() if use_explicit_env else None
        owner = spawn_owned_process(
            _sleeper_cmd(),
            start_new_session=True,
            env=env,
            tether=TetherSpec(origin="test", ceiling_seconds=60.0, tether_dir=tmp_path / "unused"),
        )
        try:
            data = _read_single_tether(scope_dir)
            assert data["owner_scope"] == token
            assert data["child_pid"] == owner.pid
            assert not (tmp_path / "unused").exists()
        finally:
            owner.settle_evidence()


class TestExplicitScopeWinsOverAmbient:
    def test_explicit_child_env_scope_wins_over_ambient(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ambient_token = new_dispatch_owner_scope_token("ambient-loses")
        ambient_dir = tmp_path / "ambient"
        explicit_token = new_dispatch_owner_scope_token("explicit-wins")
        explicit_dir = tmp_path / "explicit"
        monkeypatch.setenv(OWNER_SCOPE_ENV_VAR, ambient_token)
        monkeypatch.setenv(OWNER_SCOPE_DIR_ENV_VAR, str(ambient_dir))

        owner = spawn_owned_process(
            _sleeper_cmd(),
            start_new_session=True,
            env=_scoped_env(explicit_token, explicit_dir),
            tether=TetherSpec(origin="test", ceiling_seconds=60.0),
        )
        try:
            data = _read_single_tether(explicit_dir)
            assert data["owner_scope"] == explicit_token
            assert not ambient_dir.exists()
        finally:
            owner.settle_evidence()


class TestNoScopeUsesTetherSpecDir:
    def test_no_scope_anywhere_falls_back_to_tether_spec_dir(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv(OWNER_SCOPE_ENV_VAR, raising=False)
        monkeypatch.delenv(OWNER_SCOPE_DIR_ENV_VAR, raising=False)

        owner = spawn_owned_process(
            _sleeper_cmd(),
            start_new_session=True,
            tether=TetherSpec(origin="test", ceiling_seconds=60.0, tether_dir=tmp_path),
        )
        try:
            data = _read_single_tether(tmp_path)
            assert data["owner_scope"] is None
        finally:
            owner.settle_evidence()


def _wait_for_escaping_identities(path: Path, timeout: float = 5.0) -> dict[int, float]:
    deadline = time.monotonic() + timeout
    while not path.is_file() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert path.is_file(), f"{path} was never written"
    return {int(r["pid"]): float(r["create_time"]) for r in json.loads(path.read_text())}


class TestSettleOwnerScopeKillsEscapingDescendants:
    @pytest.mark.skipif(os.name != "posix", reason="POSIX signals required")
    def test_settle_kills_setsid_child_and_ppid_grandchild_leaving_bystanders_alive(
        self, tmp_path: Path
    ) -> None:
        """Group-escaping, SIGTERM-ignoring grandchildren force the SIGKILL escalation."""
        token = new_dispatch_owner_scope_token("escalation")
        scope_dir = tmp_path / "scope"
        ready_path = tmp_path / "escaping-identities.json"
        script = tmp_path / "group_escaping_owner.py"
        script.write_text(GROUP_ESCAPING_DESCENDANTS_SCRIPT)

        owner = spawn_owned_process(
            [sys.executable, str(script), str(ready_path), "1", "hold"],
            start_new_session=True,
            env=_scoped_env(token, scope_dir),
            tether=TetherSpec(origin="test", ceiling_seconds=60.0),
        )
        bystander = spawn_owned_process(
            _sleeper_cmd(),
            start_new_session=True,
            env=production_interpreter_env(),
            tether=TetherSpec(origin="test", ceiling_seconds=60.0, tether_dir=scope_dir),
        )
        daemon = _spawn_double_forked_daemon(tmp_path / "daemon")
        grandchildren: dict[int, float] = {}
        try:
            grandchildren = _wait_for_escaping_identities(ready_path)

            settlement = settle_owner_scope(scope_dir, token, seal=True, timeout=10.0)

            assert settlement.supported is True
            assert settlement.complete is True
            assert owner.pid in settlement.reaped_pids
            _wait_for_death(owner.pid)
            for pid in grandchildren:
                _wait_for_death(pid)

            remaining = list(scope_dir.glob("*.json"))
            assert len(remaining) == 1
            assert json.loads(remaining[0].read_text())["child_pid"] == bystander.pid
            assert psutil.pid_exists(bystander.pid)
            assert psutil.pid_exists(daemon.pid)
        finally:
            _cleanup_process_identities(grandchildren)
            with suppress(Exception):
                owner.process.kill()
                owner.process.wait(timeout=2)
            bystander.settle_evidence()
            _cleanup_process_identities({daemon.pid: daemon.create_time})


class TestSettleOwnerScopeIdentityFencing:
    def test_settle_does_not_kill_identity_mismatched_target(self, tmp_path: Path) -> None:
        token = new_dispatch_owner_scope_token("mismatch")
        child = subprocess.Popen(
            _sleeper_cmd(), start_new_session=True, env=production_interpreter_env()
        )
        try:
            _write_scoped_tether(
                tmp_path, token, child_pid=child.pid, child_starttime_ticks=999_999_999
            )

            settlement = settle_owner_scope(tmp_path, token, seal=False, timeout=10.0)

            assert psutil.pid_exists(child.pid)
            assert settlement.supported is True
            assert settlement.complete is True
            assert not settlement.reaped_pids
            assert [o.outcome for o in settlement.outcomes] == ["identity_mismatch"]
            assert list(tmp_path.glob("*.json")) == []
        finally:
            with suppress(Exception):
                child.kill()
                child.wait(timeout=2)


_RESPAWNING_SCOPED_CHILD_SCRIPT = textwrap.dedent("""\
    import json, os, signal, sys, time
    from pathlib import Path

    from autoskillit.execution.process import spawn_owned_process
    from autoskillit.execution.process._process_tether import TetherSpec

    ready_path = Path(sys.argv[1])
    handler_ready_path = Path(sys.argv[2])


    def _on_term(signum, frame):
        del signum, frame
        owner = spawn_owned_process(
            [sys.executable, "-c", "import time; time.sleep(60)"],
            start_new_session=True,
            tether=TetherSpec(origin="test", ceiling_seconds=60.0),
        )
        temporary_path = ready_path.with_name(f"{ready_path.name}.tmp")
        temporary_path.write_text(json.dumps({"gen1_pid": os.getpid(), "gen2_pid": owner.pid}))
        temporary_path.replace(ready_path)
        os._exit(0)


    signal.signal(signal.SIGTERM, _on_term)
    handler_ready_path.write_text("ready")
    time.sleep(60)
""")


class TestSettleOwnerScopeRepeatUntilStable:
    def test_settle_reaps_a_scoped_child_respawned_from_a_sigterm_handler(
        self, tmp_path: Path
    ) -> None:
        """Mirrors the production sequence: an unsealed mid-dispatch settle followed
        by the final sealed settle at scope exit — see ``owner_scope()``."""
        token = new_dispatch_owner_scope_token("respawn")
        scope_dir = tmp_path / "scope"
        ready_path = tmp_path / "gen-ready.json"
        handler_ready_path = tmp_path / "handler-ready"
        script = tmp_path / "respawning_scoped_child.py"
        script.write_text(_RESPAWNING_SCOPED_CHILD_SCRIPT)

        gen1 = spawn_owned_process(
            [sys.executable, str(script), str(ready_path), str(handler_ready_path)],
            start_new_session=True,
            env=_scoped_env(token, scope_dir),
            tether=TetherSpec(origin="test", ceiling_seconds=60.0),
        )
        gen2_pid: int | None = None
        try:
            # Wait for the SIGTERM handler to be installed — the child's own
            # import of autoskillit.execution.process can otherwise still be in
            # progress when settlement's first pass signals it, leaving the
            # default disposition (immediate termination) to fire instead.
            deadline = time.monotonic() + 5.0
            while not handler_ready_path.is_file() and time.monotonic() < deadline:
                time.sleep(0.02)
            assert handler_ready_path.is_file(), "gen1 never installed its SIGTERM handler"

            settlement1 = settle_owner_scope(scope_dir, token, seal=False, timeout=8.0)
            record = _wait_for_ready_json(ready_path)
            gen2_pid = record["gen2_pid"]
            settlement2 = settle_owner_scope(scope_dir, token, seal=True, timeout=8.0)

            assert gen1.pid in settlement1.reaped_pids or gen1.pid in settlement2.reaped_pids
            assert gen2_pid in settlement1.reaped_pids or gen2_pid in settlement2.reaped_pids
            _wait_for_death(gen1.pid)
            _wait_for_death(gen2_pid)
            assert is_owner_scope_sealed(scope_dir, token) is True
        finally:
            with suppress(ProcessLookupError, OSError):
                os.kill(gen1.pid, signal.SIGKILL)
            if gen2_pid is not None:
                with suppress(ProcessLookupError, OSError):
                    os.kill(gen2_pid, signal.SIGKILL)


class TestOwnerScopeSeal:
    def test_spawn_into_sealed_scope_raises_before_popen(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        token = new_dispatch_owner_scope_token("sealed-before")
        scope_dir = tmp_path / "scope"
        seal_owner_scope(scope_dir, token)

        popen_calls: list[object] = []
        monkeypatch.setattr(
            owned_group.subprocess,
            "Popen",
            lambda *args, **kwargs: popen_calls.append((args, kwargs)),
        )

        with pytest.raises(OwnerScopeSealedError):
            spawn_owned_process(
                _sleeper_cmd(),
                start_new_session=True,
                env=_scoped_env(token, scope_dir),
                tether=TetherSpec(origin="test", ceiling_seconds=60.0),
            )

        assert popen_calls == []

    def test_seal_race_after_write_tether_kills_child_and_raises(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        token = new_dispatch_owner_scope_token("sealed-race")
        scope_dir = tmp_path / "scope"
        original_write_tether = owned_group.write_tether
        captured: dict[str, int] = {}

        def _write_then_seal(record: TetherRecord, tether_dir: Path) -> Path:
            captured["pid"] = record.child_pid
            path = original_write_tether(record, tether_dir)
            seal_owner_scope(scope_dir, token)
            return path

        monkeypatch.setattr(owned_group, "write_tether", _write_then_seal)

        with pytest.raises(OwnerScopeSealedError):
            spawn_owned_process(
                _sleeper_cmd(),
                start_new_session=True,
                env=_scoped_env(token, scope_dir),
                tether=TetherSpec(origin="test", ceiling_seconds=60.0),
            )

        assert "pid" in captured
        _wait_for_death(captured["pid"])
        assert list(scope_dir.glob("*.json")) == []


class TestSweepExpiresSealMarkers:
    def test_sweep_removes_expired_seal_markers_and_keeps_fresh_ones(self, tmp_path: Path) -> None:
        old_token = new_dispatch_owner_scope_token("sweep-old")
        fresh_token = new_dispatch_owner_scope_token("sweep-fresh")
        seal_owner_scope(tmp_path, old_token)
        seal_owner_scope(tmp_path, fresh_token)
        old_marker = sealed_scope_dir(tmp_path) / f"{old_token}.sealed"
        old_time = time.time() - DEFAULT_TETHER_CEILING_SECONDS - 60.0
        os.utime(old_marker, (old_time, old_time))

        sweep_orphaned_tethers(tmp_path, min_age_seconds=0.0)

        assert not old_marker.exists()
        assert is_owner_scope_sealed(tmp_path, fresh_token) is True


class TestSettleOwnerScopeUnsupportedPlatform:
    def test_settle_off_linux_leaves_processes_untouched(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        token = new_dispatch_owner_scope_token("darwin")
        child = subprocess.Popen(
            _sleeper_cmd(), start_new_session=True, env=production_interpreter_env()
        )
        try:
            _write_scoped_tether(tmp_path, token, child_pid=child.pid)
            monkeypatch.setattr(_patch_owner_scope.sys, "platform", "darwin")

            settlement = settle_owner_scope(tmp_path, token, seal=False, timeout=5.0)

            assert settlement.supported is False
            assert settlement.complete is False
            assert psutil.pid_exists(child.pid)
            assert len(list(tmp_path.glob("*.json"))) == 1
        finally:
            with suppress(Exception):
                child.kill()
                child.wait(timeout=2)


class TestNewDispatchOwnerScopeToken:
    def test_format_and_uniqueness(self) -> None:
        pattern = re.compile(r"^dispatch-abc-[0-9a-f]{12}$")
        tokens = {new_dispatch_owner_scope_token("abc") for _ in range(5)}
        assert len(tokens) == 5
        assert all(pattern.fullmatch(token) for token in tokens)

    def test_rejects_invalid_dispatch_id(self) -> None:
        with pytest.raises(ValueError):
            new_dispatch_owner_scope_token("abc/def")

    def test_rejects_empty_dispatch_id(self) -> None:
        with pytest.raises(ValueError):
            new_dispatch_owner_scope_token("")


class TestSettleOwnerScopeWaitsForWorkloadIdentity:
    def test_settle_waits_for_workload_identity_before_removing_tether(
        self, tmp_path: Path
    ) -> None:
        """Stands in for a resolved PTY workload: the wrapper is the tether's
        ``child_pid``, the escaped double-forked process is its ``workload_pid``,
        applied late via ``update_tether_workload`` while settlement is already
        underway."""
        token = new_dispatch_owner_scope_token("workload-window")
        wrapper = subprocess.Popen(
            _sleeper_cmd(), start_new_session=True, env=production_interpreter_env()
        )
        workload = _spawn_double_forked_daemon(tmp_path / "daemon")
        try:
            path = _write_scoped_tether(tmp_path, token, child_pid=wrapper.pid)

            def _apply_workload_after_delay() -> None:
                time.sleep(0.5)
                update_tether_workload(path, workload.pid, workload.starttime_ticks)

            updater = threading.Thread(target=_apply_workload_after_delay)
            updater.start()
            settlement = settle_owner_scope(tmp_path, token, seal=False, timeout=10.0)
            updater.join(timeout=5.0)

            assert settlement.complete is True
            assert workload.pid in settlement.reaped_pids
            _wait_for_death(wrapper.pid)
            _wait_for_death(workload.pid)
            assert list(tmp_path.glob("*.json")) == []
        finally:
            with suppress(Exception):
                wrapper.kill()
                wrapper.wait(timeout=2)
            _cleanup_process_identities({workload.pid: workload.create_time})


class TestOwnerScopeSettleRetry:
    @pytest.mark.anyio
    async def test_settle_records_incomplete_without_retrying_or_raising(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls = 0

        def _boom(*args: object, **kwargs: object) -> OwnerScopeSettlement:
            nonlocal calls
            calls += 1
            raise RuntimeError("simulated settlement failure")

        monkeypatch.setattr(_patch_owner_scope, "settle_owner_scope", _boom)
        scope = OwnerScope(token=new_dispatch_owner_scope_token("retry"), tether_dir=tmp_path)

        with structlog.testing.capture_logs() as cap_logs:
            settlement = await scope.settle_descendants(seal=False)

        assert calls == 1
        assert settlement.complete is False
        assert any(entry.get("event") == "owner_scope_settlement_incomplete" for entry in cap_logs)
        assert any(entry.get("event") == "owner_scope_settlement_failed" for entry in cap_logs)


class TestDispatchScopeTokens:
    def test_matches_exact_dispatch_run_only(self, tmp_path: Path) -> None:
        token_abc = new_dispatch_owner_scope_token("abc")
        token_abc_def = new_dispatch_owner_scope_token("abc-def")
        _write_scoped_tether(tmp_path, token_abc, child_pid=os.getpid())
        _write_scoped_tether(tmp_path, token_abc_def, child_pid=os.getpid())

        assert dispatch_scope_tokens(tmp_path, "abc") == {token_abc}


class TestFoldCleanupEvidence:
    def test_marks_cleanup_incomplete_only_for_supported_incomplete_settlements(self) -> None:
        base_result = SkillResult(
            success=True,
            result="done",
            session_id="s1",
            subtype="success",
            is_error=False,
            exit_code=0,
            needs_retry=False,
            retry_reason=RetryReason.NONE,
            stderr="",
        )
        scope = OwnerScope(
            token=new_dispatch_owner_scope_token("fold"), tether_dir=Path("/nonexistent")
        )

        assert scope.fold_cleanup_evidence(base_result).infra.cleanup_incomplete is False

        scope.settlements.append(OwnerScopeSettlement(token=scope.token, supported=False))
        assert scope.fold_cleanup_evidence(base_result).infra.cleanup_incomplete is False

        scope.settlements.append(
            OwnerScopeSettlement(token=scope.token, supported=True, converged=False)
        )
        assert scope.fold_cleanup_evidence(base_result).infra.cleanup_incomplete is True
