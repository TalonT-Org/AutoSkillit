from __future__ import annotations

import importlib.util
import json
import os
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path
from types import ModuleType

import pytest

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
    sleeper = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], text=True)
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
    env = os.environ.copy()
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
    env = {key: value for key, value in os.environ.items() if key != "TMPDIR"}
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
    env = os.environ.copy()
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


def test_live_reference_parser(tmp_path: Path) -> None:
    module = _load_lifecycle_module()
    proc_root = tmp_path / "proc"
    process_dir = proc_root / "123"
    process_dir.mkdir(parents=True)
    (process_dir / "environ").write_bytes(b"A=1\0TMPDIR=/one/tmp\0")
    (process_dir / "cmdline").write_bytes(
        b"pytest\0--basetemp=/two/tmp\0-o\0cache_dir=/two/cache\0"
    )
    (process_dir / "cwd").symlink_to("/five/tmp")

    assert module.scan_linux_live_references(proc_root) == {
        Path("/one/tmp"),
        Path("/two/tmp"),
        Path("/two/cache"),
        Path("/five/tmp"),
    }
    assert module.parse_ps_live_references(
        "123 pytest TMPDIR=/three/tmp --basetemp=/four/tmp -o cache_dir=/four/cache"
    ) == {Path("/three/tmp"), Path("/four/tmp"), Path("/four/cache")}


def test_pid_probe_fails_closed_on_unexpected_oserror(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _load_lifecycle_module()

    def fail_probe(pid: int, signal: int) -> None:
        raise OSError("transient probe failure")

    monkeypatch.setattr(module.os, "kill", fail_probe)

    assert module._pid_exists(12345)


@pytest.mark.skipif(sys.platform != "linux", reason="Linux /proc fail-closed behavior")
def test_reap_fails_closed_when_liveness_scan_unavailable(tmp_path: Path) -> None:
    platform_root, generation, _, _ = _layout(tmp_path)
    generation.mkdir(parents=True)
    _backdate(generation)

    result = _reap(platform_root, "--proc-root", tmp_path / "missing-proc")

    assert result.returncode == 0
    assert generation.exists()
    assert "liveness scan" in result.stderr.lower()
