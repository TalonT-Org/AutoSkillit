"""Kernel-derived path evidence, classified so a monotonic signal can never veto reclamation.

A process's /proc/<pid>/environ and /proc/<pid>/cmdline are snapshots taken at execve() --
PROC_ENVIRON never reflects anything the process does after exec (proc_pid_environ(5)); a
well-behaved process never mutates its own argv either, so PROC_CMDLINE is monotonic *by
practice*, not by kernel contract, but the taxonomy needs "can this become false while the
holder lives", not "is this technically mutable". Do not reclassify PROC_CMDLINE as REVOCABLE
on the strength of the proc_pid_cmdline(5) man page alone -- the man page describes mutability,
and a live pytest process is independently protected by its owner marker and by the fds it
holds inside its own generation, so the lost veto signal costs nothing. Both files are empty
for a zombie, so neither can produce evidence for one -- zombie detection is an owner-marker
problem (see is_pid_zombie in _linux_proc.py), not a reference problem.

PROC_CWD, PROC_FD, and PROC_MAPS are kernel-maintained live views: they are proof of present
use and can genuinely become false the instant a holder closes an fd, changes directory, or
unmaps a file. Only these may veto a reclamation decision.

veto_paths() and snapshot_referenced() are deliberately separate functions rather than one
function that silently drops monotonic members -- the separation, not the type alone, is what
makes an evidence-class mistake unrepresentable: veto_paths() raises on a MONOTONIC member
instead of returning a plausible-looking answer, so a caller that accidentally combines both
harvests gets a loud, greppable failure instead of a quietly-wrong veto set.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

__all__ = [
    "BoundedCandidate",
    "EvidenceSource",
    "LivenessScanUnavailable",
    "PathEvidence",
    "ReclamationBound",
    "Revocability",
    "SESSION_STALE_SECONDS",
    "append_and_trim_jsonl",
    "bound_unsatisfied",
    "harvest_kernel_references",
    "harvest_snapshot_references",
    "select_overflow",
    "snapshot_referenced",
    "trim_jsonl_lines",
    "user_generation_root",
    "veto_paths",
]

#: Single TTL, single stat field (st_mtime), shared by workspace.session_skills.cleanup_stale
#: and scripts/pytest_tmp_lifecycle.py's sweep-sessions subcommand (invoked by Taskfile.yml's
#: cleanup-shm task) -- both govern the same autoskillit-sessions root. Previously two
#: independent values on two different time axes: 240 min (Taskfile find -mmin, st_mtime) vs
#: 86400 s (cleanup_stale's default, st_atime). /dev/shm is mounted noatime on this host, so
#: the st_atime gate was already inert (frozen at creation) -- st_mtime is the live predicate.
SESSION_STALE_SECONDS = 86400


class LivenessScanUnavailable(Exception):
    """A kernel evidence scan could not be completed at all (the /proc root is unreadable).

    Distinct from a per-entry read failure (a process exiting mid-scan, or lacking permission
    to read another user's /proc/<pid>/fd/*), which is skipped rather than raised here --
    promoting a per-entry failure to a whole-scan failure would fail the reaper closed on
    every run.
    """


class Revocability(StrEnum):
    """Whether a piece of path evidence can become false while its holder is still alive."""

    REVOCABLE = "revocable"
    MONOTONIC = "monotonic"


class EvidenceSource(StrEnum):
    """Where one PathEvidence was harvested from."""

    PROC_CWD = "proc_cwd"
    PROC_FD = "proc_fd"
    PROC_MAPS = "proc_maps"
    PROC_ENVIRON = "proc_environ"
    PROC_CMDLINE = "proc_cmdline"
    PS_SWEEP = "ps_sweep"


_REVOCABILITY: dict[EvidenceSource, Revocability] = {
    EvidenceSource.PROC_CWD: Revocability.REVOCABLE,
    EvidenceSource.PROC_FD: Revocability.REVOCABLE,
    EvidenceSource.PROC_MAPS: Revocability.REVOCABLE,
    EvidenceSource.PROC_ENVIRON: Revocability.MONOTONIC,
    EvidenceSource.PROC_CMDLINE: Revocability.MONOTONIC,
    EvidenceSource.PS_SWEEP: Revocability.MONOTONIC,
}

_UNCLASSIFIED_EVIDENCE_SOURCES = sorted(set(EvidenceSource) - set(_REVOCABILITY))
if _UNCLASSIFIED_EVIDENCE_SOURCES:
    raise AssertionError(
        "Every EvidenceSource must have a _REVOCABILITY entry. "
        f"Missing: {_UNCLASSIFIED_EVIDENCE_SOURCES}"
    )


@dataclass(frozen=True, slots=True)
class PathEvidence:
    """One path reference harvested from a kernel source, tagged with its revocability."""

    path: Path
    source: EvidenceSource
    revocability: Revocability


def _absolute_path(path: Path) -> Path:
    return Path(os.path.abspath(os.path.normpath(os.fspath(path))))


def _paths_from_tokens(tokens: Sequence[str]) -> set[Path]:
    """Extract TMPDIR=/--basetemp=/cache_dir= path values from environ or argv tokens."""
    references: set[Path] = set()
    for token in tokens:
        for prefix in ("TMPDIR=", "--basetemp=", "cache_dir="):
            marker_index = token.find(prefix)
            if marker_index < 0:
                continue
            value = token[marker_index + len(prefix) :].strip().strip("'\"")
            if value:
                references.add(_absolute_path(Path(value)))
    return references


def _paths_from_maps(maps_text: str) -> list[Path]:
    """Extract the mapped-file pathname from each /proc/pid/maps line that has one."""
    paths: list[Path] = []
    for line in maps_text.splitlines():
        fields = line.split(None, 5)
        if len(fields) < 6:
            continue
        pathname = fields[5].strip()
        if not pathname or pathname.startswith("["):
            continue
        paths.append(_absolute_path(Path(pathname)))
    return paths


def harvest_kernel_references(proc_root: Path) -> list[PathEvidence]:
    """Harvest REVOCABLE evidence: cwd, fd/*, maps -- kernel-maintained, live-view proof of
    present use. Feeds veto_paths() only.

    Per-entry read failures (a process exiting mid-scan, or lacking permission to read another
    user's /proc/<pid>/fd/*) are skipped -- fd/* is a new access class for this harvest and a
    per-entry PermissionError/FileNotFoundError is routine, not a scan failure. A failure to
    enumerate proc_root itself raises LivenessScanUnavailable.
    """
    try:
        process_dirs = list(proc_root.iterdir())
    except OSError as exc:
        raise LivenessScanUnavailable(f"cannot enumerate {proc_root}: {exc}") from exc
    evidence: list[PathEvidence] = []
    for process_dir in process_dirs:
        if not process_dir.name.isdigit() or not process_dir.is_dir():
            continue
        try:
            cwd = Path(os.readlink(process_dir / "cwd"))
        except OSError:
            pass
        else:
            evidence.append(
                PathEvidence(_absolute_path(cwd), EvidenceSource.PROC_CWD, Revocability.REVOCABLE)
            )
        try:
            fd_entries = list((process_dir / "fd").iterdir())
        except OSError:
            fd_entries = []
        for fd_entry in fd_entries:
            try:
                target = Path(os.readlink(fd_entry))
            except OSError:
                continue
            evidence.append(
                PathEvidence(
                    _absolute_path(target), EvidenceSource.PROC_FD, Revocability.REVOCABLE
                )
            )
        try:
            maps_text = (process_dir / "maps").read_text(errors="surrogateescape")
        except OSError:
            maps_text = ""
        for maps_path in _paths_from_maps(maps_text):
            evidence.append(
                PathEvidence(maps_path, EvidenceSource.PROC_MAPS, Revocability.REVOCABLE)
            )
    return evidence


def harvest_snapshot_references(proc_root: Path) -> list[PathEvidence]:
    """Harvest MONOTONIC evidence: environ, cmdline -- execve()-time snapshots that can only
    ever gain a path reference, never lose one, for the life of the holder. Feeds
    snapshot_referenced() only -- see the module docstring for why both sources are MONOTONIC.
    """
    try:
        process_dirs = list(proc_root.iterdir())
    except OSError as exc:
        raise LivenessScanUnavailable(f"cannot enumerate {proc_root}: {exc}") from exc
    evidence: list[PathEvidence] = []
    for process_dir in process_dirs:
        if not process_dir.name.isdigit() or not process_dir.is_dir():
            continue
        for filename, source in (
            ("environ", EvidenceSource.PROC_ENVIRON),
            ("cmdline", EvidenceSource.PROC_CMDLINE),
        ):
            try:
                raw = (process_dir / filename).read_bytes()
            except OSError:
                continue
            tokens = [part.decode(errors="surrogateescape") for part in raw.split(b"\0") if part]
            for path in _paths_from_tokens(tokens):
                evidence.append(PathEvidence(path, source, Revocability.MONOTONIC))
    return evidence


def veto_paths(evidence: Sequence[PathEvidence]) -> frozenset[Path]:
    """Reduce REVOCABLE evidence to the set of paths a caller may treat as a liveness veto.

    Raises ValueError on any MONOTONIC member: a monotonic path cannot reach a veto position
    without an explicit, greppable, guard-visible violation (the #4695 pattern, applied here).
    Never pass a combined kernel+snapshot harvest here -- harvest_snapshot_references is
    essentially always non-empty (every daemon inherits a TMPDIR= token), so a combined call
    would abort every _tmpdir-setup invocation.
    """
    paths: set[Path] = set()
    for item in evidence:
        if item.revocability is not Revocability.REVOCABLE:
            raise ValueError(
                f"veto_paths() received {item.revocability.value} evidence from "
                f"{item.source.value} ({item.path}) -- monotonic evidence must never reach a "
                "veto position. Use snapshot_referenced() for the markerless-candidate check."
            )
        paths.add(item.path)
    return frozenset(paths)


def snapshot_referenced(candidate: Path, evidence: Sequence[PathEvidence]) -> bool:
    """Report whether a *markerless* candidate is named by any evidence path.

    The separate consumer for monotonic evidence -- never produces a veto set, and has exactly
    one call site: the markerless (absent-owner-marker) branch of the reaper's retention logic.
    Uses the same containment rule as the revocable-evidence veto path: an exact match or an
    ancestor/descendant relationship, checked again after resolving symlinks.
    """
    candidate = _absolute_path(candidate)
    try:
        resolved_candidate = candidate.resolve()
    except (OSError, RuntimeError):
        return True
    for item in evidence:
        reference = _absolute_path(item.path)
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


def user_generation_root(platform_root: Path) -> Path:
    """The per-uid root under a platform temp root that owns pytest generations.

    Lifted verbatim from scripts/pytest_tmp_lifecycle.py's _user_root() so IL-0 code (the
    Stage-2 capacity preflight, the doctor check) can name this root without violating the
    import-layer contracts that keep scripts/ unreachable from src/autoskillit/.
    """
    return _absolute_path(platform_root) / f"autoskillit-pytest-{os.getuid()}"


@dataclass(frozen=True, slots=True)
class ReclamationBound:
    """A ceiling a reclaimable store must not exceed, oldest-first when it does.

    Either field may be None to disable that dimension.
    """

    max_generations: int | None = None
    max_bytes: int | None = None


@dataclass(frozen=True, slots=True)
class BoundedCandidate:
    """One reclaimable unit under a ReclamationBound: a pytest generation, a JSONL record, etc.

    `protected` must be True whenever some OTHER rule (a live owner, a revocable kernel
    reference) already forbids reclaiming this candidate -- select_overflow never selects a
    protected candidate, so the bound can never become a second door around that rule.
    """

    path: Path
    mtime: float
    size_bytes: int
    protected: bool


def select_overflow(
    candidates: Sequence[BoundedCandidate], bound: ReclamationBound
) -> list[BoundedCandidate]:
    """Oldest-first selection of unprotected candidates whose removal would satisfy `bound`.

    Mirrors execution/session_log.py's _MAX_SESSIONS co-retention model: sort by mtime, then
    walk oldest-first, selecting candidates for removal only while the ceiling remains
    exceeded. A protected candidate is never selected -- it does not count as "removable" even
    if selecting it would satisfy the bound, so bound_unsatisfied can report the ceiling as
    un-satisfiable rather than the bound silently deleting live work.
    """
    remaining_count = len(candidates)
    remaining_bytes = sum(c.size_bytes for c in candidates)
    eligible = sorted((c for c in candidates if not c.protected), key=lambda c: c.mtime)

    selected: list[BoundedCandidate] = []
    for candidate in eligible:
        over_count = bound.max_generations is not None and remaining_count > bound.max_generations
        over_bytes = bound.max_bytes is not None and remaining_bytes > bound.max_bytes
        if not (over_count or over_bytes):
            break
        selected.append(candidate)
        remaining_count -= 1
        remaining_bytes -= candidate.size_bytes
    return selected


def bound_unsatisfied(
    candidates: Sequence[BoundedCandidate],
    selected: Sequence[BoundedCandidate],
    bound: ReclamationBound,
) -> bool:
    """True when `bound` is still exceeded after removing `selected` from `candidates`.

    Counts every non-selected candidate toward the remaining total, protected or not -- a
    protected (live-owner or revocably-referenced) candidate that select_overflow declined to
    touch still occupies space, so it must still count against the ceiling here.
    """
    selected_paths = {c.path for c in selected}
    remaining = [c for c in candidates if c.path not in selected_paths]
    if bound.max_generations is not None and len(remaining) > bound.max_generations:
        return True
    if bound.max_bytes is not None and sum(c.size_bytes for c in remaining) > bound.max_bytes:
        return True
    return False


def trim_jsonl_lines(lines: Sequence[str], *, max_lines: int) -> list[str]:
    """Oldest-first line trimming for an append-only JSONL store.

    An append-only event-log line has no "protected" concept the way a BoundedCandidate
    directory does -- nothing owns a past event record the way a live process owns a
    generation -- so this is a plain oldest-first slice, not select_overflow's
    protection-aware selection.
    """
    if max_lines <= 0:
        return []
    if len(lines) <= max_lines:
        return list(lines)
    return list(lines[-max_lines:])


def append_and_trim_jsonl(path: Path, line: str, *, max_lines: int) -> None:
    """Append one JSON line to `path`, then trim to at most `max_lines`, oldest-first.

    Mirrors execution/session_log.py's count-bound retention model, applied to a flat JSONL
    file instead of a directory tree. Not atomic across the read-modify-write (matches the
    existing writers' posture -- reaper_events.jsonl, session_provenance.jsonl -- which are
    also plain appends without file locking); an interleaved concurrent append from another
    process could be dropped by the trim. Acceptable for best-effort operational event logs.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    existing: list[str] = []
    if path.exists():
        existing = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    existing.append(line)
    trimmed = trim_jsonl_lines(existing, max_lines=max_lines)
    path.write_text("\n".join(trimmed) + ("\n" if trimmed else ""), encoding="utf-8")
