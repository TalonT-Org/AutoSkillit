#!/usr/bin/env python3
"""Create invocation-unique pytest temp generations and reap provably dead ones.

Bootstraps `src/` onto sys.path so this script can import the IL-0 reclamation-evidence
primitives (core/runtime/_reclamation.py, core/runtime/_linux_proc.py) without requiring the
project venv -- both modules are stdlib-only, the same guarantee core/AGENTS.md makes for hook
subprocesses. See core/runtime/_reclamation.py's module docstring for the evidence-classification
rationale (REVOCABLE vs MONOTONIC) this reaper's retention logic depends on.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shlex
import shutil
import stat
import subprocess
import sys
import time
from collections.abc import Iterable, Sequence
from enum import StrEnum
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING


def _load_standalone_module(name: str, path: Path) -> ModuleType:
    """Load one stdlib-only IL-0 leaf without executing package initializers."""
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load standalone module {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


if TYPE_CHECKING:
    from autoskillit.core import StoreCapacityExhaustedError, default_space_probe
    from autoskillit.core.runtime import (
        PYTEST_GENERATION_NAME_RE,
        SESSION_STALE_SECONDS,
        BoundedCandidate,
        EvidenceSource,
        LivenessScanUnavailable,
        PathEvidence,
        ReclamationBound,
        Revocability,
        bound_unsatisfied,
        harvest_kernel_references,
        harvest_snapshot_references,
        is_pid_zombie,
        read_boot_id,
        read_starttime_ticks,
        select_overflow,
        snapshot_referenced,
        user_generation_root,
        veto_paths,
    )
else:
    core_root = Path(__file__).resolve().parent.parent / "src" / "autoskillit" / "core"
    capacity = _load_standalone_module("_autoskillit_capacity", core_root / "_capacity.py")
    linux_proc = _load_standalone_module(
        "_autoskillit_linux_proc", core_root / "runtime" / "_linux_proc.py"
    )
    reclamation = _load_standalone_module(
        "_autoskillit_reclamation", core_root / "runtime" / "_reclamation.py"
    )

    class StoreCapacityExhaustedError(RuntimeError):
        """Standalone capacity fault used before the project environment exists."""

        def __init__(
            self,
            *,
            path: Path,
            free_bytes: int,
            total_bytes: int,
            remedy: str,
        ) -> None:
            self.path = path
            self.free_bytes = free_bytes
            self.total_bytes = total_bytes
            self.remedy = remedy
            super().__init__(f"{path}: {free_bytes} bytes free of {total_bytes} total -- {remedy}")

    default_space_probe = capacity.default_space_probe
    PYTEST_GENERATION_NAME_RE = reclamation.PYTEST_GENERATION_NAME_RE
    SESSION_STALE_SECONDS = reclamation.SESSION_STALE_SECONDS
    BoundedCandidate = reclamation.BoundedCandidate
    EvidenceSource = reclamation.EvidenceSource
    LivenessScanUnavailable = reclamation.LivenessScanUnavailable
    PathEvidence = reclamation.PathEvidence
    ReclamationBound = reclamation.ReclamationBound
    Revocability = reclamation.Revocability
    bound_unsatisfied = reclamation.bound_unsatisfied
    harvest_kernel_references = reclamation.harvest_kernel_references
    harvest_snapshot_references = reclamation.harvest_snapshot_references
    is_pid_zombie = linux_proc.is_pid_zombie
    read_boot_id = linux_proc.read_boot_id
    read_starttime_ticks = linux_proc.read_starttime_ticks
    select_overflow = reclamation.select_overflow
    snapshot_referenced = reclamation.snapshot_referenced
    user_generation_root = reclamation.user_generation_root
    veto_paths = reclamation.veto_paths

_LEGACY_PREFIXES = (
    "pytest-tmp-",
    "pytest-cache-",
    "pytest-tmp",
    "pytest-cache",
    "test-basetemp",
    "reader-live-diagnostic",
    # CI scratch roots (S2-8) -- conformance-probes.yml, coverage-oracle.yml,
    # test-filter-audit.yml each mkdir their own fixed-name root outside the generation
    # scheme; widening the prefix match is what makes `reap` reach them at all.
    "pytest-probes",
    "pytest-coverage",
    "pytest-audit",
)


class LifecycleError(Exception):
    """A setup safety invariant was violated."""


class _OwnerMarkerState(StrEnum):
    """The three outcomes reading owner.json can produce -- collapsing corrupt into absent
    demotes a mature, live-owned generation to markerless (120-minute) protection the instant
    a kill-mid-write truncates the marker, leaving only a kernel reference at scan time between
    a live run and deletion.
    """

    ABSENT = "absent"
    VALID = "valid"
    CORRUPT = "corrupt"


class _OwnerLiveness(StrEnum):
    """Alive / provably dead / cannot tell -- only DEAD may ever be reclaimed."""

    ALIVE = "alive"
    DEAD = "dead"
    INDETERMINATE = "indeterminate"


def _log(message: str) -> None:
    print(f"pytest tmp lifecycle: {message}", file=sys.stderr)


def _absolute(path: Path) -> Path:
    return Path(os.path.abspath(os.path.normpath(os.fspath(path))))


def _user_root(platform_root: Path) -> Path:
    return user_generation_root(platform_root)


def _validate_setup_paths(
    platform_root: Path, tmp_dir: Path, cache_dir: Path
) -> tuple[Path, Path]:
    expected_root = _user_root(platform_root)
    tmp_dir = _absolute(tmp_dir)
    cache_dir = _absolute(cache_dir)
    if tmp_dir.name != "tmp" or cache_dir.name != "cache":
        raise LifecycleError("--dir and --cache-dir must end in tmp and cache")
    if tmp_dir.parent != cache_dir.parent:
        raise LifecycleError("tmp and cache must belong to the same generation")
    generation = tmp_dir.parent
    if generation.parent != expected_root or not PYTEST_GENERATION_NAME_RE.fullmatch(
        generation.name
    ):
        raise LifecycleError(
            "generation must be pytest-<8-hex-worktree-hash>-<run-id> "
            f"directly under {expected_root}"
        )
    return expected_root, generation


def _require_safe_rmtree() -> None:
    if not getattr(shutil.rmtree, "avoids_symlink_attacks", False):
        raise LifecycleError("this interpreter does not provide symlink-safe shutil.rmtree")


def _ensure_private_root(path: Path) -> None:
    try:
        path.mkdir(mode=0o700, parents=False, exist_ok=True)
    except OSError as exc:
        raise LifecycleError(f"cannot create private root {path}: {exc}") from exc
    try:
        root_stat = path.lstat()
    except OSError as exc:
        raise LifecycleError(f"cannot inspect private root {path}: {exc}") from exc
    if stat.S_ISLNK(root_stat.st_mode) or not stat.S_ISDIR(root_stat.st_mode):
        raise LifecycleError(f"private root is not a real directory: {path}")
    if root_stat.st_uid != os.getuid():
        raise LifecycleError(f"private root is not owned by uid {os.getuid()}: {path}")
    try:
        path.chmod(0o700)
    except OSError as exc:
        raise LifecycleError(f"cannot normalize private root permissions: {exc}") from exc


def _paths_from_tokens(tokens: Iterable[str]) -> set[Path]:
    """Extract TMPDIR=/--basetemp=/cache_dir= path values from ps-sweep tokens."""
    # Local copy: core.runtime._reclamation's equivalent parser is module-private, not
    # importable -- this is the macOS ps-sweep's only remaining consumer.
    references: set[Path] = set()
    for token in tokens:
        for prefix in ("TMPDIR=", "--basetemp=", "cache_dir="):
            marker_index = token.find(prefix)
            if marker_index < 0:
                continue
            value = token[marker_index + len(prefix) :].strip().strip("'\"")
            if value:
                references.add(_absolute(Path(value)))
    return references


def parse_ps_live_references(output: str) -> list[PathEvidence]:
    """Extract lifecycle path references from one macOS ps environment sweep.

    Always MONOTONIC -- ps offers no cwd/fd/maps equivalent on macOS, so this source can never
    veto; macOS reclamation rests on the owner marker plus the ReclamationBound (see S1-5 /
    tests/AGENTS.md's Performance section).
    """
    evidence: list[PathEvidence] = []
    for line in output.splitlines():
        try:
            tokens = shlex.split(line)
        except ValueError:
            tokens = line.split()
        for path in _paths_from_tokens(tokens):
            evidence.append(PathEvidence(path, EvidenceSource.PS_SWEEP, Revocability.MONOTONIC))
    return evidence


def _harvest_kernel_evidence(proc_root: Path) -> list[PathEvidence]:
    if sys.platform == "linux":
        return harvest_kernel_references(proc_root)
    return []  # no revocable-evidence source on macOS -- ps offers no cwd/fd/maps equivalent


def _harvest_snapshot_evidence(proc_root: Path) -> list[PathEvidence]:
    if sys.platform == "linux":
        return harvest_snapshot_references(proc_root)
    try:
        result = subprocess.run(
            ["ps", "axww", "-E", "-o", "pid=,command="],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise LivenessScanUnavailable(f"macOS ps scan failed: {exc}") from exc
    if result.returncode != 0:
        raise LivenessScanUnavailable(
            f"macOS ps scan exited {result.returncode}: {result.stderr.strip()}"
        )
    return parse_ps_live_references(result.stdout)


def _macos_start_id(pid: int) -> str:
    result = subprocess.run(
        ["ps", "-p", str(pid), "-o", "lstart="],
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    if result.returncode != 0 or not result.stdout.strip():
        raise OSError(f"cannot read process start identity for pid {pid}")
    return result.stdout.strip()


def _owner_liveness(owner: dict[str, object], proc_root: Path) -> _OwnerLiveness:
    """Alive / provably dead / cannot tell -- only DEAD may ever be reclaimed.

    os.kill(pid, 0) is the existence probe (matches the original _pid_exists, and works without
    /proc read access -- it distinguishes ESRCH, a genuine ProcessLookupError, from EPERM, a
    process that exists but cannot be signalled). EPERM confirms existence just as success does
    -- os.kill only raises it after the kernel has already resolved the pid -- so it falls
    through to the same /proc refinement below rather than short-circuiting: /proc reads live in
    a different permission domain than kill() (world-readable boot_id; starttime needs only
    /proc/<pid>/stat read access) and routinely succeed exactly when kill() EPERMs, e.g. a pid
    recycled by the OS to a different-uid process on a long-lived host. The refinement's DEAD
    determination is gated on a kernel-authoritative boot_id/starttime mismatch, never on how
    kill() responded, so this cannot turn a genuinely-ALIVE owner into DEAD. The IL-0
    /proc-reading primitives refine an existence-confirmed pid into alive/dead/indeterminate; on
    any of their own read failures the result is INDETERMINATE, restoring the fail-CLOSED
    posture the reaper needs -- a transient /proc read failure must retain the candidate, not
    reclaim it the moment grace lapses.
    """
    pid = owner["pid"]
    if not isinstance(pid, int) or pid <= 0:
        return _OwnerLiveness.DEAD
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return _OwnerLiveness.DEAD
    except OSError:
        pass  # EPERM (or other) -- kill() can't tell us; the /proc identity check below can.

    if sys.platform == "linux":
        if is_pid_zombie(pid, proc_root=proc_root):
            return _OwnerLiveness.DEAD
        boot_id = read_boot_id(proc_root=proc_root)
        if boot_id is None:
            return _OwnerLiveness.INDETERMINATE
        starttime_ticks = read_starttime_ticks(pid, proc_root=proc_root)
        if starttime_ticks is None:
            return _OwnerLiveness.INDETERMINATE
        if boot_id != owner["boot_id"] or str(starttime_ticks) != owner["start_id"]:
            return _OwnerLiveness.DEAD
        return _OwnerLiveness.ALIVE

    try:
        macos_start_id = _macos_start_id(pid)
    except OSError:
        return _OwnerLiveness.INDETERMINATE
    if macos_start_id != owner["start_id"]:
        return _OwnerLiveness.DEAD
    return _OwnerLiveness.ALIVE


def _load_owner(marker: Path) -> tuple[_OwnerMarkerState, dict[str, object] | None]:
    try:
        raw = marker.read_text()
    except OSError:
        return _OwnerMarkerState.ABSENT, None
    try:
        payload = json.loads(raw)
    except (ValueError, TypeError):
        return _OwnerMarkerState.CORRUPT, None
    if not isinstance(payload, dict):
        return _OwnerMarkerState.CORRUPT, None
    if not isinstance(payload.get("pid"), int):
        return _OwnerMarkerState.CORRUPT, None
    if not isinstance(payload.get("start_id"), str):
        return _OwnerMarkerState.CORRUPT, None
    if not isinstance(payload.get("boot_id"), str):
        return _OwnerMarkerState.CORRUPT, None
    if not isinstance(payload.get("created_at"), (int, float)):
        return _OwnerMarkerState.CORRUPT, None
    return _OwnerMarkerState.VALID, payload


def _older_than(path: Path, minutes: float) -> bool:
    try:
        return time.time() - path.stat().st_mtime > minutes * 60
    except OSError:
        return False


def _contains_reference(candidate: Path, references: Iterable[Path]) -> bool:
    candidate = _absolute(candidate)
    try:
        resolved_candidate = candidate.resolve()
    except (OSError, RuntimeError):
        return True
    for reference in references:
        reference = _absolute(reference)
        try:
            reference.relative_to(candidate)
        except ValueError:
            try:
                reference.resolve().relative_to(resolved_candidate)
            except ValueError:
                continue
            except (OSError, RuntimeError):
                return True
        return True
    return False


def _safe_candidates(platform_root: Path, user_root: Path) -> list[Path]:
    candidates: list[Path] = []
    if os.path.lexists(user_root):
        try:
            root_stat = user_root.lstat()
        except OSError as exc:
            _log(f"cannot inspect private root {user_root}: {exc}")
        else:
            if stat.S_ISLNK(root_stat.st_mode) or not stat.S_ISDIR(root_stat.st_mode):
                _log(f"skipping unsafe private root {user_root}")
            elif root_stat.st_uid != os.getuid():
                _log(f"skipping private root owned by uid {root_stat.st_uid}: {user_root}")
            else:
                try:
                    user_root.chmod(0o700)
                except OSError as exc:
                    _log(f"cannot normalize private root permissions for {user_root}: {exc}")
                    return candidates
                try:
                    candidates.extend(
                        child for child in user_root.iterdir() if child.name.startswith("pytest-")
                    )
                except OSError as exc:
                    _log(f"cannot enumerate private root {user_root}: {exc}")
    try:
        candidates.extend(
            child for child in platform_root.iterdir() if child.name.startswith(_LEGACY_PREFIXES)
        )
    except OSError as exc:
        _log(f"cannot enumerate platform root {platform_root}: {exc}")
    return candidates


def _remove_candidate(candidate: Path) -> None:
    try:
        shutil.rmtree(candidate)
    except FileNotFoundError:
        return
    except OSError as exc:
        _log(f"could not remove {candidate}: {exc}")


def _directory_size_bytes(path: Path) -> int:
    total = 0
    try:
        entries = list(path.rglob("*"))
    except OSError:
        return 0
    for entry in entries:
        try:
            if entry.is_file() and not entry.is_symlink():
                total += entry.stat().st_size
        except OSError:
            continue
    return total


def _bounded_candidate(
    candidate: Path, candidate_stat: os.stat_result, *, protected: bool
) -> BoundedCandidate:
    return BoundedCandidate(
        path=_absolute(candidate),
        mtime=candidate_stat.st_mtime,
        size_bytes=_directory_size_bytes(candidate),
        protected=protected,
    )


def _reap(
    platform_root: Path,
    *,
    grace_minutes: float,
    legacy_age_minutes: float,
    proc_root: Path,
    excluded: set[Path] | None = None,
) -> list[BoundedCandidate]:
    """Reap provably dead, unreferenced candidates; return the survivors for ReclamationBound.

    Retention shape (owner-state table, see the plan): a REVOCABLE reference (veto_paths) or a
    live/indeterminate owner always retains, unconditionally. A monotonic reference
    (snapshot_referenced) protects only the markerless branch, since a marked generation already
    has a sound owner-liveness proof and does not need a fallback signal. Scan failure retains
    everything -- returns an empty survivor list, same fail-closed contract as before.
    """
    try:
        kernel_evidence = _harvest_kernel_evidence(proc_root)
        snapshot_evidence = _harvest_snapshot_evidence(proc_root)
    except LivenessScanUnavailable as exc:
        _log(f"liveness scan unavailable; reaping skipped: {exc}")
        return []
    revocable_paths = veto_paths(kernel_evidence)
    user_root = _user_root(platform_root)
    excluded_paths = {_absolute(path) for path in (excluded or set())}
    survivors: list[BoundedCandidate] = []
    for candidate in _safe_candidates(_absolute(platform_root), user_root):
        if _absolute(candidate) in excluded_paths:
            continue
        try:
            candidate_stat = candidate.lstat()
        except FileNotFoundError:
            continue
        except OSError as exc:
            _log(f"cannot inspect {candidate}: {exc}")
            continue
        if stat.S_ISLNK(candidate_stat.st_mode) or not stat.S_ISDIR(candidate_stat.st_mode):
            _log(f"skipping non-directory or symlink candidate {candidate}")
            continue
        if candidate_stat.st_uid != os.getuid():
            _log(f"skipping candidate owned by uid {candidate_stat.st_uid}: {candidate}")
            continue

        has_revocable_reference = _contains_reference(candidate, revocable_paths)
        marker = candidate / "owner.json"
        marker_state, owner = _load_owner(marker)

        if marker_state is _OwnerMarkerState.VALID:
            assert owner is not None
            if _owner_liveness(owner, proc_root) is not _OwnerLiveness.DEAD:
                survivors.append(_bounded_candidate(candidate, candidate_stat, protected=True))
                continue
            if has_revocable_reference:
                survivors.append(_bounded_candidate(candidate, candidate_stat, protected=True))
                continue
            if not _older_than(marker, grace_minutes):
                # provably dead, no revocable reference, still within grace: normal reap
                # retains it, but this is exactly select_overflow's eligibility criterion --
                # the bound MAY reclaim it early under capacity pressure.
                survivors.append(_bounded_candidate(candidate, candidate_stat, protected=False))
                continue
        elif marker_state is _OwnerMarkerState.CORRUPT:
            # Treated as *valid, dead*: grace-gated and revocable-reference-gated, never
            # demoted to markerless protection -- a kill mid-write must not weaken a mature,
            # live-owned generation to relying solely on the instantaneous reference scan.
            if has_revocable_reference:
                survivors.append(_bounded_candidate(candidate, candidate_stat, protected=True))
                continue
            if not _older_than(marker, grace_minutes):
                survivors.append(_bounded_candidate(candidate, candidate_stat, protected=False))
                continue
        else:  # ABSENT
            if has_revocable_reference or snapshot_referenced(candidate, snapshot_evidence):
                survivors.append(_bounded_candidate(candidate, candidate_stat, protected=True))
                continue
            if not _older_than(candidate, legacy_age_minutes):
                # No owner marker to prove dead -- the bound must never touch a markerless
                # candidate; it might be another concurrent _setup mid-creation, protected
                # today only by this age gate.
                survivors.append(_bounded_candidate(candidate, candidate_stat, protected=True))
                continue
        _remove_candidate(candidate)
    return survivors


def _write_owner(marker: Path, owner_pid: int, proc_root: Path) -> None:
    if sys.platform == "linux":
        starttime_ticks = read_starttime_ticks(owner_pid, proc_root=proc_root)
        if starttime_ticks is None:
            raise OSError(f"cannot read process start identity for pid {owner_pid}")
        start_id = str(starttime_ticks)
        boot_id = read_boot_id(proc_root=proc_root)
        if boot_id is None:
            raise OSError("cannot read system boot id")
    else:
        start_id = _macos_start_id(owner_pid)
        boot_id = ""
    payload = {
        "pid": owner_pid,
        "start_id": start_id,
        "boot_id": boot_id,
        "created_at": time.time(),
    }
    descriptor = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        json.dump(payload, stream, separators=(",", ":"))


def _setup(args: argparse.Namespace) -> int:
    _require_safe_rmtree()
    platform_root = _absolute(args.root)
    user_root, generation = _validate_setup_paths(platform_root, args.tmp_dir, args.cache_dir)
    _ensure_private_root(user_root)
    survivors = _reap(
        platform_root,
        grace_minutes=args.grace_minutes,
        legacy_age_minutes=args.legacy_age_minutes,
        proc_root=args.proc_root,
        excluded={generation},
    )
    bound = ReclamationBound(max_generations=args.max_generations, max_bytes=args.max_bytes)
    selected = select_overflow(survivors, bound)
    for overflow_candidate in selected:
        _log(f"bound exceeded; reclaiming {overflow_candidate.path} ahead of its grace window")
        _remove_candidate(overflow_candidate.path)
    if bound_unsatisfied(survivors, selected, bound):
        selected_paths = {c.path for c in selected}
        protected_count = sum(1 for c in survivors if c.path not in selected_paths and c.protected)
        probe_note = ""
        try:
            total_bytes, _used_bytes, free_bytes = default_space_probe(platform_root)
        except OSError as probe_exc:
            total_bytes, free_bytes = 0, 0
            probe_note = (
                f"; additionally, the disk-usage probe of {platform_root} failed: {probe_exc}"
            )
        raise StoreCapacityExhaustedError(
            path=platform_root,
            free_bytes=free_bytes,
            total_bytes=total_bytes,
            remedy=(
                f"generation ceiling exceeded (max_generations={bound.max_generations}, "
                f"max_bytes={bound.max_bytes}) and cannot be satisfied by reclamation: "
                f"{protected_count} candidates remain protected by a live owner or a "
                "revocable reference; investigate leaked processes holding stale "
                "generations, or raise --max-generations/--max-bytes for legitimate "
                f"concurrency{probe_note}"
            ),
        )
    try:
        generation.mkdir(mode=0o700)
    except FileExistsError:
        raise LifecycleError(f"generation collision at {generation}")
    except OSError as exc:
        raise LifecycleError(f"cannot claim generation {generation}: {exc}") from exc
    try:
        (generation / "tmp").mkdir(mode=0o700)
        (generation / "cache").mkdir(mode=0o700)
        _write_owner(generation / "owner.json", args.owner_pid or os.getppid(), args.proc_root)
    except (OSError, subprocess.SubprocessError) as exc:
        try:
            shutil.rmtree(generation)
        except OSError as cleanup_exc:
            _log(f"could not clean partially-created generation {generation}: {cleanup_exc}")
        raise LifecycleError(f"could not initialize generation {generation}: {exc}") from exc
    return 0


def _reap_command(args: argparse.Namespace) -> int:
    try:
        _require_safe_rmtree()
    except LifecycleError as exc:
        _log(str(exc))
        return 0
    _reap(
        _absolute(args.root),
        grace_minutes=args.grace_minutes,
        legacy_age_minutes=args.legacy_age_minutes,
        proc_root=args.proc_root,
    )
    return 0


def _sweep_sessions(args: argparse.Namespace) -> int:
    """Reap stale headless-* session dirs under <root>/autoskillit-sessions.

    st_mtime-gated against SESSION_STALE_SECONDS -- the same TTL and stat field
    workspace.session_skills.cleanup_stale uses for the same root (see S2-7 /
    core/runtime/_reclamation.py's SESSION_STALE_SECONDS docstring). The Taskfile's
    cleanup-shm task invokes this instead of a bare `find ... -exec rm -rf`, so the two
    reclaimers can no longer disagree on staleness.
    """
    sessions_root = _absolute(args.root) / "autoskillit-sessions"
    try:
        entries = list(sessions_root.iterdir())
    except OSError:
        return 0
    now = time.time()
    removed = 0
    for entry in entries:
        if not entry.name.startswith("headless-"):
            continue
        try:
            entry_stat = entry.lstat()
        except OSError:
            continue
        if stat.S_ISLNK(entry_stat.st_mode) or not stat.S_ISDIR(entry_stat.st_mode):
            continue
        if now - entry_stat.st_mtime <= SESSION_STALE_SECONDS:
            continue
        _remove_candidate(entry)
        removed += 1
    if removed:
        _log(f"removed {removed} stale headless session dirs under {sessions_root}")
    return 0


def _add_reap_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--grace-minutes", type=float, default=5)
    parser.add_argument("--legacy-age-minutes", type=float, default=120)
    parser.add_argument("--proc-root", type=Path, default=Path("/proc"))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    setup_parser = subparsers.add_parser("setup")
    _add_reap_options(setup_parser)
    setup_parser.add_argument("--dir", dest="tmp_dir", type=Path, required=True)
    setup_parser.add_argument("--cache-dir", type=Path, required=True)
    setup_parser.add_argument("--owner-pid", type=int)
    # Generous headroom over the realistic concurrency ceiling (23 registered worktrees x 4
    # xdist workers = ~92 simultaneous legitimate generations) while still finite -- unlike
    # today's unbounded growth. max-bytes defaults to half of a typical /dev/shm allocation
    # (20 GiB observed during this rectify), leaving headroom for everything else sharing it.
    setup_parser.add_argument("--max-generations", type=int, default=100)
    setup_parser.add_argument("--max-bytes", type=int, default=10_000_000_000)
    setup_parser.set_defaults(handler=_setup)
    reap_parser = subparsers.add_parser("reap")
    _add_reap_options(reap_parser)
    reap_parser.set_defaults(handler=_reap_command)
    sweep_sessions_parser = subparsers.add_parser("sweep-sessions")
    sweep_sessions_parser.add_argument("--root", type=Path, required=True)
    sweep_sessions_parser.set_defaults(handler=_sweep_sessions)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        return int(args.handler(args))
    except (LifecycleError, StoreCapacityExhaustedError) as exc:
        _log(str(exc))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
