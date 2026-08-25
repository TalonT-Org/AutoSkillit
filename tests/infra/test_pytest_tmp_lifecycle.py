from __future__ import annotations

import importlib.util
import json
import os
import shutil
import stat
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path
from types import ModuleType

import pytest

from autoskillit.core.runtime import (
    BoundedCandidate,
    EvidenceSource,
    PathEvidence,
    ReclamationBound,
    Revocability,
    bound_unsatisfied,
    harvest_kernel_references,
    harvest_snapshot_references,
    select_overflow,
    snapshot_referenced,
    veto_paths,
)
from tests.conftest import production_interpreter_env

pytestmark = [pytest.mark.layer("infra"), pytest.mark.medium]

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "pytest_tmp_lifecycle.py"


def _load_lifecycle_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("pytest_tmp_lifecycle", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(*args: object) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *(str(arg) for arg in args)],
        env=production_interpreter_env(),
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )


def _layout(tmp_path: Path, run_id: str = "run-a") -> tuple[Path, Path, Path, Path]:
    platform_root = tmp_path / "platform"
    platform_root.mkdir(exist_ok=True)
    user_root = platform_root / f"autoskillit-pytest-{os.getuid()}"
    generation = user_root / f"pytest-deadbeef-{run_id}"
    return platform_root, generation, generation / "tmp", generation / "cache"


def _setup(
    platform_root: Path,
    tmp_dir: Path,
    cache_dir: Path,
    *,
    owner_pid: int | None = None,
) -> subprocess.CompletedProcess[str]:
    args: list[object] = [
        "setup",
        "--root",
        platform_root,
        "--dir",
        tmp_dir,
        "--cache-dir",
        cache_dir,
    ]
    if owner_pid is not None:
        args.extend(["--owner-pid", owner_pid])
    return _run(*args)


def _reap(platform_root: Path, *extra: object) -> subprocess.CompletedProcess[str]:
    return _run(
        "reap",
        "--root",
        platform_root,
        "--grace-minutes",
        0,
        "--legacy-age-minutes",
        0,
        *extra,
    )


def _backdate(path: Path, seconds: int = 300) -> None:
    old = time.time() - seconds
    os.utime(path, (old, old), follow_symlinks=False)


def _dead_pid() -> int:
    process = subprocess.run([sys.executable, "-c", "pass"], check=True)
    return process.returncode + 99_000_000


def _write_dead_marker(generation: Path, *, start_id: str = "dead") -> Path:
    marker = generation / "owner.json"
    marker.write_text(
        json.dumps(
            {
                "pid": _dead_pid(),
                "start_id": start_id,
                "boot_id": "dead-boot",
                "created_at": time.time() - 300,
            }
        )
    )
    _backdate(marker)
    return marker


def _stop(process: subprocess.Popen[str]) -> None:
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def test_setup_creates_generation_dir(tmp_path: Path) -> None:
    platform_root, generation, tmp_dir, cache_dir = _layout(tmp_path)
    result = _setup(platform_root, tmp_dir, cache_dir, owner_pid=os.getpid())

    assert result.returncode == 0, result.stderr
    assert {path.name for path in generation.iterdir()} == {"tmp", "cache", "owner.json"}
    assert stat.S_IMODE(generation.parent.stat().st_mode) == 0o700
    marker = json.loads((generation / "owner.json").read_text())
    assert marker["pid"] == os.getpid()
    assert marker["start_id"]
    assert "boot_id" in marker
    assert isinstance(marker["created_at"], float)


@pytest.mark.parametrize("entry_kind", ["directory", "file"])
def test_setup_fails_loudly_on_existing_generation(tmp_path: Path, entry_kind: str) -> None:
    platform_root, generation, tmp_dir, cache_dir = _layout(tmp_path)
    generation.parent.mkdir(mode=0o700)
    if entry_kind == "directory":
        generation.mkdir()
        sentinel = generation / "sentinel"
        sentinel.write_text("preserve")
    else:
        generation.write_text("preserve")

    result = _setup(platform_root, tmp_dir, cache_dir)

    assert result.returncode == 2
    assert "collision" in result.stderr.lower()
    if entry_kind == "directory":
        assert sentinel.read_text() == "preserve"
    else:
        assert generation.read_text() == "preserve"


def test_marker_survives_basetemp_clearing(tmp_path: Path) -> None:
    platform_root, generation, tmp_dir, cache_dir = _layout(tmp_path)
    assert _setup(platform_root, tmp_dir, cache_dir, owner_pid=os.getpid()).returncode == 0
    shutil.rmtree(tmp_dir)
    tmp_dir.mkdir()

    assert (generation / "owner.json").exists()
    assert _reap(platform_root).returncode == 0
    assert generation.exists()
    _write_dead_marker(generation)
    assert _reap(platform_root).returncode == 0
    assert not generation.exists()


def test_reap_skips_live_owner(tmp_path: Path) -> None:
    platform_root, generation, tmp_dir, cache_dir = _layout(tmp_path)
    sleeper = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        text=True,
        env=production_interpreter_env(),
    )
    try:
        assert _setup(platform_root, tmp_dir, cache_dir, owner_pid=sleeper.pid).returncode == 0
        _backdate(generation / "owner.json")
        assert _reap(platform_root).returncode == 0
        assert generation.exists()
    finally:
        _stop(sleeper)


def test_reap_removes_dead_owner_after_grace(tmp_path: Path) -> None:
    platform_root, generation, _, _ = _layout(tmp_path)
    generation.mkdir(parents=True)
    _write_dead_marker(generation)

    assert _reap(platform_root).returncode == 0
    assert not generation.exists()


def test_reap_keeps_recent_dead_owner_within_grace(tmp_path: Path) -> None:
    platform_root, generation, _, _ = _layout(tmp_path)
    generation.mkdir(parents=True)
    marker = _write_dead_marker(generation)
    os.utime(marker, None)

    result = _run("reap", "--root", platform_root, "--grace-minutes", 5)

    assert result.returncode == 0
    assert generation.exists()


def test_reap_treats_recycled_pid_as_dead(tmp_path: Path) -> None:
    platform_root, generation, tmp_dir, cache_dir = _layout(tmp_path)
    assert _setup(platform_root, tmp_dir, cache_dir, owner_pid=os.getpid()).returncode == 0
    marker = generation / "owner.json"
    payload = json.loads(marker.read_text())
    payload["start_id"] = f"wrong-{payload['start_id']}"
    marker.write_text(json.dumps(payload))
    _backdate(marker)

    assert _reap(platform_root).returncode == 0
    assert not generation.exists()


def test_reap_age_gates_markerless_and_legacy_dirs(tmp_path: Path) -> None:
    platform_root, generation, _, _ = _layout(tmp_path)
    generation.mkdir(parents=True)
    legacy_tmp = platform_root / "pytest-tmp-phase-a-full"
    legacy_cache = platform_root / "pytest-cache-deadbeef"
    legacy_tmp.mkdir()
    legacy_cache.mkdir()

    fresh = _run("reap", "--root", platform_root, "--legacy-age-minutes", 120)
    assert fresh.returncode == 0
    assert generation.exists() and legacy_tmp.exists() and legacy_cache.exists()

    for path in (generation, legacy_tmp, legacy_cache):
        _backdate(path, seconds=3 * 60 * 60)
    assert _reap(platform_root).returncode == 0
    assert not generation.exists() and not legacy_tmp.exists() and not legacy_cache.exists()


@pytest.mark.skipif(sys.platform != "linux", reason="Linux /proc live-reference behavior")
def test_reap_vetoes_dirs_referenced_by_live_processes(tmp_path: Path) -> None:
    platform_root, generation, tmp_dir, _ = _layout(tmp_path)
    tmp_dir.mkdir(parents=True)
    _backdate(generation)
    env = production_interpreter_env()
    env["TMPDIR"] = str(tmp_dir)
    sleeper = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"], env=env, text=True
    )
    try:
        assert _reap(platform_root).returncode == 0
        assert generation.exists()
    finally:
        _stop(sleeper)
    _backdate(generation)
    assert _reap(platform_root).returncode == 0
    assert not generation.exists()


@pytest.mark.skipif(sys.platform != "linux", reason="Linux /proc cwd behavior")
def test_reap_vetoes_generation_used_as_live_cwd(tmp_path: Path) -> None:
    platform_root, generation, tmp_dir, _ = _layout(tmp_path)
    tmp_dir.mkdir(parents=True)
    _backdate(generation)
    env = {key: value for key, value in production_interpreter_env().items() if key != "TMPDIR"}
    sleeper = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        cwd=tmp_dir,
        env=env,
        text=True,
    )
    try:
        assert _reap(platform_root).returncode == 0
        assert generation.exists()
    finally:
        _stop(sleeper)
    _backdate(generation)
    assert _reap(platform_root).returncode == 0
    assert not generation.exists()


def test_reap_errors_are_nonfatal(tmp_path: Path) -> None:
    platform_root, blocked, _, _ = _layout(tmp_path, "blocked")
    other = blocked.parent / "pytest-deadbeef-other"
    blocked_child = blocked / "locked"
    blocked_child.mkdir(parents=True)
    other.mkdir()
    _backdate(blocked)
    _backdate(other)
    blocked_child.chmod(0)
    try:
        result = _reap(platform_root)
        assert result.returncode == 0
        assert not other.exists()
    finally:
        if blocked_child.exists():
            blocked_child.chmod(0o700)
            shutil.rmtree(blocked)


def test_sequential_generations_with_surviving_straggler(tmp_path: Path) -> None:
    platform_root, generation_a, tmp_a, cache_a = _layout(tmp_path, "run-a")
    _, generation_b, tmp_b, cache_b = _layout(tmp_path, "run-b")
    assert _setup(platform_root, tmp_a, cache_a, owner_pid=os.getpid()).returncode == 0
    env = production_interpreter_env()
    env["TMPDIR"] = str(tmp_a)
    writer_code = """
import os
import pathlib
import time

root = pathlib.Path(os.environ["TMPDIR"])
index = 0
while True:
    (root / f"writer-{index}").write_text("alive")
    index += 1
    time.sleep(0.01)
"""
    writer = subprocess.Popen([sys.executable, "-c", writer_code], env=env, text=True)
    try:
        deadline = time.monotonic() + 5
        while not any(tmp_a.glob("writer-*")) and time.monotonic() < deadline:
            time.sleep(0.02)
        assert any(tmp_a.glob("writer-*"))
        result = _setup(platform_root, tmp_b, cache_b, owner_pid=os.getpid())
        assert result.returncode == 0, result.stderr
        assert generation_a.exists() and generation_b.exists()
        assert not any(tmp_b.iterdir())
    finally:
        _stop(writer)
    _write_dead_marker(generation_a)
    assert _reap(platform_root).returncode == 0
    assert not generation_a.exists()
    assert generation_b.exists()


def test_setup_runs_reap_first(tmp_path: Path) -> None:
    platform_root, old_generation, _, _ = _layout(tmp_path, "old")
    _, new_generation, new_tmp, new_cache = _layout(tmp_path, "new")
    old_generation.mkdir(parents=True)
    _backdate(old_generation, seconds=3 * 60 * 60)

    result = _setup(platform_root, new_tmp, new_cache, owner_pid=os.getpid())

    assert result.returncode == 0, result.stderr
    assert not old_generation.exists()
    assert new_generation.exists()


def test_setup_rejects_split_generation_paths(tmp_path: Path) -> None:
    platform_root, _, tmp_dir, _ = _layout(tmp_path, "run-a")
    _, _, _, cache_dir = _layout(tmp_path, "run-b")

    result = _setup(platform_root, tmp_dir, cache_dir)

    assert result.returncode == 2
    assert "same generation" in result.stderr.lower()


def test_root_safety(tmp_path: Path) -> None:
    platform_root, generation, tmp_dir, cache_dir = _layout(tmp_path)
    target = tmp_path / "target"
    target.mkdir()
    generation.parent.symlink_to(target, target_is_directory=True)

    setup = _setup(platform_root, tmp_dir, cache_dir)
    reap = _reap(platform_root)

    assert setup.returncode == 2
    assert reap.returncode == 0
    assert not any(target.iterdir())

    generation.parent.unlink()
    generation.parent.mkdir(mode=0o700)
    protected = tmp_path / "protected"
    protected.mkdir()
    (protected / "sentinel").write_text("preserve")
    generation.symlink_to(protected, target_is_directory=True)
    legacy_link = platform_root / "pytest-tmp-link"
    legacy_link.symlink_to(protected, target_is_directory=True)
    assert _reap(platform_root).returncode == 0
    assert (protected / "sentinel").read_text() == "preserve"


def test_evidence_is_classified_by_source(tmp_path: Path) -> None:
    """A2: environ/cmdline yield MONOTONIC evidence; cwd/fd/maps yield REVOCABLE evidence, and
    veto_paths() of the kernel harvest returns exactly the revocable paths.

    An environment block is an execve() snapshot, not evidence of use -- see
    core/runtime/_reclamation.py's module docstring for the full rationale. Replaces the old
    test_live_reference_parser, which asserted the pre-fix combined (unclassified) scan.
    """
    proc_root = tmp_path / "proc"
    process_dir = proc_root / "123"
    process_dir.mkdir(parents=True)
    (process_dir / "environ").write_bytes(b"A=1\0TMPDIR=/one/tmp\0")
    (process_dir / "cmdline").write_bytes(
        b"pytest\0--basetemp=/two/tmp\0-o\0cache_dir=/two/cache\0"
    )
    (process_dir / "cwd").symlink_to("/five/tmp")
    (process_dir / "fd").mkdir()
    (process_dir / "fd" / "3").symlink_to("/six/tmp")
    (process_dir / "maps").write_text(
        "00400000-00401000 r-xp 00000000 08:01 123 /seven/tmp/mapped.so\n"
        "7f0000000000-7f0000001000 rw-p 00000000 00:00 0\n"
    )

    kernel_evidence = harvest_kernel_references(proc_root)
    snapshot_evidence = harvest_snapshot_references(proc_root)

    kernel_by_source: dict[EvidenceSource, list[PathEvidence]] = defaultdict(list)
    for item in kernel_evidence:
        kernel_by_source[item.source].append(item)
    for source in (EvidenceSource.PROC_CWD, EvidenceSource.PROC_FD, EvidenceSource.PROC_MAPS):
        assert len(kernel_by_source[source]) == 1, (
            f"expected exactly one {source} entry in this fixture, got "
            f"{len(kernel_by_source[source])}"
        )
    assert kernel_by_source[EvidenceSource.PROC_CWD][0].path == Path("/five/tmp")
    assert kernel_by_source[EvidenceSource.PROC_CWD][0].revocability is Revocability.REVOCABLE
    assert kernel_by_source[EvidenceSource.PROC_FD][0].path == Path("/six/tmp")
    assert kernel_by_source[EvidenceSource.PROC_FD][0].revocability is Revocability.REVOCABLE
    assert kernel_by_source[EvidenceSource.PROC_MAPS][0].path == Path("/seven/tmp/mapped.so")
    assert kernel_by_source[EvidenceSource.PROC_MAPS][0].revocability is Revocability.REVOCABLE

    snapshot_by_source: dict[EvidenceSource, set[Path]] = {}
    for item in snapshot_evidence:
        assert item.revocability is Revocability.MONOTONIC
        snapshot_by_source.setdefault(item.source, set()).add(item.path)
    assert snapshot_by_source[EvidenceSource.PROC_ENVIRON] == {Path("/one/tmp")}
    assert snapshot_by_source[EvidenceSource.PROC_CMDLINE] == {
        Path("/two/tmp"),
        Path("/two/cache"),
    }

    assert veto_paths(kernel_evidence) == {
        Path("/five/tmp"),
        Path("/six/tmp"),
        Path("/seven/tmp/mapped.so"),
    }


def test_pid_probe_fails_closed_on_unexpected_oserror(monkeypatch: pytest.MonkeyPatch) -> None:
    """A probe that cannot determine liveness must report INDETERMINATE, never DEAD -- a
    transient /proc read failure must retain the candidate, not reclaim it. Rewritten against
    _owner_liveness; _pid_exists was deleted in favor of the shared IL-0 primitives.

    os.kill() failing (EPERM or otherwise) now falls through to the /proc refinement rather
    than short-circuiting -- see test_pid_probe_eperm_refines_via_proc_mismatch for that path.
    This test covers the case where the /proc refinement *also* fails (no signal), which is
    the actual fail-closed contract the docstring describes; deterministically forced via
    monkeypatch rather than relying on pid 12345 not existing on the test machine's real /proc.
    """
    module = _load_lifecycle_module()

    def fail_probe(pid: int, signal: int) -> None:
        raise OSError("transient probe failure")

    monkeypatch.setattr(module.os, "kill", fail_probe)
    if sys.platform == "linux":
        monkeypatch.setattr(module, "read_boot_id", lambda *, proc_root: None)
    else:
        monkeypatch.setattr(
            module,
            "_macos_start_id",
            lambda pid: (_ for _ in ()).throw(OSError("no such process")),
        )

    owner: dict[str, object] = {
        "pid": 12345,
        "start_id": "irrelevant",
        "boot_id": "irrelevant",
        "created_at": time.time(),
    }
    assert module._owner_liveness(owner, Path("/proc")) is module._OwnerLiveness.INDETERMINATE


@pytest.mark.skipif(sys.platform != "linux", reason="Linux /proc refinement path")
def test_pid_probe_eperm_refines_via_proc_mismatch(monkeypatch: pytest.MonkeyPatch) -> None:
    """EPERM from os.kill() confirms existence, not failure -- it must fall through to the
    /proc boot_id/starttime refinement rather than short-circuit to INDETERMINATE. A
    kernel-authoritative mismatch there still correctly resolves to DEAD.
    """
    module = _load_lifecycle_module()

    def eperm_probe(pid: int, signal: int) -> None:
        raise PermissionError("cross-uid pid, cannot signal")

    monkeypatch.setattr(module.os, "kill", eperm_probe)
    monkeypatch.setattr(module, "is_pid_zombie", lambda pid, *, proc_root: False)
    monkeypatch.setattr(module, "read_boot_id", lambda *, proc_root: "current-boot")
    monkeypatch.setattr(module, "read_starttime_ticks", lambda pid, *, proc_root: 999999)

    owner: dict[str, object] = {
        "pid": 12345,
        "start_id": "111111",  # mismatches the mocked 999999 -- a different process reused the pid
        "boot_id": "current-boot",
        "created_at": time.time(),
    }
    assert module._owner_liveness(owner, Path("/proc")) is module._OwnerLiveness.DEAD


def test_reference_containment_resolves_symlinked_paths(tmp_path: Path) -> None:
    module = _load_lifecycle_module()
    real_root = tmp_path / "real"
    candidate = real_root / "generation"
    candidate.mkdir(parents=True)
    alias = tmp_path / "alias"
    alias.symlink_to(real_root, target_is_directory=True)

    assert module._contains_reference(candidate, {alias / "generation" / "tmp"})


@pytest.mark.skipif(sys.platform != "linux", reason="Linux /proc fail-closed behavior")
def test_reap_fails_closed_when_liveness_scan_unavailable(tmp_path: Path) -> None:
    platform_root, generation, _, _ = _layout(tmp_path)
    generation.mkdir(parents=True)
    _backdate(generation)

    result = _reap(platform_root, "--proc-root", tmp_path / "missing-proc")

    assert result.returncode == 0
    assert generation.exists()
    assert "liveness scan" in result.stderr.lower()


@pytest.mark.skipif(sys.platform != "linux", reason="Linux /proc revocable-evidence behavior")
def test_reap_reclaims_a_generation_pinned_only_by_an_immortal_environ_holder(
    tmp_path: Path,
) -> None:
    """A1: the case production hits. A dead owner, past grace, with no live pytest process --
    but an unrelated immortal daemon (e.g. dbus-daemon) inherited a stale TMPDIR= token in its
    environ and holds no cwd/fd/maps inside the generation. Only a revocable reference may
    veto; a monotonic one must not.
    """
    platform_root, generation, tmp_dir, cache_dir = _layout(tmp_path)
    generation.mkdir(parents=True)
    tmp_dir.mkdir()
    cache_dir.mkdir()
    _write_dead_marker(generation)  # dead pid (real kernel), marker backdated past grace

    proc_root = tmp_path / "proc"
    holder_dir = proc_root / "99999"
    holder_dir.mkdir(parents=True)
    (holder_dir / "environ").write_bytes(f"TMPDIR={tmp_dir}\0".encode())
    (holder_dir / "cmdline").write_bytes(b"")
    # No cwd, fd/, or maps entries for this holder -- environ is its *only* reference.

    result = _reap(platform_root, "--proc-root", proc_root)

    assert result.returncode == 0, result.stderr
    assert not generation.exists()


def test_veto_paths_rejects_monotonic_evidence() -> None:
    """A3: passing MONOTONIC evidence to veto_paths() raises rather than silently filtering,
    matching _detach_spawn_violation_reason's fail-closed posture.
    """
    monotonic = PathEvidence(Path("/one/tmp"), EvidenceSource.PROC_ENVIRON, Revocability.MONOTONIC)
    with pytest.raises(ValueError, match="monotonic"):
        veto_paths([monotonic])


def test_snapshot_referenced_accepts_monotonic_evidence(tmp_path: Path) -> None:
    """A3: the markerless consumer takes what the veto consumer refuses."""
    candidate = tmp_path / "generation"
    candidate.mkdir()
    monotonic = PathEvidence(
        candidate / "tmp", EvidenceSource.PROC_ENVIRON, Revocability.MONOTONIC
    )

    assert snapshot_referenced(candidate, [monotonic])


def test_select_overflow_bounds_by_generation_count_regardless_of_references() -> None:
    """A4: N generations over the ceiling, each provably-dead-owner and unprotected; the
    ceiling reclaims the oldest ones oldest-first regardless of any monotonic reference they
    might separately carry (which never reaches `protected` in the first place).
    """
    candidates = [
        BoundedCandidate(path=Path(f"/gen-{i}"), mtime=float(i), size_bytes=0, protected=False)
        for i in range(5)
    ]
    bound = ReclamationBound(max_generations=2)

    selected = select_overflow(candidates, bound)

    assert [c.path for c in selected] == [Path("/gen-0"), Path("/gen-1"), Path("/gen-2")]
    assert not bound_unsatisfied(candidates, selected, bound)


def test_select_overflow_bounds_by_bytes_regardless_of_references() -> None:
    """A4: the byte dimension, same shape as the generation-count dimension."""
    candidates = [
        BoundedCandidate(path=Path(f"/gen-{i}"), mtime=float(i), size_bytes=100, protected=False)
        for i in range(5)
    ]
    bound = ReclamationBound(max_bytes=250)

    selected = select_overflow(candidates, bound)

    assert [c.path for c in selected] == [Path("/gen-0"), Path("/gen-1"), Path("/gen-2")]
    assert not bound_unsatisfied(candidates, selected, bound)


def test_bound_never_reclaims_a_live_owner() -> None:
    """A4: a live-owner generation survives the bound, however far over ceiling."""
    candidates = [
        BoundedCandidate(path=Path("/live-owner"), mtime=0.0, size_bytes=0, protected=True),
    ]
    bound = ReclamationBound(max_generations=0)

    selected = select_overflow(candidates, bound)

    assert selected == []
    assert bound_unsatisfied(candidates, selected, bound)


def test_bound_never_reclaims_a_revocably_referenced_generation() -> None:
    """A4: a dead-owner generation still holding an fd/cwd reference survives the bound too --
    the bound must honour the same reference gate _reap does, or it becomes a second door
    around the reference gate.
    """
    candidates = [
        BoundedCandidate(
            path=Path("/revocably-referenced"), mtime=0.0, size_bytes=0, protected=True
        ),
    ]
    bound = ReclamationBound(max_generations=0)

    selected = select_overflow(candidates, bound)

    assert selected == []
    assert bound_unsatisfied(candidates, selected, bound)


def test_setup_fails_when_the_bound_cannot_be_satisfied(tmp_path: Path) -> None:
    """A4: every over-ceiling candidate is protected (a live owner); _setup must fail loudly
    naming the ceiling and the protected-candidate count, rather than claiming another
    generation on top.
    """
    platform_root, _, _, _ = _layout(tmp_path)
    sleeper = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        text=True,
        env=production_interpreter_env(),
    )
    try:
        _, protected_generation, protected_tmp, protected_cache = _layout(tmp_path, "protected")
        assert (
            _setup(platform_root, protected_tmp, protected_cache, owner_pid=sleeper.pid).returncode
            == 0
        )

        _, new_generation, new_tmp, new_cache = _layout(tmp_path, "new")
        result = _run(
            "setup",
            "--root",
            platform_root,
            "--dir",
            new_tmp,
            "--cache-dir",
            new_cache,
            "--owner-pid",
            os.getpid(),
            "--max-generations",
            0,
        )

        assert result.returncode == 2
        assert "ceiling" in result.stderr.lower()
        assert "1" in result.stderr
        assert not new_generation.exists()
        assert protected_generation.exists()
    finally:
        _stop(sleeper)


@pytest.mark.skipif(sys.platform != "linux", reason="Linux /proc revocable-reference behavior")
@pytest.mark.parametrize(
    ("owner_state", "has_revocable_reference", "expect_survives"),
    [
        pytest.param("alive", False, True, id="alive"),
        pytest.param("dead-within-grace", False, True, id="dead-within-grace"),
        pytest.param("dead-within-grace", True, True, id="dead-within-grace-referenced"),
        pytest.param("dead-past-grace", False, False, id="dead-past-grace"),
        pytest.param("dead-past-grace", True, True, id="dead-past-grace-referenced"),
        pytest.param("corrupt-within-grace", False, True, id="corrupt-within-grace"),
        pytest.param("corrupt-past-grace", False, False, id="corrupt-past-grace"),
        pytest.param("corrupt-past-grace", True, True, id="corrupt-past-grace-referenced"),
        pytest.param("absent-young", False, True, id="absent-young"),
        pytest.param("absent-old", False, False, id="absent-old"),
        pytest.param("absent-old", True, True, id="absent-old-referenced"),
    ],
)
def test_reap_owner_state_matrix(
    tmp_path: Path, owner_state: str, has_revocable_reference: bool, expect_survives: bool
) -> None:
    """A4b: every owner-marker state x revocable-reference combination behaves per the S1-3
    table. Two rows have no coverage elsewhere: dead-within-grace (the #4353 race protection,
    which must hold regardless of reference presence) and corrupt/unparseable (must be
    grace-gated exactly like a valid dead marker, never demoted to the weaker markerless/
    legacy-age path).
    """
    platform_root, generation, tmp_dir, cache_dir = _layout(tmp_path)
    holder: subprocess.Popen[str] | None = None

    if owner_state == "alive":
        holder = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)"],
            text=True,
            env=production_interpreter_env(),
        )
        assert _setup(platform_root, tmp_dir, cache_dir, owner_pid=holder.pid).returncode == 0
    else:
        generation.mkdir(parents=True)
        tmp_dir.mkdir()
        cache_dir.mkdir()
        if owner_state == "dead-within-grace":
            marker = _write_dead_marker(generation)
            os.utime(marker, None)
        elif owner_state == "dead-past-grace":
            # _write_dead_marker backdates by exactly 300s (5 min), which equals the 5-minute
            # grace window passed below -- _older_than's strict ">" would then be a coin flip
            # on the boundary. Push it safely further back.
            marker = _write_dead_marker(generation)
            _backdate(marker, seconds=400)
        elif owner_state == "corrupt-within-grace":
            (generation / "owner.json").write_text('{"pid": 1, "start_id": "x", "boot_id": "y"')
        elif owner_state == "corrupt-past-grace":
            marker = generation / "owner.json"
            marker.write_text('{"pid": 1, "start_id": "x", "boot_id": "y"')
            _backdate(marker, seconds=400)
        elif owner_state == "absent-old":
            _backdate(generation, seconds=3 * 60 * 60)
        elif owner_state != "absent-young":
            raise AssertionError(owner_state)

    reference_holder: subprocess.Popen[str] | None = None
    try:
        if has_revocable_reference:
            reference_holder = subprocess.Popen(
                [sys.executable, "-c", "import time; time.sleep(60)"],
                cwd=tmp_dir,
                env={k: v for k, v in production_interpreter_env().items() if k != "TMPDIR"},
                text=True,
            )
            time.sleep(0.1)  # let the child actually chdir before we scan /proc

        result = _reap(platform_root, "--grace-minutes", 5, "--legacy-age-minutes", 120)

        assert result.returncode == 0, result.stderr
        assert generation.exists() == expect_survives
    finally:
        if reference_holder is not None:
            _stop(reference_holder)
        if holder is not None:
            _stop(holder)


def test_reap_frees_bytes(tmp_path: Path) -> None:
    """A5: assert the outcome (bytes freed), not directory presence -- this assertion class
    has zero occurrences in the file today, and a reaper that correctly refuses to delete
    anything, forever, passes every `generation.exists()`-shaped assertion.
    """
    platform_root, generation, tmp_dir, cache_dir = _layout(tmp_path)
    generation.mkdir(parents=True)
    tmp_dir.mkdir()
    cache_dir.mkdir()
    payload = b"x" * 4096
    (tmp_dir / "payload.bin").write_bytes(payload)
    _write_dead_marker(generation)

    before = sum(f.stat().st_size for f in generation.rglob("*") if f.is_file())
    assert before >= len(payload)

    assert _reap(platform_root).returncode == 0

    assert not generation.exists()


@pytest.mark.skipif(sys.platform != "linux", reason="Linux zombie-state behavior")
def test_zombie_owner_is_not_treated_as_alive() -> None:
    """A6: os.kill(zombie_pid, 0) succeeds -- is_pid_zombie must still report it dead."""
    module = _load_lifecycle_module()
    pid = os.fork()
    if pid == 0:
        os._exit(0)
    try:
        time.sleep(0.2)  # let the kernel transition the child to zombie state
        owner: dict[str, object] = {
            "pid": pid,
            "start_id": "irrelevant",
            "boot_id": "irrelevant",
            "created_at": time.time(),
        }
        assert module._owner_liveness(owner, Path("/proc")) is module._OwnerLiveness.DEAD
    finally:
        os.waitpid(pid, 0)


def test_ps_sweep_evidence_is_classified_monotonic() -> None:
    """A8: macOS parity is a decision, not an omission. parse_ps_live_references always yields
    MONOTONIC evidence -- ps offers no cwd/fd/maps equivalent -- so it structurally cannot
    veto. Runs on all platforms; pure string parsing.
    """
    module = _load_lifecycle_module()

    evidence = module.parse_ps_live_references(
        "123 pytest TMPDIR=/three/tmp --basetemp=/four/tmp -o cache_dir=/four/cache"
    )

    assert {item.path for item in evidence} == {
        Path("/three/tmp"),
        Path("/four/tmp"),
        Path("/four/cache"),
    }
    assert all(item.revocability is module.Revocability.MONOTONIC for item in evidence)
    assert all(item.source is module.EvidenceSource.PS_SWEEP for item in evidence)
    with pytest.raises(ValueError, match="monotonic"):
        module.veto_paths(evidence)


def test_lifecycle_script_runs_under_a_bare_interpreter(tmp_path: Path) -> None:
    """A9: _tmpdir-setup runs concurrently with install-worktree (both are deps: of test-all),
    so the venv is not guaranteed to exist when the reaper runs. The bootstrap must work under
    a bare system interpreter with no venv on PATH.
    """
    env = production_interpreter_env()
    env.pop("VIRTUAL_ENV", None)
    venv_dir = str(Path(sys.executable).resolve().parent)
    stripped_path = os.pathsep.join(
        entry for entry in env.get("PATH", "").split(os.pathsep) if entry != venv_dir
    )
    env["PATH"] = stripped_path

    bare_python = shutil.which("python3", path=stripped_path) or shutil.which(
        "python", path=stripped_path
    )
    if bare_python is None:
        pytest.skip("no system interpreter outside the project venv found on PATH")

    platform_root, _, tmp_dir, cache_dir = _layout(tmp_path)
    result = subprocess.run(
        [
            bare_python,
            str(SCRIPT),
            "setup",
            "--root",
            str(platform_root),
            "--dir",
            str(tmp_dir),
            "--cache-dir",
            str(cache_dir),
            "--owner-pid",
            str(os.getpid()),
        ],
        env=env,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )

    assert result.returncode == 0, result.stderr
