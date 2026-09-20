"""Installation, entry points, and version drift doctor checks."""

from __future__ import annotations

import base64
import csv
import hashlib
import importlib.metadata
import json
import shutil
import stat
import subprocess
import urllib.parse
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from autoskillit.core import Severity, get_logger, is_python_bytecode_path

from ._doctor_types import DoctorResult

if TYPE_CHECKING:
    from autoskillit.cli.install._install_info import InstallInfo

logger = get_logger(__name__)


def _format_upgrade_cmd(info: InstallInfo) -> str:
    from autoskillit.cli.install._install_info import upgrade_command

    cmd = upgrade_command(info)
    return " ".join(cmd.argv) if cmd else "autoskillit update"


def _check_autoskillit_on_path() -> DoctorResult:
    """Check that the autoskillit command is available on PATH."""
    if shutil.which("autoskillit") is None:
        return DoctorResult(
            Severity.WARNING,
            "autoskillit_on_path",
            "'autoskillit' command not found on PATH.",
        )
    return DoctorResult(Severity.OK, "autoskillit_on_path", "autoskillit command found on PATH")


def _check_editable_install_source_exists() -> DoctorResult:
    """Detect editable autoskillit installs whose source directory no longer exists."""
    import importlib.metadata as meta

    check_name = "editable_install_source_exists"
    try:
        dist = meta.Distribution.from_name("autoskillit")
    except meta.PackageNotFoundError:
        return DoctorResult(Severity.OK, check_name, "autoskillit not installed in this env")

    direct_url_text = dist.read_text("direct_url.json")
    if not direct_url_text:
        return DoctorResult(Severity.OK, check_name, "Not an editable install")

    try:
        direct_url = json.loads(direct_url_text)
    except json.JSONDecodeError:
        return DoctorResult(Severity.OK, check_name, "direct_url.json unreadable — skipped")

    is_editable = (
        direct_url.get("dir_info", {}).get("editable") is True
        or direct_url.get("editable") is True
    )
    if not is_editable:
        return DoctorResult(Severity.OK, check_name, "Not an editable install")

    url = direct_url.get("url", "")
    src_path = urllib.parse.urlparse(url).path if url.startswith("file://") else ""
    if src_path:
        # pip records the editable-install source URL with percent-encoded
        # special characters (e.g. literal " becomes %22). urllib's
        # parsed .path leaves the encoding intact, so unquote before the
        # filesystem existence check — otherwise a worktree whose directory
        # name contains shell-special characters is misclassified as
        # "deleted from a different path".
        src_path = urllib.parse.unquote(src_path)
    if not src_path or Path(src_path).exists():
        return DoctorResult(Severity.OK, check_name, "Editable install source directory exists")

    return DoctorResult(
        Severity.ERROR,
        check_name,
        f"autoskillit is installed from a deleted directory: {src_path}. "
        f"Fix: restore the editable source directory or re-run installation.",
    )


def _check_stale_entry_points() -> DoctorResult:
    """Detect autoskillit binaries on PATH outside ~/.local/bin (stale/poisoned installs)."""
    check_name = "stale_entry_points"
    primary = shutil.which("autoskillit")
    if not primary:
        return DoctorResult(Severity.OK, check_name, "autoskillit not found on PATH")

    try:
        result = subprocess.run(
            ["which", "-a", "autoskillit"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        all_paths = [p.strip() for p in result.stdout.splitlines() if p.strip()]
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        all_paths = [primary]

    expected_prefix = Path.home() / ".local"
    stale = [p for p in all_paths if not Path(p).is_relative_to(expected_prefix)]
    if not stale:
        return DoctorResult(Severity.OK, check_name, "No stale autoskillit entry points found")

    stale_list = ", ".join(stale)
    from autoskillit.cli.install._install_info import detect_install

    _info = detect_install()
    _cmd_str = _format_upgrade_cmd(_info)
    return DoctorResult(
        Severity.WARNING,
        check_name,
        f"Found autoskillit entry point(s) outside ~/.local/bin: {stale_list}. "
        f"These may be stale editable installs. "
        f"Fix: {_cmd_str} && autoskillit install",
    )


def _check_source_version_drift(home: Path | None = None) -> DoctorResult:
    """Network source-drift check.

    Compares the installed release identity against the target that the update
    command would install. Uses network-backed update resolution.
    """
    check_name = "source_version_drift"
    _home = home or Path.home()

    try:
        import autoskillit as _pkg
        from autoskillit.cli.install._install_info import (
            InstallType,
            detect_install,
            release_identity,
        )
        from autoskillit.cli.update._update_checks_source import resolve_target_identity
        from autoskillit.core import update_available

        info = detect_install()

        if info.install_type == InstallType.LOCAL_EDITABLE:
            return DoctorResult(
                Severity.OK, check_name, "Local editable install — drift check not applicable"
            )

        if info.install_type in (InstallType.UNKNOWN, InstallType.LOCAL_PATH):
            return DoctorResult(
                Severity.OK,
                check_name,
                "Not a source-tracked install — drift check not applicable",
            )

        from packaging.version import InvalidVersion, Version

        # ``autoskillit.__version__`` is unconditionally set at import time to a
        # non-empty string from ``importlib.metadata.version()``; the only way
        # it could be malformed is a corrupt distribution, which Version() catches.
        current = _pkg.__version__
        try:
            Version(current)
        except InvalidVersion:
            return DoctorResult(
                Severity.ERROR,
                check_name,
                "Installation integrity failure: autoskillit has an invalid __version__. "
                "Run `autoskillit install` before checking for source drift.",
            )

        target = resolve_target_identity(info, _home, network=True)

        if target is None:
            return DoctorResult(
                Severity.OK,
                check_name,
                "Source drift reference SHA unavailable — check network connectivity",
            )

        installed = release_identity(info, version=current)
        if not update_available(installed, target):
            return DoctorResult(Severity.OK, check_name, "No source drift detected")

        installed_short = (info.commit_id or "unknown")[:8]
        ref_short = (target.commit or target.version)[:8]
        _cmd_str = _format_upgrade_cmd(info)
        return DoctorResult(
            Severity.WARNING,
            check_name,
            f"Source drift: installed={installed_short}, reference={ref_short}. "
            f"Run: {_cmd_str} && autoskillit install",
        )

    except Exception:
        logger.debug("Source drift check failed", exc_info=True)
        return DoctorResult(
            Severity.OK, check_name, "Source drift check skipped (unexpected error)"
        )


def _check_install_classification() -> DoctorResult:
    """Classify the current autoskillit install type via direct_url.json."""
    check_name = "install_classification"
    try:
        from autoskillit.cli.install._install_info import InstallType, detect_install

        info = detect_install()
        if info.install_type == InstallType.UNKNOWN:
            return DoctorResult(
                Severity.WARNING,
                check_name,
                "install type could not be detected from direct_url.json",
            )
        commit_short = (info.commit_id or "")[:8]
        return DoctorResult(
            Severity.OK,
            check_name,
            f"install_type={info.install_type}, "
            f"requested_revision={info.requested_revision}, "
            f"commit_id={commit_short}",
        )
    except Exception:
        logger.debug("Install classification check failed", exc_info=True)
        return DoctorResult(
            Severity.OK, check_name, "Install classification check skipped (unexpected error)"
        )


def _check_publication_obligation(home: Path | None = None) -> DoctorResult:
    """Report a pending publication obligation, if any (diagnostic only).

    No auto-fix here: full publication repair lives at the update-failure
    handler and non-server CLI startup (see cli.app.main() and
    cli.update._obligation_repair). MCP startup can repair broken hook
    commands but cannot satisfy or clear the broader publication obligation.
    """
    check_name = "publication_obligation"
    _home = home or Path.home()
    try:
        from autoskillit.workspace import read_obligation

        obligation = read_obligation(_home)
        if obligation is None:
            return DoctorResult(Severity.OK, check_name, "No publication obligation pending")
        return DoctorResult(
            Severity.WARNING,
            check_name,
            f"Publication owed since {obligation.written_at} "
            f"(previous_version={obligation.previous_version}, "
            f"expected_version={obligation.expected_version or 'unknown'}). "
            f"Run `autoskillit install` from an external terminal, or run a "
            f"healthy non-server CLI command to trigger automatic repair.",
        )
    except Exception as exc:
        logger.debug("Publication obligation check failed", exc_info=True)
        return DoctorResult(
            Severity.WARNING,
            check_name,
            f"Could not determine publication obligation state: {exc}",
        )


def _check_update_dismissal_state(home: Path | None = None) -> DoctorResult:
    """Report the current update-prompt dismissal state."""
    check_name = "update_dismissal_state"
    _home = home or Path.home()
    try:
        from autoskillit.cli.install._install_info import detect_install, dismissal_window
        from autoskillit.cli.update._update_checks import _read_dismiss_state

        state = _read_dismiss_state(_home)
        entry = state.get("update_prompt")
        if not isinstance(entry, dict) or "dismissed_at" not in entry:
            return DoctorResult(Severity.OK, check_name, "No active dismissal")

        from datetime import datetime

        info = detect_install()
        window = dismissal_window(info)
        dismissed_at = datetime.fromisoformat(str(entry["dismissed_at"]))
        expiry = (dismissed_at + window).strftime("%Y-%m-%d")
        conditions = entry.get("conditions", [])
        return DoctorResult(
            Severity.OK,
            check_name,
            f"update_prompt dismissed until {expiry}; conditions={conditions}",
        )
    except Exception:
        logger.debug("Update dismissal state check failed", exc_info=True)
        return DoctorResult(
            Severity.OK, check_name, "Update dismissal state check skipped (unexpected error)"
        )


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
    except FileNotFoundError:
        # No uv cache directory yet — a normal "fresh install" state.
        pass
    except OSError as exc:
        # Cache directory exists but is unreadable. Surface as a debug
        # diagnostic so an operator can distinguish missing cache from a
        # permission/IO problem.
        logger.debug("uv cache directory unreadable", exc_info=exc)
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
        except FileNotFoundError:
            # Candidate installation vanished between discovery and resolve —
            # benign race; nothing to verify.
            continue
        except OSError as exc:
            # Filesystem error on a candidate path. Log so an operator can
            # distinguish a transient I/O issue from a stable symlink loop.
            logger.debug(
                "installation candidate unresolvable",
                extra={"label": label, "package_root": str(package_root)},
                exc_info=exc,
            )
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
            except ValueError:
                results.append(
                    DoctorResult(
                        Severity.ERROR,
                        "installation_record_unknown_algorithm",
                        f"Unknown RECORD hash algorithm {algorithm!r} "
                        f"for {candidate} in {_display(installation)}",
                    )
                )
                continue
            except OSError:
                results.append(
                    DoctorResult(
                        Severity.ERROR,
                        "installation_record_unreadable_entry",
                        f"Unreadable RECORD entry {candidate} in {_display(installation)}",
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
