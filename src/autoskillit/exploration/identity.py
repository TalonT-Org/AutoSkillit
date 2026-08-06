"""Repository identity resolution and offline/archive activation authority."""

from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import regex as re

from autoskillit.core import (
    GitHubRepositoryRef,
    RemoteIdentityResolution,
    RepositoryIdentity,
    parse_github_remote_url,
    resolve_repository_remote_identity_sync,
)

from ._digest import qualified_digest

AUTOSKILLIT_REPOSITORY_IDENTITY = "github.com/talont-org/autoskillit"
AUTOSKILLIT_REPOSITORY_DISPLAY_IDENTITY = "github.com/TalonT-Org/AutoSkillit"
OFFLINE_DECLARATION_PATH = ".autoskillit/repository-profile.v1.json"
OFFLINE_DECLARATION_SCHEMA_VERSION = 1
OFFLINE_DECLARATION_DIGEST_DOMAIN = b"autoskillit.offline-repository-profile.v1\0"
REPOSITORY_IDENTITY_DIGEST_DOMAIN = b"autoskillit.repository-identity.v1\0"
OFFLINE_REQUIRED_MARKER_PATHS: tuple[str, ...] = (
    "pyproject.toml",
    "src/autoskillit/__init__.py",
)
OFFLINE_QUORUM_MARKER_PATHS: tuple[str, ...] = (
    "src/autoskillit/core/__init__.py",
    "src/autoskillit/execution/__init__.py",
    "src/autoskillit/recipe/__init__.py",
    "src/autoskillit/server/__init__.py",
)
OFFLINE_MARKER_QUORUM = 3
_QUALIFIED_SHA256_RE = re.compile(r"sha256:[0-9a-f]{64}\Z")

_IDENTITY_CONFIG_SOURCE_URL = "autoskillit.repositoryIdentity.sourceUrl"
_IDENTITY_CONFIG_SOURCE_REMOTE = "autoskillit.repositoryIdentity.sourceRemote"
_IDENTITY_CONFIG_SOURCE_USABLE = "autoskillit.repositoryIdentity.sourceUsable"
_IDENTITY_CONFIG_OVERRIDE_APPLIED = "autoskillit.repositoryIdentity.overrideApplied"

IdentitySource = Literal["remote", "trusted_pre_override", "offline_declaration", "unresolved"]


@dataclass(frozen=True, slots=True)
class IdentityEvidence:
    """One deterministic identity/activation observation."""

    source: str
    value: str
    accepted: bool
    diagnostic: str


@dataclass(frozen=True, slots=True)
class RepositoryIdentityResolution:
    """Repository identity plus activation-safe provenance."""

    normalized_identity: str
    display_identity: str
    source: IdentitySource
    source_remote: str
    usable_remote_found: bool
    autoskillit_overlay: bool
    evidence: tuple[IdentityEvidence, ...]
    repository_identity: RepositoryIdentity

    @property
    def identity_digest(self) -> str:
        return qualified_digest(
            REPOSITORY_IDENTITY_DIGEST_DOMAIN,
            {
                "normalized_identity": self.normalized_identity,
                "source": self.source,
                "source_remote": self.source_remote,
                "usable_remote_found": self.usable_remote_found,
            },
        )


@dataclass(frozen=True, slots=True)
class _TrustedCloneSource:
    override_applied: bool
    usable: bool
    url: str
    remote_name: str


def _git_config(root: Path, key: str) -> str:
    try:
        result = subprocess.run(
            ["git", "config", "--local", "--get", key],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def _git_value(root: Path, *args: str) -> str:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def _canonical_repository_identity(
    root: Path,
    *,
    repository: str,
    github: GitHubRepositoryRef | None = None,
    archive_revision: str = "",
) -> RepositoryIdentity:
    worktree = _git_value(root, "rev-parse", "--show-toplevel") or str(root)
    common_git_dir = _git_value(root, "rev-parse", "--path-format=absolute", "--git-common-dir")
    revision = _git_value(root, "rev-parse", "--verify", "HEAD") or archive_revision or "unborn"
    return RepositoryIdentity(
        repository=repository,
        revision=revision,
        host="github.com" if github is not None else "",
        owner=github.owner if github is not None else "",
        repo=github.repository if github is not None else "",
        common_git_dir=str(Path(common_git_dir).resolve()) if common_git_dir else "",
        worktree_path=str(Path(worktree).resolve()),
    )


def _trusted_clone_source(root: Path) -> _TrustedCloneSource | None:
    override = _git_config(root, _IDENTITY_CONFIG_OVERRIDE_APPLIED).casefold()
    if override not in {"true", "false"}:
        return None
    usable = _git_config(root, _IDENTITY_CONFIG_SOURCE_USABLE).casefold()
    if usable not in {"true", "false"}:
        return None
    return _TrustedCloneSource(
        override_applied=override == "true",
        usable=usable == "true",
        url=_git_config(root, _IDENTITY_CONFIG_SOURCE_URL),
        remote_name=_git_config(root, _IDENTITY_CONFIG_SOURCE_REMOTE),
    )


def _remote_evidence(resolution: RemoteIdentityResolution) -> tuple[IdentityEvidence, ...]:
    evidence: list[IdentityEvidence] = []
    for probe in resolution.probes:
        value = (
            probe.github_repository.display_identity
            if probe.github_repository is not None
            else probe.url
        )
        evidence.append(
            IdentityEvidence(
                source=f"git_remote:{probe.name}",
                value=value,
                accepted=probe.name == resolution.selected_remote and probe.usable,
                diagnostic=probe.diagnostic,
            )
        )
    for conflict in resolution.conflicting_github_identities:
        evidence.append(
            IdentityEvidence(
                source="lower_precedence_conflict",
                value=conflict,
                accepted=False,
                diagnostic="ignored_by_upstream_before_origin_precedence",
            )
        )
    return tuple(evidence)


def _canonical_declaration_payload(raw: dict[str, object]) -> bytes:
    payload = {key: value for key, value in raw.items() if key != "content_digest"}
    return json.dumps(
        payload,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")


def _validate_offline_declaration(root: Path) -> tuple[bool, tuple[IdentityEvidence, ...]]:
    declaration_path = root / OFFLINE_DECLARATION_PATH
    try:
        raw_bytes = declaration_path.read_bytes()
    except FileNotFoundError:
        return False, (
            IdentityEvidence(
                source="offline_declaration",
                value=str(OFFLINE_DECLARATION_PATH),
                accepted=False,
                diagnostic="missing",
            ),
        )
    except OSError as exc:
        return False, (
            IdentityEvidence(
                source="offline_declaration",
                value=str(OFFLINE_DECLARATION_PATH),
                accepted=False,
                diagnostic=f"unreadable:{type(exc).__name__}",
            ),
        )
    if len(raw_bytes) > 32 * 1024:
        return False, (
            IdentityEvidence(
                source="offline_declaration",
                value=str(OFFLINE_DECLARATION_PATH),
                accepted=False,
                diagnostic="oversized",
            ),
        )
    try:
        raw = json.loads(raw_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raw = None
    if not isinstance(raw, dict) or set(raw) != {
        "schema_version",
        "repository",
        "required_markers",
        "quorum_markers",
        "marker_quorum",
        "content_digest",
    }:
        return False, (
            IdentityEvidence(
                source="offline_declaration",
                value=str(OFFLINE_DECLARATION_PATH),
                accepted=False,
                diagnostic="invalid_schema",
            ),
        )
    required_markers = raw.get("required_markers")
    quorum_markers = raw.get("quorum_markers")
    if (
        raw.get("schema_version") != OFFLINE_DECLARATION_SCHEMA_VERSION
        or str(raw.get("repository", "")).casefold() != AUTOSKILLIT_REPOSITORY_IDENTITY
        or raw.get("marker_quorum") != OFFLINE_MARKER_QUORUM
        or not isinstance(required_markers, dict)
        or tuple(required_markers) != OFFLINE_REQUIRED_MARKER_PATHS
        or not isinstance(quorum_markers, dict)
        or tuple(quorum_markers) != OFFLINE_QUORUM_MARKER_PATHS
        or not all(
            isinstance(value, str) and _QUALIFIED_SHA256_RE.fullmatch(value)
            for value in (*required_markers.values(), *quorum_markers.values())
        )
    ):
        return False, (
            IdentityEvidence(
                source="offline_declaration",
                value=str(OFFLINE_DECLARATION_PATH),
                accepted=False,
                diagnostic="unsupported_declaration",
            ),
        )
    content = OFFLINE_DECLARATION_DIGEST_DOMAIN + _canonical_declaration_payload(raw)
    expected_content_digest = f"sha256:{hashlib.sha256(content).hexdigest()}"
    if raw.get("content_digest") != expected_content_digest:
        return False, (
            IdentityEvidence(
                source="offline_declaration",
                value=str(OFFLINE_DECLARATION_PATH),
                accepted=False,
                diagnostic="content_digest_mismatch",
            ),
        )

    marker_evidence: list[IdentityEvidence] = []

    def marker_matches(marker_path: str, declared_digest: str, source: str) -> bool:
        marker = root / marker_path
        if marker.is_symlink() or not marker.is_file():
            accepted = False
            diagnostic = "missing"
        else:
            try:
                digest = f"sha256:{hashlib.sha256(marker.read_bytes()).hexdigest()}"
            except OSError as exc:
                accepted = False
                diagnostic = f"unreadable:{type(exc).__name__}"
            else:
                accepted = digest == declared_digest
                diagnostic = "digest_match" if accepted else "digest_mismatch"
        marker_evidence.append(
            IdentityEvidence(
                source=source,
                value=marker_path,
                accepted=accepted,
                diagnostic=diagnostic,
            )
        )
        return accepted

    required_matched = sum(
        marker_matches(path, required_markers[path], "offline_required_marker")
        for path in OFFLINE_REQUIRED_MARKER_PATHS
    )
    quorum_matched = sum(
        marker_matches(path, quorum_markers[path], "offline_quorum_marker")
        for path in OFFLINE_QUORUM_MARKER_PATHS
    )
    declaration_accepted = (
        required_matched == len(OFFLINE_REQUIRED_MARKER_PATHS)
        and quorum_matched >= OFFLINE_MARKER_QUORUM
    )
    declaration_evidence = IdentityEvidence(
        source="offline_declaration",
        value=str(OFFLINE_DECLARATION_PATH),
        accepted=declaration_accepted,
        diagnostic=(
            f"required_markers:{required_matched}/{len(OFFLINE_REQUIRED_MARKER_PATHS)};"
            f"cross_layer_quorum:{quorum_matched}/{len(OFFLINE_QUORUM_MARKER_PATHS)}"
        ),
    )
    return declaration_accepted, (declaration_evidence, *marker_evidence)


def _from_github_ref(
    repository: GitHubRepositoryRef,
    *,
    root: Path,
    source: Literal["remote", "trusted_pre_override"],
    source_remote: str,
    usable_remote_found: bool,
    evidence: tuple[IdentityEvidence, ...],
) -> RepositoryIdentityResolution:
    normalized = repository.normalized_identity
    active = normalized == AUTOSKILLIT_REPOSITORY_IDENTITY
    canonical_github = (
        GitHubRepositoryRef("TalonT-Org", "AutoSkillit", repository.transport)
        if active
        else repository
    )
    return RepositoryIdentityResolution(
        normalized_identity=normalized,
        display_identity=repository.display_identity,
        source=source,
        source_remote=source_remote,
        usable_remote_found=usable_remote_found,
        autoskillit_overlay=active,
        evidence=evidence
        + (
            IdentityEvidence(
                source="autoskillit_overlay",
                value=normalized,
                accepted=active,
                diagnostic="exact_identity" if active else "fork_or_other_repository",
            ),
        ),
        repository_identity=_canonical_repository_identity(
            root,
            repository=normalized,
            github=canonical_github,
        ),
    )


def resolve_repository_identity(root: str | Path) -> RepositoryIdentityResolution:
    """Resolve repository identity without accepting caller or prompt hints."""
    resolved_root = Path(root).resolve()
    remotes = resolve_repository_remote_identity_sync(resolved_root)
    evidence = _remote_evidence(remotes)
    trusted = _trusted_clone_source(resolved_root)

    # A clone-time override changes operational upstream, not repository identity.
    # When present, only the configured source observed before that override is
    # eligible to activate a repository-specific profile.
    if trusted is not None and trusted.override_applied:
        trusted_ref = parse_github_remote_url(trusted.url) if trusted.usable else None
        trusted_evidence = IdentityEvidence(
            source="trusted_pre_override",
            value=trusted.url,
            accepted=trusted.usable and trusted_ref is not None,
            diagnostic=(
                "configured_source_remote"
                if trusted.usable and trusted_ref is not None
                else "no_usable_github_source"
            ),
        )
        evidence = evidence + (trusted_evidence,)
        if trusted_ref is not None:
            return _from_github_ref(
                trusted_ref,
                root=resolved_root,
                source="trusted_pre_override",
                source_remote=trusted.remote_name,
                usable_remote_found=True,
                evidence=evidence,
            )
        usable_remote_found = trusted.usable
    else:
        usable_remote_found = remotes.usable_remote_found
        if remotes.repository is not None:
            return _from_github_ref(
                remotes.repository,
                root=resolved_root,
                source="remote",
                source_remote=remotes.selected_remote,
                usable_remote_found=True,
                evidence=evidence,
            )

    # A usable non-GitHub remote is positive evidence for another/unknown
    # repository, so the offline declaration is intentionally not consulted.
    if usable_remote_found:
        return RepositoryIdentityResolution(
            normalized_identity="",
            display_identity="",
            source="unresolved",
            source_remote=(
                trusted.remote_name if trusted is not None else remotes.selected_remote
            ),
            usable_remote_found=True,
            autoskillit_overlay=False,
            evidence=evidence
            + (
                IdentityEvidence(
                    source="offline_declaration",
                    value=str(OFFLINE_DECLARATION_PATH),
                    accepted=False,
                    diagnostic="forbidden_when_usable_remote_exists",
                ),
            ),
            repository_identity=_canonical_repository_identity(
                resolved_root,
                repository=remotes.selected_url or "local-repository",
            ),
        )

    offline_active, offline_evidence = _validate_offline_declaration(resolved_root)
    if offline_active:
        declaration_bytes = (resolved_root / OFFLINE_DECLARATION_PATH).read_bytes()
        archive_revision = f"sha256:{hashlib.sha256(declaration_bytes).hexdigest()}"
        official = GitHubRepositoryRef("TalonT-Org", "AutoSkillit", "https")
        return RepositoryIdentityResolution(
            normalized_identity=AUTOSKILLIT_REPOSITORY_IDENTITY,
            display_identity=AUTOSKILLIT_REPOSITORY_DISPLAY_IDENTITY,
            source="offline_declaration",
            source_remote="",
            usable_remote_found=False,
            autoskillit_overlay=True,
            evidence=evidence + offline_evidence,
            repository_identity=_canonical_repository_identity(
                resolved_root,
                repository=AUTOSKILLIT_REPOSITORY_IDENTITY,
                github=official,
                archive_revision=archive_revision,
            ),
        )
    return RepositoryIdentityResolution(
        normalized_identity="",
        display_identity="",
        source="unresolved",
        source_remote="",
        usable_remote_found=False,
        autoskillit_overlay=False,
        evidence=evidence + offline_evidence,
        repository_identity=_canonical_repository_identity(
            resolved_root,
            repository="local-repository",
        ),
    )
