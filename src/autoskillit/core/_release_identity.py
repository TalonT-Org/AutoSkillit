"""Release identity and channel-specific freshness policy — IL-0."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import assert_never

from packaging.version import InvalidVersion, Version


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


def _parse_version(raw: str, channel: ReleaseChannel) -> Version:
    """Parse a release-identity version string with channel-aware error context."""
    try:
        return Version(raw)
    except InvalidVersion as err:
        raise ValueError(f"unparseable {channel.value} version: {raw!r}") from err


def _compare_versions(installed_raw: str, target_raw: str, channel: ReleaseChannel) -> bool:
    """Return ``Version(target) > Version(installed)`` under *channel*."""
    return _parse_version(target_raw, channel) > _parse_version(installed_raw, channel)


def update_available(installed: ReleaseIdentity, target: ReleaseIdentity) -> bool:
    """Return whether *target* is newer under the identities' shared channel."""
    channel = _require_same_channel(installed, target)
    match channel:
        case ReleaseChannel.RELEASED:
            return _compare_versions(installed.version, target.version, channel)
        case ReleaseChannel.BRANCH:
            return target.commit != installed.commit
        case ReleaseChannel.WORKING_TREE:
            return _compare_versions(installed.version, target.version, channel)
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
    observed_key: object
    previous_key: object
    target_key: object | None
    match channel:
        case ReleaseChannel.RELEASED:
            previous_version = _parse_version(previous.version, channel)
            observed_version = _parse_version(observed.version, channel)
            if observed_version > previous_version:
                return AdvanceVerdict.ADVANCED
            if observed_version == previous_version:
                return AdvanceVerdict.UNCHANGED
            return AdvanceVerdict.REGRESSED
        case ReleaseChannel.BRANCH:
            observed_key = observed.commit
            previous_key = previous.commit
            target_key = target.commit if target is not None else None
        case ReleaseChannel.WORKING_TREE:
            if target is None:
                return AdvanceVerdict.NOT_APPLICABLE
            observed_key = _parse_version(observed.version, channel)
            previous_key = _parse_version(previous.version, channel)
            target_key = _parse_version(target.version, channel)
        case unhandled:
            assert_never(unhandled)
    if observed_key == previous_key:
        return AdvanceVerdict.UNCHANGED
    if target_key is None or observed_key == target_key:
        return AdvanceVerdict.ADVANCED
    return AdvanceVerdict.DIVERGED_FROM_TARGET


def version_advanced(installed: ReleaseIdentity, target: ReleaseIdentity) -> bool:
    """Return the presentation-level PEP 440 version comparison."""
    return Version(target.version) > Version(installed.version)
