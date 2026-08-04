"""IL-0 canonical remote and repository identity authority.

Provides a synchronous resolver usable from any import layer (including
hooks that cannot use asyncio) and the single-source-of-truth constant
for remote probe ordering.  Repository-profile activation deliberately uses
the same precedence as clone isolation: ``upstream`` before ``origin``.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

REMOTE_PRECEDENCE: tuple[str, ...] = ("upstream", "origin")

_GITHUB_HOST = "github.com"
_GITHUB_COMPONENT_RE = re.compile(r"[A-Za-z0-9_.-]+\Z")
_GITHUB_SCP_RE = re.compile(
    r"git@github\.com:(?P<owner>[A-Za-z0-9_.-]+)/"
    r"(?P<repository>[A-Za-z0-9_.-]+?)(?:\.git)?\Z",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class GitHubRepositoryRef:
    """A validated GitHub owner/repository reference from a configured remote."""

    owner: str
    repository: str
    transport: Literal["https", "ssh"]

    @property
    def normalized_identity(self) -> str:
        """Return the comparison identity; GitHub slugs are case-insensitive."""
        return f"{_GITHUB_HOST}/{self.owner.casefold()}/{self.repository.casefold()}"

    @property
    def display_identity(self) -> str:
        """Return the source-cased identity for diagnostics."""
        return f"{_GITHUB_HOST}/{self.owner}/{self.repository}"


@dataclass(frozen=True, slots=True)
class RemoteIdentityProbe:
    """One configured remote observed while resolving repository identity."""

    name: str
    url: str
    usable: bool
    github_repository: GitHubRepositoryRef | None
    diagnostic: str = ""


@dataclass(frozen=True, slots=True)
class RemoteIdentityResolution:
    """Deterministic result of the canonical upstream-before-origin probe."""

    selected_remote: str
    selected_url: str
    repository: GitHubRepositoryRef | None
    usable_remote_found: bool
    probes: tuple[RemoteIdentityProbe, ...]
    conflicting_github_identities: tuple[str, ...] = ()


def _split_github_path(path: str) -> tuple[str, str] | None:
    """Validate and split an exact two-component GitHub repository path."""
    if "%" in path or "\\" in path:
        return None
    components = path.strip("/").split("/")
    if len(components) != 2:
        return None
    owner, repository = components
    if repository.casefold().endswith(".git"):
        repository = repository[:-4]
    if not owner or not repository:
        return None
    if not _GITHUB_COMPONENT_RE.fullmatch(owner):
        return None
    if not _GITHUB_COMPONENT_RE.fullmatch(repository):
        return None
    return owner, repository


def parse_github_remote_url(url: str) -> GitHubRepositoryRef | None:
    """Parse an exact GitHub HTTPS or SSH remote URL.

    Host substring matches, HTTP, alternate ports, embedded credentials, query
    strings, fragments, and paths other than ``owner/repository`` are rejected.
    The source casing is retained for diagnostics while
    :attr:`GitHubRepositoryRef.normalized_identity` defines comparison semantics.
    """
    candidate = url.strip()
    if not candidate or any(character.isspace() for character in candidate):
        return None

    scp_match = _GITHUB_SCP_RE.fullmatch(candidate)
    if scp_match is not None:
        return GitHubRepositoryRef(
            owner=scp_match.group("owner"),
            repository=scp_match.group("repository"),
            transport="ssh",
        )

    parsed = urlsplit(candidate)
    try:
        port = parsed.port
    except ValueError:
        return None
    if parsed.query or parsed.fragment or port is not None:
        return None
    scheme = parsed.scheme.casefold()
    if scheme == "https":
        if parsed.hostname is None or parsed.hostname.casefold() != _GITHUB_HOST:
            return None
        if parsed.username is not None or parsed.password is not None:
            return None
        transport: Literal["https", "ssh"] = "https"
    elif scheme == "ssh":
        if parsed.hostname is None or parsed.hostname.casefold() != _GITHUB_HOST:
            return None
        if parsed.username != "git" or parsed.password is not None:
            return None
        transport = "ssh"
    else:
        return None

    components = _split_github_path(parsed.path)
    if components is None:
        return None
    owner, repository = components
    return GitHubRepositoryRef(
        owner=owner,
        repository=repository,
        transport=transport,
    )


def _probe_remote_sync(cwd: str | Path, name: str) -> RemoteIdentityProbe:
    try:
        result = subprocess.run(
            ["git", "remote", "get-url", name],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=15,
        )
    except subprocess.TimeoutExpired:
        return RemoteIdentityProbe(
            name=name,
            url="",
            usable=False,
            github_repository=None,
            diagnostic="timeout",
        )
    except OSError as exc:
        return RemoteIdentityProbe(
            name=name,
            url="",
            usable=False,
            github_repository=None,
            diagnostic=f"os_error:{type(exc).__name__}",
        )
    if result.returncode != 0:
        return RemoteIdentityProbe(
            name=name,
            url="",
            usable=False,
            github_repository=None,
            diagnostic="not_configured",
        )
    url = result.stdout.strip()
    if not url or url.casefold().startswith("file://"):
        return RemoteIdentityProbe(
            name=name,
            url=url,
            usable=False,
            github_repository=None,
            diagnostic="empty" if not url else "clone_isolation_file_url",
        )
    github_repository = parse_github_remote_url(url)
    return RemoteIdentityProbe(
        name=name,
        url=url,
        usable=True,
        github_repository=github_repository,
        diagnostic="" if github_repository is not None else "non_github_remote",
    )


def resolve_repository_remote_identity_sync(
    cwd: str | Path,
) -> RemoteIdentityResolution:
    """Resolve configured repository identity using canonical remote precedence.

    The first usable remote is authoritative.  Lower-precedence GitHub remotes
    are retained as conflict diagnostics but cannot override it.  This means the
    common fork layout (official ``upstream``, fork ``origin``) activates the
    official repository, while a fork in the authoritative slot never does.
    """
    probes = tuple(_probe_remote_sync(cwd, name) for name in REMOTE_PRECEDENCE)
    selected = next((probe for probe in probes if probe.usable), None)
    if selected is None:
        return RemoteIdentityResolution(
            selected_remote="",
            selected_url="",
            repository=None,
            usable_remote_found=False,
            probes=probes,
        )

    selected_identity = (
        selected.github_repository.normalized_identity
        if selected.github_repository is not None
        else ""
    )
    conflicts = tuple(
        sorted(
            {
                probe.github_repository.normalized_identity
                for probe in probes
                if probe.usable
                and probe.github_repository is not None
                and probe.github_repository.normalized_identity != selected_identity
            }
        )
    )
    return RemoteIdentityResolution(
        selected_remote=selected.name,
        selected_url=selected.url,
        repository=selected.github_repository,
        usable_remote_found=True,
        probes=probes,
        conflicting_github_identities=conflicts,
    )


def resolve_clone_remote_name_sync(cwd: str | Path) -> str:
    """Return the git remote name to use for fetch/push operations (sync).

    Tries remotes in precedence order (upstream before origin).
    Rejects file:// URLs — those indicate a clone-isolation origin.
    Falls back to "origin" if no remote qualifies.
    """
    for name in REMOTE_PRECEDENCE:
        try:
            result = subprocess.run(
                ["git", "remote", "get-url", name],
                cwd=str(cwd),
                capture_output=True,
                text=True,
                timeout=15,
            )
            if result.returncode != 0:
                continue
            url = result.stdout.strip()
            if url.startswith("file://"):
                continue
            return name
        except (subprocess.TimeoutExpired, OSError):
            continue
    return "origin"
