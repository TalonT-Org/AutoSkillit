"""Read-only verification of every discoverable AutoSkillit installation."""

from __future__ import annotations

import base64
import csv
import hashlib
import importlib.metadata
import stat
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

from autoskillit.core import Severity, is_python_bytecode_path

from ._doctor_types import DoctorResult

_RECORD_NAME = "RECORD"
_PACKAGE_NAME = "autoskillit"


@dataclass
class _Installation:
    package_root: Path
    record_path: Path
    aliases: set[str]


def _record_paths_below(root: Path) -> Iterator[tuple[Path, Path]]:
    """Yield package and RECORD paths found below one owned installation root."""
    if not root.is_dir():
        return
    try:
        records = sorted(root.glob(f"**/{_PACKAGE_NAME}-*.dist-info/{_RECORD_NAME}"))
    except OSError:
        return
    for record_path in records:
        package_root = record_path.parent.parent / _PACKAGE_NAME
        if package_root.is_dir():
            yield package_root, record_path


def _active_record_paths() -> Iterator[tuple[Path, Path]]:
    """Yield the running distribution's package and RECORD paths when available."""
    try:
        distribution = importlib.metadata.distribution(_PACKAGE_NAME)
        files = distribution.files or ()
    except importlib.metadata.PackageNotFoundError:
        return
    except OSError:
        return

    for relative_path in files:
        if str(relative_path).endswith(f".dist-info/{_RECORD_NAME}"):
            record_path = Path(str(distribution.locate_file(relative_path)))
            package_root = record_path.parent.parent / _PACKAGE_NAME
            if package_root.is_dir():
                yield package_root, record_path
            return

    try:
        site_packages = Path(str(distribution.locate_file("")))
    except OSError:
        return
    yield from _record_paths_below(site_packages)


def _owned_installation_roots(home: Path) -> tuple[tuple[str, Path], ...]:
    """Return bounded installation/cache roots owned by the selected home."""
    roots: list[tuple[str, Path]] = [
        ("plugin generations", home / ".autoskillit" / "plugin-generations"),
        ("uv tool", home / ".local" / "share" / "uv" / "tools" / _PACKAGE_NAME),
    ]
    cache_root = home / ".cache" / "uv"
    try:
        roots.extend(
            (f"uv cache {path.name}", path) for path in sorted(cache_root.glob("archive-v*"))
        )
    except OSError:
        pass
    return tuple(roots)


def _discover_installations(
    *, home: Path, roots: Iterable[Path] | None
) -> tuple[_Installation, ...]:
    """Deduplicate physical installs while retaining every discovery alias."""
    candidates: list[tuple[str, Path, Path]] = []
    if roots is None:
        candidates.extend(
            ("active distribution", package, record) for package, record in _active_record_paths()
        )
        for label, root in _owned_installation_roots(home):
            candidates.extend(
                (label, package, record) for package, record in _record_paths_below(root)
            )
    else:
        for root in roots:
            candidates.extend(
                (str(root), package, record) for package, record in _record_paths_below(root)
            )

    installs: dict[str, _Installation] = {}
    for label, package_root, record_path in candidates:
        try:
            canonical_root = package_root.resolve(strict=True)
            canonical_record = record_path.resolve(strict=True)
        except OSError:
            continue
        key = str(canonical_root)
        alias = f"{label}: {package_root}"
        if existing := installs.get(key):
            existing.aliases.add(alias)
        else:
            installs[key] = _Installation(canonical_root, canonical_record, {alias})
    return tuple(installs[key] for key in sorted(installs))


def _display(installation: _Installation) -> str:
    aliases = "; ".join(sorted(installation.aliases))
    return f"{installation.package_root} ({aliases})"


def _record_hash(value: str) -> tuple[str, bytes] | None:
    algorithm, separator, encoded = value.partition("=")
    if not separator or not algorithm or not encoded:
        return None
    try:
        return algorithm, base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
    except ValueError:
        return None


def _regular_files(root: Path) -> Iterator[Path]:
    try:
        paths = root.glob("**/*")
        for path in paths:
            try:
                if stat.S_ISREG(path.stat(follow_symlinks=False).st_mode):
                    yield path
            except OSError:
                continue
    except OSError:
        return


def _verify_record(installation: _Installation) -> tuple[list[DoctorResult], set[str]]:
    """Verify hashed RECORD members and return its package-tree membership."""
    results: list[DoctorResult] = []
    tracked: set[str] = set()
    site_packages = installation.record_path.parent.parent
    try:
        rows = csv.reader(installation.record_path.read_text(encoding="utf-8").splitlines())
        for row in rows:
            if len(row) < 2:
                continue
            relative = Path(row[0])
            if relative.is_absolute() or ".." in relative.parts:
                results.append(
                    DoctorResult(
                        Severity.ERROR,
                        "installation_record_invalid",
                        f"Invalid RECORD path {row[0]!r} in {_display(installation)}",
                    )
                )
                continue
            candidate = site_packages / relative
            try:
                package_relative = candidate.relative_to(installation.package_root)
            except ValueError:
                package_relative = None
            if package_relative is not None:
                tracked.add(package_relative.as_posix())
            if not row[1]:
                continue
            parsed_hash = _record_hash(row[1])
            if parsed_hash is None:
                results.append(
                    DoctorResult(
                        Severity.ERROR,
                        "installation_record_invalid",
                        f"Invalid RECORD hash for {row[0]} in {_display(installation)}",
                    )
                )
                continue
            algorithm, expected = parsed_hash
            try:
                observed = hashlib.new(algorithm, candidate.read_bytes()).digest()
            except (OSError, ValueError):
                results.append(
                    DoctorResult(
                        Severity.ERROR,
                        "installation_record_mismatch",
                        "Missing or unreadable RECORD entry "
                        f"{candidate} in {_display(installation)}",
                    )
                )
                continue
            if observed != expected:
                results.append(
                    DoctorResult(
                        Severity.ERROR,
                        "installation_record_mismatch",
                        f"RECORD hash mismatch for {candidate} in {_display(installation)}",
                    )
                )
    except (OSError, UnicodeDecodeError, csv.Error) as exc:
        results.append(
            DoctorResult(
                Severity.ERROR,
                "installation_record_unreadable",
                f"Cannot read RECORD {installation.record_path}: {exc}",
            )
        )
    return results, tracked


def _untracked_findings(installation: _Installation, tracked: set[str]) -> list[DoctorResult]:
    findings: list[DoctorResult] = []
    for path in _regular_files(installation.package_root):
        relative = path.relative_to(installation.package_root).as_posix()
        if relative in tracked or is_python_bytecode_path(path):
            continue
        findings.append(
            DoctorResult(
                Severity.ERROR,
                "installation_untracked_file",
                f"Untracked package file {path} in {_display(installation)}",
            )
        )
    return findings


def _hardlink_findings(installations: Iterable[_Installation]) -> list[DoctorResult]:
    entries: dict[tuple[int, int], list[Path]] = {}
    for installation in installations:
        for path in _regular_files(installation.package_root):
            try:
                metadata = path.stat(follow_symlinks=False)
            except OSError:
                continue
            entries.setdefault((metadata.st_dev, metadata.st_ino), []).append(path)
    return [
        DoctorResult(
            Severity.ERROR,
            "installation_hardlink",
            "Hard-linked installation entries share one inode: "
            + ", ".join(str(path) for path in sorted(paths)),
        )
        for paths in entries.values()
        if len(paths) > 1
    ]


def verify_installations(
    *, home: Path | None = None, roots: Iterable[Path] | None = None
) -> list[DoctorResult]:
    """Return read-only integrity findings for every discoverable installation.

    ``roots`` is an isolated-fixture seam. Production callers omit it so the
    active distribution, retained plugin generations, legacy uv tool, and uv
    archive peers are all inspected.
    """
    installations = _discover_installations(home=home or Path.home(), roots=roots)
    if not installations:
        return [
            DoctorResult(
                Severity.OK,
                "installation_integrity",
                "No discoverable AutoSkillit installations require verification",
            )
        ]

    results: list[DoctorResult] = []
    for installation in installations:
        record_findings, tracked = _verify_record(installation)
        results.extend(record_findings)
        results.extend(_untracked_findings(installation, tracked))
    results.extend(_hardlink_findings(installations))
    if not results:
        return [
            DoctorResult(
                Severity.OK,
                "installation_integrity",
                "All discoverable AutoSkillit installations match their RECORD files",
            )
        ]
    return results
