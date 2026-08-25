"""Release identity and channel-specific freshness policy — IL-0."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import assert_never

from packaging.version import Version


class ReleaseChannel(StrEnum):
    """Authority used to decide whether one release is newer than another."""

    RELEASED = "released"
    BRANCH = "branch"
    WORKING_TREE = "working-tree"


class AdvanceVerdict(StrEnum):
    """Result of comparing an observed install with its previous identity."""

    ADVANCED = "advanced"
    UNCHANGED = "unchanged"
    REGRESSED = "regressed"
    DIVERGED_FROM_TARGET = "diverged-from-target"
    NOT_APPLICABLE = "not-applicable"


@dataclass(frozen=True, slots=True)
class ReleaseIdentity:
    """Identity of an install under one release channel's authority."""

    channel: ReleaseChannel
    version: str
    commit: str | None = None
    ref: str | None = None

    def __post_init__(self) -> None:
        if self.channel == ReleaseChannel.BRANCH and (self.commit is None or self.ref is None):
            raise ValueError("branch release identities require both commit and ref")

    def key(self) -> str:
        """Return an opaque identity key that must never be ordered or passed to Version."""
        match self.channel:
            case ReleaseChannel.RELEASED:
                return self.version
            case ReleaseChannel.BRANCH:
                assert self.commit is not None
                assert self.ref is not None
                sanitized_ref = re.sub(r"[^A-Za-z0-9]+", ".", self.ref).strip(".")
                return f"{self.version}+{sanitized_ref or 'branch'}.g{self.commit[:12]}"
            case ReleaseChannel.WORKING_TREE:
                return f"{self.version}+local"
            case unhandled:
                assert_never(unhandled)


def _require_same_channel(
    *identities: ReleaseIdentity | None,
) -> ReleaseChannel:
    channels = {identity.channel for identity in identities if identity is not None}
    if len(channels) != 1:
        raise ValueError("release identities must use the same channel")
    return next(iter(channels))


def update_available(installed: ReleaseIdentity, target: ReleaseIdentity) -> bool:
    """Return whether *target* is newer under the identities' shared channel."""
    channel = _require_same_channel(installed, target)
    match channel:
        case ReleaseChannel.RELEASED:
            return Version(target.version) > Version(installed.version)
        case ReleaseChannel.BRANCH:
            return target.commit != installed.commit
        case ReleaseChannel.WORKING_TREE:
            return False
        case unhandled:
            assert_never(unhandled)


def advance_verdict(
    *,
    previous: ReleaseIdentity,
    observed: ReleaseIdentity,
    target: ReleaseIdentity | None,
) -> AdvanceVerdict:
    """Judge whether an upgrade advanced according to its release channel."""
    channel = _require_same_channel(previous, observed, target)
    match channel:
        case ReleaseChannel.RELEASED:
            previous_version = Version(previous.version)
            observed_version = Version(observed.version)
            if observed_version > previous_version:
                return AdvanceVerdict.ADVANCED
            if observed_version == previous_version:
                return AdvanceVerdict.UNCHANGED
            return AdvanceVerdict.REGRESSED
        case ReleaseChannel.BRANCH:
            if observed.commit == previous.commit:
                return AdvanceVerdict.UNCHANGED
            if target is None or observed.commit == target.commit:
                return AdvanceVerdict.ADVANCED
            return AdvanceVerdict.DIVERGED_FROM_TARGET
        case ReleaseChannel.WORKING_TREE:
            return AdvanceVerdict.NOT_APPLICABLE
        case unhandled:
            assert_never(unhandled)


def version_advanced(installed: ReleaseIdentity, target: ReleaseIdentity) -> bool:
    """Return the presentation-level PEP 440 version comparison."""
    return Version(target.version) > Version(installed.version)
