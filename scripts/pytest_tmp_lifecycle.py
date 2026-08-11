#!/usr/bin/env python3
"""Create invocation-unique pytest temp generations and reap provably dead ones."""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import stat
import subprocess
import sys
import time
from collections.abc import Iterable, Sequence
from pathlib import Path

_GENERATION_RE = re.compile(r"^pytest-[0-9a-f]{8}-.+$")
_LEGACY_PREFIXES = ("pytest-tmp-", "pytest-cache-")


class LifecycleError(Exception):
    """A setup safety invariant was violated."""


class LivenessScanUnavailable(Exception):
    """The process-wide liveness scan could not be completed."""


def _log(message: str) -> None:
    print(f"pytest tmp lifecycle: {message}", file=sys.stderr)


def _absolute(path: Path) -> Path:
    return Path(os.path.abspath(os.path.normpath(os.fspath(path))))


def _user_root(platform_root: Path) -> Path:
    return _absolute(platform_root) / f"autoskillit-pytest-{os.getuid()}"


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
    if generation.parent != expected_root or not _GENERATION_RE.fullmatch(generation.name):
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


def parse_ps_live_references(output: str) -> set[Path]:
    """Extract lifecycle path references from one macOS ps environment sweep."""
    references: set[Path] = set()
    for line in output.splitlines():
        try:
            tokens = shlex.split(line)
        except ValueError:
            tokens = line.split()
        references.update(_paths_from_tokens(tokens))
    return references


def scan_linux_live_references(proc_root: Path) -> set[Path]:
    """Read every accessible process environment and command line exactly once."""
    try:
        process_dirs = list(proc_root.iterdir())
    except OSError as exc:
        raise LivenessScanUnavailable(f"cannot enumerate {proc_root}: {exc}") from exc
    references: set[Path] = set()
    for process_dir in process_dirs:
        if not process_dir.name.isdigit() or not process_dir.is_dir():
            continue
        for filename in ("environ", "cmdline"):
            try:
                raw = (process_dir / filename).read_bytes()
            except OSError:
                continue
            tokens = [part.decode(errors="surrogateescape") for part in raw.split(b"\0") if part]
            references.update(_paths_from_tokens(tokens))
    return references


def _scan_live_references(proc_root: Path) -> set[Path]:
    if sys.platform == "linux":
        return scan_linux_live_references(proc_root)
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


def _linux_start_id(pid: int, proc_root: Path) -> str:
    raw = (proc_root / str(pid) / "stat").read_text()
    close_paren = raw.rfind(")")
    if close_paren < 0:
        raise OSError(f"malformed stat record for pid {pid}")
    fields_after_comm = raw[close_paren + 1 :].split()
    if len(fields_after_comm) <= 19:
        raise OSError(f"short stat record for pid {pid}")
    return fields_after_comm[19]


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


def _start_id(pid: int, proc_root: Path) -> str:
    if sys.platform == "linux":
        return _linux_start_id(pid, proc_root)
    return _macos_start_id(pid)


def _boot_id(proc_root: Path) -> str:
    if sys.platform != "linux":
        return ""
    return (proc_root / "sys" / "kernel" / "random" / "boot_id").read_text().strip()


def _pid_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _owner_is_alive(owner: dict[str, object], proc_root: Path) -> bool:
    pid = owner["pid"]
    if not isinstance(pid, int) or pid <= 0 or not _pid_exists(pid):
        return False
    try:
        return owner["boot_id"] == _boot_id(proc_root) and owner["start_id"] == _start_id(
            pid, proc_root
        )
    except (OSError, subprocess.SubprocessError):
        return True


def _load_owner(marker: Path) -> dict[str, object] | None:
    try:
        payload = json.loads(marker.read_text())
    except (OSError, ValueError, TypeError):
        return None
    if not isinstance(payload, dict):
        return None
    if not isinstance(payload.get("pid"), int):
        return None
    if not isinstance(payload.get("start_id"), str):
        return None
    if not isinstance(payload.get("boot_id"), str):
        return None
    if not isinstance(payload.get("created_at"), (int, float)):
        return None
    return payload


def _older_than(path: Path, minutes: float) -> bool:
    try:
        return time.time() - path.stat().st_mtime > minutes * 60
    except OSError:
        return False


def _contains_reference(candidate: Path, references: set[Path]) -> bool:
    candidate = _absolute(candidate)
    for reference in references:
        try:
            _absolute(reference).relative_to(candidate)
        except ValueError:
            continue
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


def _reap(
    platform_root: Path,
    *,
    grace_minutes: float,
    legacy_age_minutes: float,
    proc_root: Path,
    excluded: set[Path] | None = None,
) -> None:
    try:
        references = _scan_live_references(proc_root)
    except LivenessScanUnavailable as exc:
        _log(f"liveness scan unavailable; reaping skipped: {exc}")
        return
    user_root = _user_root(platform_root)
    excluded_paths = {_absolute(path) for path in (excluded or set())}
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
        if _contains_reference(candidate, references):
            continue
        marker = candidate / "owner.json"
        owner = _load_owner(marker)
        if owner is not None:
            if _owner_is_alive(owner, proc_root) or not _older_than(marker, grace_minutes):
                continue
        elif not _older_than(candidate, legacy_age_minutes):
            continue
        _remove_candidate(candidate)


def _write_owner(marker: Path, owner_pid: int, proc_root: Path) -> None:
    payload = {
        "pid": owner_pid,
        "start_id": _start_id(owner_pid, proc_root),
        "boot_id": _boot_id(proc_root),
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
    _reap(
        platform_root,
        grace_minutes=args.grace_minutes,
        legacy_age_minutes=args.legacy_age_minutes,
        proc_root=args.proc_root,
        excluded={generation},
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
    setup_parser.set_defaults(handler=_setup)
    reap_parser = subparsers.add_parser("reap")
    _add_reap_options(reap_parser)
    reap_parser.set_defaults(handler=_reap_command)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        return int(args.handler(args))
    except LifecycleError as exc:
        _log(str(exc))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
