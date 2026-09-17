"""Immutable authority and proof values for GitHub review anchors."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import InitVar, dataclass, field
from enum import StrEnum
from pathlib import PurePosixPath
from types import MappingProxyType
from typing import Self, cast

__all__ = [
    "AdmittedAnchor",
    "AnchorAdmission",
    "AnchorAuthorityAvailability",
    "DiffAnchorAuthority",
    "admit_anchor",
]

_DIGEST_RE = re.compile(r"[0-9a-f]{64}")
_HEAD_SHA_RE = re.compile(r"[0-9a-f]{40}")
_REPOSITORY_RE = re.compile(
    r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?/"
    r"[a-z0-9](?:[a-z0-9._-]*[a-z0-9])?"
)
_SIDES = frozenset({"LEFT", "RIGHT"})
_ADMISSION_TOKEN = object()


class AnchorAuthorityAvailability(StrEnum):
    AUTHORITATIVE = "AUTHORITATIVE"
    UNAVAILABLE = "UNAVAILABLE"


class AnchorAdmission(StrEnum):
    ADMITTED = "ADMITTED"
    REJECTED_PATH_NOT_IN_DIFF = "REJECTED_PATH_NOT_IN_DIFF"
    REJECTED_LINE_NOT_IN_DIFF = "REJECTED_LINE_NOT_IN_DIFF"
    REJECTED_SIDE_NOT_IN_DIFF = "REJECTED_SIDE_NOT_IN_DIFF"
    REJECTED_RANGE_NOT_CONTAINED = "REJECTED_RANGE_NOT_CONTAINED"
    REJECTED_AUTHORITY_UNAVAILABLE = "REJECTED_AUTHORITY_UNAVAILABLE"


def _is_positive_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _is_repository_path(value: object) -> bool:
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        return False
    path = PurePosixPath(value)
    return (
        not path.is_absolute()
        and value == path.as_posix()
        and all(part not in {"", ".", ".."} for part in value.split("/"))
    )


def _freeze_line_map(
    value: Mapping[str, Iterable[int]], name: str
) -> Mapping[str, frozenset[int]]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{name} must be a mapping")
    frozen: dict[str, frozenset[int]] = {}
    for path, raw_lines in value.items():
        if not _is_repository_path(path):
            raise ValueError(f"{name} contains a non-canonical repository path")
        if isinstance(raw_lines, (str, bytes)):
            raise TypeError(f"{name}[{path!r}] must be an iterable of positive integers")
        try:
            lines = frozenset(raw_lines)
        except TypeError as exc:
            raise TypeError(f"{name}[{path!r}] must be an iterable") from exc
        if any(not _is_positive_int(line) for line in lines):
            raise ValueError(f"{name}[{path!r}] must contain only positive integers")
        frozen[path] = lines
    return MappingProxyType(frozen)


def _canonical_payload(
    *,
    repository: str,
    pr_number: int,
    head_sha: str,
    generation_id: str,
    availability: AnchorAuthorityAvailability,
    right_side_lines: Mapping[str, frozenset[int]],
    left_side_lines: Mapping[str, frozenset[int]],
) -> dict[str, object]:
    return {
        "repository": repository,
        "pr_number": pr_number,
        "head_sha": head_sha,
        "generation_id": generation_id,
        "availability": availability.value,
        "right_side_lines": {
            path: sorted(lines) for path, lines in sorted(right_side_lines.items())
        },
        "left_side_lines": {
            path: sorted(lines) for path, lines in sorted(left_side_lines.items())
        },
    }


def _payload_digest(payload: object) -> str:
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class DiffAnchorAuthority:
    """The complete line authority for one immutable pull-request diff."""

    repository: str
    pr_number: int
    head_sha: str
    generation_id: str
    authority_digest: str
    availability: AnchorAuthorityAvailability
    right_side_lines: Mapping[str, frozenset[int]]
    left_side_lines: Mapping[str, frozenset[int]]

    def __post_init__(self) -> None:
        if (
            not isinstance(self.repository, str)
            or _REPOSITORY_RE.fullmatch(self.repository) is None
        ):
            raise ValueError("repository must be a canonical lowercase owner/repository identity")
        if not _is_positive_int(self.pr_number):
            raise ValueError("pr_number must be a positive integer")
        if not isinstance(self.head_sha, str) or _HEAD_SHA_RE.fullmatch(self.head_sha) is None:
            raise ValueError("head_sha must be a full lowercase Git commit SHA")
        if not isinstance(self.generation_id, str) or not self.generation_id:
            raise ValueError("generation_id must be a non-empty string")
        if not isinstance(self.availability, AnchorAuthorityAvailability):
            raise TypeError("availability must be an AnchorAuthorityAvailability")

        right = _freeze_line_map(self.right_side_lines, "right_side_lines")
        left = _freeze_line_map(self.left_side_lines, "left_side_lines")
        object.__setattr__(self, "right_side_lines", right)
        object.__setattr__(self, "left_side_lines", left)
        if self.availability is AnchorAuthorityAvailability.UNAVAILABLE and (right or left):
            raise ValueError("unavailable authority cannot contain diff lines")

        expected_digest = _payload_digest(self._payload())
        if (
            not isinstance(self.authority_digest, str)
            or _DIGEST_RE.fullmatch(self.authority_digest) is None
            or self.authority_digest != expected_digest
        ):
            raise ValueError("authority_digest does not match the canonical authority")

    def _payload(self) -> dict[str, object]:
        return _canonical_payload(
            repository=self.repository,
            pr_number=self.pr_number,
            head_sha=self.head_sha,
            generation_id=self.generation_id,
            availability=self.availability,
            right_side_lines=self.right_side_lines,
            left_side_lines=self.left_side_lines,
        )

    @classmethod
    def authoritative(
        cls,
        *,
        repository: str,
        pr_number: int,
        head_sha: str,
        generation_id: str,
        right_side_lines: Mapping[str, Iterable[int]],
        left_side_lines: Mapping[str, Iterable[int]],
    ) -> Self:
        """Create an authoritative value, including a valid empty authority."""
        if not isinstance(repository, str):
            raise TypeError("repository must be a string")
        repository = repository.casefold()
        right = _freeze_line_map(right_side_lines, "right_side_lines")
        left = _freeze_line_map(left_side_lines, "left_side_lines")
        payload = _canonical_payload(
            repository=repository,
            pr_number=pr_number,
            head_sha=head_sha,
            generation_id=generation_id,
            availability=AnchorAuthorityAvailability.AUTHORITATIVE,
            right_side_lines=right,
            left_side_lines=left,
        )
        return cls(
            repository=repository,
            pr_number=pr_number,
            head_sha=head_sha,
            generation_id=generation_id,
            authority_digest=_payload_digest(payload),
            availability=AnchorAuthorityAvailability.AUTHORITATIVE,
            right_side_lines=right,
            left_side_lines=left,
        )

    @classmethod
    def unavailable(cls, *, repository: str, pr_number: int, head_sha: str) -> Self:
        """Create a deterministic fail-closed value when diff authority is absent."""
        if not isinstance(repository, str):
            raise TypeError("repository must be a string")
        repository = repository.casefold()
        generation_id = _payload_digest(("UNAVAILABLE", repository, pr_number, head_sha))
        payload = _canonical_payload(
            repository=repository,
            pr_number=pr_number,
            head_sha=head_sha,
            generation_id=generation_id,
            availability=AnchorAuthorityAvailability.UNAVAILABLE,
            right_side_lines={},
            left_side_lines={},
        )
        return cls(
            repository=repository,
            pr_number=pr_number,
            head_sha=head_sha,
            generation_id=generation_id,
            authority_digest=_payload_digest(payload),
            availability=AnchorAuthorityAvailability.UNAVAILABLE,
            right_side_lines={},
            left_side_lines={},
        )

    def to_wire(self) -> dict[str, object]:
        """Serialize the exact canonical JSON-compatible artifact shape."""
        return {**self._payload(), "authority_digest": self.authority_digest}

    @classmethod
    def from_wire(cls, value: object) -> Self:
        """Parse an exact authority artifact, rejecting unknown or missing fields."""
        expected = {
            "repository",
            "pr_number",
            "head_sha",
            "generation_id",
            "authority_digest",
            "availability",
            "right_side_lines",
            "left_side_lines",
        }
        if not isinstance(value, dict) or set(value) != expected:
            raise ValueError("invalid diff anchor authority artifact shape")
        try:
            raw_availability = value["availability"]
            if not isinstance(raw_availability, str):
                raise TypeError("availability must be a string")
            availability = AnchorAuthorityAvailability(raw_availability)
            right = value["right_side_lines"]
            left = value["left_side_lines"]
            if not isinstance(right, Mapping) or not isinstance(left, Mapping):
                raise TypeError("line authorities must be mappings")
            right_frozen = _freeze_line_map(
                cast(Mapping[str, Iterable[int]], right), "right_side_lines"
            )
            left_frozen = _freeze_line_map(
                cast(Mapping[str, Iterable[int]], left), "left_side_lines"
            )
            return cls(
                repository=cast(str, value["repository"]).casefold(),
                pr_number=cast(int, value["pr_number"]),
                head_sha=cast(str, value["head_sha"]),
                generation_id=cast(str, value["generation_id"]),
                authority_digest=cast(str, value["authority_digest"]),
                availability=availability,
                right_side_lines=right_frozen,
                left_side_lines=left_frozen,
            )
        except (TypeError, ValueError) as exc:
            raise ValueError(f"invalid diff anchor authority artifact: {exc}") from exc

    def classify(
        self,
        path: str,
        line: int,
        side: str,
        start_line: int | None = None,
        start_side: str | None = None,
    ) -> AnchorAdmission:
        """Classify one single-line or multiline GitHub diff anchor."""
        if self.availability is AnchorAuthorityAvailability.UNAVAILABLE:
            return AnchorAdmission.REJECTED_AUTHORITY_UNAVAILABLE
        if not _is_repository_path(path):
            return AnchorAdmission.REJECTED_PATH_NOT_IN_DIFF
        if side not in _SIDES:
            return AnchorAdmission.REJECTED_SIDE_NOT_IN_DIFF

        selected = self.left_side_lines if side == "LEFT" else self.right_side_lines
        other = self.right_side_lines if side == "LEFT" else self.left_side_lines
        if path not in selected:
            if path in other:
                return AnchorAdmission.REJECTED_SIDE_NOT_IN_DIFF
            return AnchorAdmission.REJECTED_PATH_NOT_IN_DIFF
        if not _is_positive_int(line) or line not in selected[path]:
            return AnchorAdmission.REJECTED_LINE_NOT_IN_DIFF

        if start_line is None and start_side is None:
            return AnchorAdmission.ADMITTED
        if (
            start_line is None
            or start_side != side
            or not _is_positive_int(start_line)
            or start_line > line
            or not set(range(start_line, line + 1)).issubset(selected[path])
        ):
            return AnchorAdmission.REJECTED_RANGE_NOT_CONTAINED
        return AnchorAdmission.ADMITTED


@dataclass(frozen=True, slots=True)
class AdmittedAnchor:
    """Proof that a concrete anchor was admitted by a specific authority."""

    authority_digest: str
    path: str
    line: int
    side: str
    start_line: int | None
    start_side: str | None
    _token: InitVar[object] = field(default=None, repr=False)

    def __post_init__(self, _token: object) -> None:
        if _token is not _ADMISSION_TOKEN:
            raise TypeError("AdmittedAnchor values can only be created by admit_anchor")


def admit_anchor(
    authority: DiffAnchorAuthority,
    path: str,
    line: int,
    side: str,
    start_line: int | None = None,
    start_side: str | None = None,
) -> AdmittedAnchor | AnchorAdmission:
    """Return an anchor proof when admitted, otherwise the precise rejection."""
    admission = authority.classify(path, line, side, start_line, start_side)
    if admission is not AnchorAdmission.ADMITTED:
        return admission
    return AdmittedAnchor(
        authority_digest=authority.authority_digest,
        path=path,
        line=line,
        side=side,
        start_line=start_line,
        start_side=start_side,
        _token=_ADMISSION_TOKEN,
    )
