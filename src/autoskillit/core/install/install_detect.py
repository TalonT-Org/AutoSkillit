"""Install-type detection for feature gating — IL-0."""

from __future__ import annotations

import json
import logging
import re
import subprocess
import tomllib
from dataclasses import dataclass
from enum import StrEnum, unique
from pathlib import Path
from typing import TypedDict
from urllib.parse import unquote, urlparse

__all__ = [
    "DirectUrlInfo",
    "SourceCurrency",
    "SourceCurrencyStatus",
    "autoskillit_source_version",
    "distribution_version_at",
    "file_url_path",
    "is_dev_install",
    "parse_direct_url",
    "source_currency",
    "_is_release_tag",
    "_is_stable_track",
]

logger = logging.getLogger(__name__)  # noqa: TID251 — IL-0 module, no autoskillit imports allowed


class DirectUrlInfo(TypedDict):
    install_type: str
    requested_revision: str | None
    commit_id: str | None
    editable: bool
    url: str


@dataclass(frozen=True, slots=True)
class SourceCurrency:
    status: SourceCurrencyStatus
    installed_commit: str | None
    checkout_head: str | None
    behind_by: int | None
    generation_root: Path | None
    installed_version: str | None = None
    checkout_version: str | None = None
    install_type: str | None = None


@unique
class SourceCurrencyStatus(StrEnum):
    CURRENT = "current"
    STALE = "stale"
    DIVERGED = "diverged"
    NOT_SOURCE_CHECKOUT = "not_source_checkout"
    UNKNOWN = "unknown"


def _git(checkout: Path, *args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(checkout), *args],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def file_url_path(url: str) -> Path | None:
    """Return the local absolute path named by a ``file://`` URL, else ``None``.

    pip percent-encodes special characters in ``direct_url.json`` URLs, so the
    path component is unquoted rather than sliced off the prefix.
    """
    parsed = urlparse(url)
    if parsed.scheme != "file" or parsed.netloc not in ("", "localhost"):
        return None
    if not parsed.path.startswith("/"):
        return None
    return Path(unquote(parsed.path))


def autoskillit_source_version(root: Path) -> str | None:
    """Return ``[project].version`` when ``root`` is an autoskillit source tree."""
    try:
        with (root / "pyproject.toml").open("rb") as handle:
            data = tomllib.load(handle)
    except (OSError, ValueError):
        return None
    project = data.get("project")
    if not isinstance(project, dict) or project.get("name") != "autoskillit":
        return None
    version = project.get("version")
    return version if isinstance(version, str) and version else None


def _running_distribution_version() -> str | None:
    import importlib.metadata

    try:
        return importlib.metadata.version("autoskillit")
    except importlib.metadata.PackageNotFoundError:
        return None


def _version_currency(
    checkout: Path, info: DirectUrlInfo, generation_root: Path | None
) -> SourceCurrency:
    install_type = info["install_type"]
    installed = (
        distribution_version_at(generation_root)
        if generation_root is not None
        else _running_distribution_version()
    )
    checkout_version = autoskillit_source_version(checkout)

    def result(status: SourceCurrencyStatus) -> SourceCurrency:
        return SourceCurrency(
            status,
            installed_commit=None,
            checkout_head=None,
            behind_by=None,
            generation_root=generation_root,
            installed_version=installed,
            checkout_version=checkout_version,
            install_type=install_type,
        )

    if checkout_version is None:
        return result(SourceCurrencyStatus.NOT_SOURCE_CHECKOUT)
    if install_type == "local-path":
        recorded = file_url_path(info["url"])
        if recorded is None or recorded.resolve() != checkout.resolve():
            return result(SourceCurrencyStatus.NOT_SOURCE_CHECKOUT)
    if installed is None:
        return result(SourceCurrencyStatus.UNKNOWN)
    return result(
        SourceCurrencyStatus.CURRENT
        if installed == checkout_version
        else SourceCurrencyStatus.STALE
    )


def source_currency(checkout: Path, *, generation_root: Path | None) -> SourceCurrency:
    """Compare the deployed generation provenance with a checkout.

    Commit-bearing provenance compares against the checkout's ``HEAD``;
    ``local-path`` and unknown provenance compare the installed version
    against the checkout's ``[project].version``.
    """
    info = parse_direct_url(generation_root) if generation_root is not None else parse_direct_url()
    install_type = info["install_type"]
    if install_type in ("local-path", "unknown"):
        return _version_currency(checkout, info, generation_root)
    if generation_root is None:
        return SourceCurrency(
            SourceCurrencyStatus.UNKNOWN, None, None, None, None, install_type=install_type
        )
    head = _git(checkout, "rev-parse", "HEAD")
    if head is None:
        return SourceCurrency(
            SourceCurrencyStatus.UNKNOWN,
            info["commit_id"],
            None,
            None,
            generation_root,
            install_type=install_type,
        )
    if install_type == "local-editable":
        installed_path = file_url_path(info["url"])
        return SourceCurrency(
            SourceCurrencyStatus.CURRENT
            if installed_path is not None and installed_path.resolve() == checkout.resolve()
            else SourceCurrencyStatus.NOT_SOURCE_CHECKOUT,
            None,
            head,
            None,
            generation_root,
            install_type=install_type,
        )
    commit = info["commit_id"]
    if install_type != "git-vcs" or not commit:
        return SourceCurrency(
            SourceCurrencyStatus.UNKNOWN,
            commit,
            head,
            None,
            generation_root,
            install_type=install_type,
        )
    if commit == head:
        return SourceCurrency(
            SourceCurrencyStatus.CURRENT,
            commit,
            head,
            0,
            generation_root,
            install_type=install_type,
        )
    if _git(checkout, "cat-file", "-e", f"{commit}^{{commit}}") is None:
        return SourceCurrency(
            SourceCurrencyStatus.NOT_SOURCE_CHECKOUT,
            commit,
            head,
            None,
            generation_root,
            install_type=install_type,
        )
    if _git(checkout, "merge-base", "--is-ancestor", commit, "HEAD") is None:
        return SourceCurrency(
            SourceCurrencyStatus.DIVERGED,
            commit,
            head,
            None,
            generation_root,
            install_type=install_type,
        )
    behind = _git(checkout, "rev-list", "--count", f"{commit}..HEAD")
    return SourceCurrency(
        SourceCurrencyStatus.STALE,
        commit,
        head,
        int(behind) if behind is not None else None,
        generation_root,
        install_type=install_type,
    )


def _unknown_direct_url() -> DirectUrlInfo:
    return {
        "install_type": "unknown",
        "requested_revision": None,
        "commit_id": None,
        "editable": False,
        "url": "",
    }


def _is_release_tag(rev: str) -> bool:
    """Return True if rev looks like a version tag (e.g. 'v0.7.75', '0.7.75')."""
    return bool(re.fullmatch(r"v?\d+(\.\d+)*", rev))


def _is_stable_track(rev: str | None) -> bool:
    return not rev or rev in ("main", "stable") or _is_release_tag(rev)


def _parse_direct_url_text(raw: str | None) -> DirectUrlInfo:
    if not raw:
        return _unknown_direct_url()
    data = json.loads(raw)
    raw_url = data.get("url")
    url = raw_url if isinstance(raw_url, str) else ""
    vcs_info = data.get("vcs_info", {})
    if isinstance(vcs_info, dict) and vcs_info.get("vcs") == "git":
        return {
            "install_type": "git-vcs",
            "requested_revision": vcs_info.get("requested_revision") or None,
            "commit_id": vcs_info.get("commit_id") or None,
            "editable": False,
            "url": url,
        }
    dir_info = data.get("dir_info", {})
    if isinstance(dir_info, dict) and dir_info.get("editable") is True:
        return {
            "install_type": "local-editable",
            "requested_revision": None,
            "commit_id": None,
            "editable": True,
            "url": url,
        }
    if url.startswith("file://"):
        return {
            "install_type": "local-path",
            "requested_revision": None,
            "commit_id": None,
            "editable": False,
            "url": url,
        }
    unknown = _unknown_direct_url()
    unknown["url"] = url
    return unknown


def _staged_dist_info_paths(root: Path) -> tuple[Path, ...]:
    pattern = "autoskillit/lib/python*/site-packages/autoskillit-*.dist-info"
    resolved: dict[str, Path] = {}
    for candidate in root.glob(pattern):
        try:
            path = candidate.resolve()
        except OSError:
            continue
        resolved[str(path)] = path
    return tuple(resolved[key] for key in sorted(resolved))


def _staged_direct_url_paths(root: Path) -> tuple[Path, ...]:
    pattern = "autoskillit/lib/python*/site-packages/autoskillit-*.dist-info/direct_url.json"
    resolved: dict[str, Path] = {}
    for candidate in root.glob(pattern):
        try:
            path = candidate.resolve()
        except OSError:
            continue
        resolved[str(path)] = path
    return tuple(resolved[key] for key in sorted(resolved))


def parse_direct_url(root: Path | None = None) -> DirectUrlInfo:
    """Parse direct_url.json and return a canonical install descriptor.

    Keys: install_type (str), requested_revision (str|None),
          commit_id (str|None), editable (bool), url (str).
    """
    try:
        import importlib.metadata

        if root is None:
            dist = importlib.metadata.Distribution.from_name("autoskillit")
            return _parse_direct_url_text(dist.read_text("direct_url.json"))
        paths = _staged_direct_url_paths(root)
        if paths:
            try:
                raw = paths[0].read_text(encoding="utf-8")
            except OSError:
                return _unknown_direct_url()
            return _parse_direct_url_text(raw)
        return _unknown_direct_url()
    except Exception:
        logger.debug("direct_url.json parsing failed", exc_info=True)
        return _unknown_direct_url()


def distribution_version_at(root: Path) -> str | None:
    """Return the installed distribution version found below a uv tool root."""
    for dist_info in _staged_dist_info_paths(root):
        name = dist_info.name
        prefix = "autoskillit-"
        suffix = ".dist-info"
        if name.startswith(prefix) and name.endswith(suffix):
            version = name[len(prefix) : -len(suffix)]
            if version:
                return version
    return None


def is_dev_install() -> bool:
    """Return True if a development install (editable or dev-track VCS); False on any error."""
    try:
        info = parse_direct_url()
        if info["editable"]:
            return True
        if info["install_type"] == "git-vcs" and not _is_stable_track(info["requested_revision"]):
            return True
        return False
    except Exception:
        logger.debug("install type detection failed", exc_info=True)
        return False
