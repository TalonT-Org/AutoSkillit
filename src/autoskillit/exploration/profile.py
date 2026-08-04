"""Repository profile detection independent of collector implementation."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path

from autoskillit.core import (
    ExplorationApplicability,
    ProfileActivation,
    RepositoryProfileId,
)

from .identity import RepositoryIdentityResolution, resolve_repository_identity

PROFILE_SCHEMA_VERSION = "autoskillit.repository-profiles.v1"
PROFILE_ACTIVATION_DIGEST_DOMAIN = b"autoskillit.profile-activation.v1\0"


@dataclass(frozen=True, slots=True)
class RepositoryProfileActivation:
    """Canonical activations plus the evidence and versions that produced them."""

    activations: tuple[ProfileActivation, ...]
    identity: RepositoryIdentityResolution
    evidence: tuple[str, ...]
    profile_versions: tuple[tuple[str, str], ...]
    activation_digest: str
    schema_version: str = PROFILE_SCHEMA_VERSION


def _contains_python_source(root: Path, *, max_entries: int = 50_000) -> tuple[bool, str]:
    seen = 0
    for directory, names, files in os.walk(root, followlinks=False):
        names[:] = sorted(
            name for name in names if name not in {".git", ".venv", "node_modules", "__pycache__"}
        )
        for name in sorted(files):
            seen += 1
            if seen > max_entries:
                return False, f"python_scan_truncated:{max_entries}"
            if name.endswith((".py", ".pyi")):
                relative = (Path(directory) / name).relative_to(root).as_posix()
                return True, f"python_source:{relative}"
    return False, "python_source_absent"


def activate_repository_profiles(
    root: str | Path,
    *,
    identity: RepositoryIdentityResolution | None = None,
) -> RepositoryProfileActivation:
    """Detect generic Python and exact AutoSkillit overlay applicability."""
    resolved_root = Path(root).resolve()
    resolved_identity = identity or resolve_repository_identity(resolved_root)
    python_semantics, python_evidence = _contains_python_source(resolved_root)
    evidence = tuple(
        f"{item.source}:{item.value}:{str(item.accepted).lower()}:{item.diagnostic}"
        for item in resolved_identity.evidence
    ) + (python_evidence,)
    activations = (
        ProfileActivation(
            RepositoryProfileId.LANGUAGE_NEUTRAL,
            ExplorationApplicability.APPLICABLE,
            "language-neutral repository/artifact core is always available",
        ),
        ProfileActivation(
            RepositoryProfileId.GENERIC_PYTHON,
            (
                ExplorationApplicability.APPLICABLE
                if python_semantics
                else ExplorationApplicability.NOT_APPLICABLE
            ),
            python_evidence,
        ),
        ProfileActivation(
            RepositoryProfileId.AUTOSKILLIT,
            (
                ExplorationApplicability.APPLICABLE
                if resolved_identity.autoskillit_overlay
                else ExplorationApplicability.NOT_APPLICABLE
            ),
            (
                f"{resolved_identity.source}:exact_autoskillit_identity"
                if resolved_identity.autoskillit_overlay
                else f"generic_fallback:{resolved_identity.source}"
            ),
        ),
    )
    profile_versions = (
        (RepositoryProfileId.LANGUAGE_NEUTRAL.value, "1"),
        (RepositoryProfileId.GENERIC_PYTHON.value, "1"),
        (RepositoryProfileId.AUTOSKILLIT.value, "1"),
    )
    activation_payload = json.dumps(
        {
            "activations": [
                [item.profile.value, item.applicability.value, item.reason] for item in activations
            ],
            "evidence": evidence,
            "identity": resolved_identity.repository_identity.digest,
            "profile_versions": profile_versions,
            "schema_version": PROFILE_SCHEMA_VERSION,
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")
    activation_hex = hashlib.sha256(
        PROFILE_ACTIVATION_DIGEST_DOMAIN + activation_payload
    ).hexdigest()
    activation_digest = f"sha256:{activation_hex}"
    return RepositoryProfileActivation(
        activations=activations,
        identity=resolved_identity,
        evidence=evidence,
        profile_versions=profile_versions,
        activation_digest=activation_digest,
    )
