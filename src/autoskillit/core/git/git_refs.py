"""Qualified Git ref construction and resolution."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

RefCategory = Literal["local_branch", "remote_tracking", "tag", "other"]

_LOCAL_BRANCH_PREFIX = "refs/heads/"
_REMOTE_TRACKING_PREFIX = "refs/remotes/"
_TAG_PREFIX = "refs/tags/"


@dataclass(frozen=True, slots=True)
class ResolvedRef:
    """A fully qualified Git ref resolved to a commit object."""

    category: RefCategory
    qualified_ref: str
    short_name: str
    sha: str


def local_branch_ref(name: str) -> str:
    """Return the fully qualified local-branch ref for *name*."""
    return f"{_LOCAL_BRANCH_PREFIX}{name}"


def remote_tracking_ref(remote: str, name: str) -> str:
    """Return the fully qualified remote-tracking ref for *remote* and *name*."""
    return f"{_REMOTE_TRACKING_PREFIX}{remote}/{name}"


def _categorize_ref(qualified_ref: str) -> RefCategory:
    if qualified_ref.startswith(_LOCAL_BRANCH_PREFIX):
        return "local_branch"
    if qualified_ref.startswith(_REMOTE_TRACKING_PREFIX):
        return "remote_tracking"
    if qualified_ref.startswith(_TAG_PREFIX):
        return "tag"
    return "other"


def verify_qualified_ref_sync(
    repo_path: Path, qualified_ref: str, *, timeout: float = 10.0
) -> ResolvedRef | None:
    """Resolve a fully qualified ref to a commit, returning ``None`` on Git failure."""
    if not qualified_ref.startswith("refs/"):
        raise ValueError(f"qualified_ref must start with 'refs/': {qualified_ref!r}")

    try:
        result = subprocess.run(
            [
                "git",
                "rev-parse",
                "--verify",
                "--end-of-options",
                f"{qualified_ref}^{{commit}}",
            ],
            cwd=str(repo_path),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    if result.returncode != 0:
        return None

    category = _categorize_ref(qualified_ref)
    if category == "local_branch":
        short_name = qualified_ref.removeprefix(_LOCAL_BRANCH_PREFIX)
    elif category == "remote_tracking":
        short_name = qualified_ref.removeprefix(_REMOTE_TRACKING_PREFIX)
    elif category == "tag":
        short_name = qualified_ref.removeprefix(_TAG_PREFIX)
    else:
        short_name = qualified_ref.removeprefix("refs/")
    return ResolvedRef(
        category=category,
        qualified_ref=qualified_ref,
        short_name=short_name,
        sha=result.stdout.strip(),
    )


__all__ = [
    "ResolvedRef",
    "local_branch_ref",
    "remote_tracking_ref",
    "verify_qualified_ref_sync",
]
