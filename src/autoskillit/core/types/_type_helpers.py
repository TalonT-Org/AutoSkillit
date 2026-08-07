"""Core skill name resolution and text-processing helpers.

Zero autoskillit imports outside this sub-package. Provides extract_skill_name,
extract_path_arg, resolve_target_skill, truncate_text, fleet_error, and session_type.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import warnings
from collections.abc import Callable
from datetime import date
from pathlib import Path
from types import UnionType
from typing import Any, Never, Union, assert_never, get_args, get_origin

from ._type_backend import BackendConventions
from ._type_constants import SKILL_COMMAND_PREFIX
from ._type_constants_env import HEADLESS_ENV_VAR, SESSION_TYPE_ENV_VAR
from ._type_constants_registries import FLEET_ERROR_CODES
from ._type_enums import SessionType, SkillSource, WitnessKind
from ._type_protocols_workspace import SkillResolver
from ._type_skill_contract import SkillSourceRef

__all__ = [
    "is_path_like_token",
    "extract_path_arg",
    "extract_positional_args",
    "extract_skill_name",
    "detect_body_marker",
    "fleet_error",
    "render_target_skill_command",
    "resolve_skill_name",
    "resolve_target_skill",
    "session_type",
    "strip_markdown_code_regions",
    "truncate_text",
]

_SKILL_CMD_RE = re.compile(
    r"^/(?:autoskillit:)?([\w-]+)"
)  # anchored: strict leading-slash for extraction
_SKILL_RESOLVE_RE = re.compile(
    r"/(?:autoskillit:)?([\w-]+)"
)  # unanchored: supports "Use /..." prefix forms

_PATH_PREFIXES: tuple[str, ...] = ("/", "./", ".autoskillit/")

CONTEXT_ADMISSION_PROTOCOL_VERSION = 1
_MAX_UINT64 = (1 << 64) - 1
_CONTENT_FREE_TEXT = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:@+-]*\Z")
_CONTENT_FREE_LOCATOR = re.compile(r"[A-Za-z0-9][A-Za-z0-9_./:@+-]*\Z")
_REASON_CODE = re.compile(r"[a-z][a-z0-9-]{0,63}\Z")
_GIT_REVISION = re.compile(r"[0-9a-fA-F]{40}\Z")
_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}\Z")
_FRESHNESS_POLICIES = frozenset(
    {
        "verify_on_version_or_configuration_change",
        "verify_on_revision_change",
        "infer_only",
    }
)
_SENSITIVE_TEXT_MARKERS = (
    "authorization",
    "bearer",
    "content:",
    "password",
    "secret",
    "token=",
)


class ContextAdmissionValidationError(ValueError):
    """Raised when a protocol value violates a content-free invariant."""


class UnsupportedContextAdmissionProtocolError(ContextAdmissionValidationError):
    """Raised when a value uses unsupported protocol semantics."""


def _raise_invalid(reason_code: str) -> Never:
    raise ContextAdmissionValidationError(reason_code)


def _validate_protocol_version(protocol_version: int) -> None:
    if protocol_version != CONTEXT_ADMISSION_PROTOCOL_VERSION:
        raise UnsupportedContextAdmissionProtocolError("unsupported_protocol_version")


def _validate_non_negative(value: int, reason_code: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0 or value > _MAX_UINT64:
        _raise_invalid(reason_code)


def _reconciled_snapshot_counts(
    active_count: int,
    remaining_count: int,
    hard_limit: int,
    deducted_charge: int,
    terminal_charge: int,
) -> tuple[int, int]:
    charge_delta = deducted_charge - terminal_charge
    if charge_delta > 0:
        capacity_slack = max(hard_limit - active_count - remaining_count, 0)
        active_credit = min(max(charge_delta - capacity_slack, 0), active_count)
        restored_count = min(charge_delta, capacity_slack + active_credit)
        return active_count - active_credit, remaining_count + restored_count
    additional_charge = min(-charge_delta, remaining_count)
    return active_count + additional_charge, remaining_count - additional_charge


def _validate_bounded_text(
    value: str,
    reason_code: str,
    *,
    maximum: int = 128,
    locator: bool = False,
) -> None:
    if not isinstance(value, str) or not value or len(value) > maximum:
        _raise_invalid(reason_code)
    lowered = value.casefold()
    pattern = _CONTENT_FREE_LOCATOR if locator else _CONTENT_FREE_TEXT
    if (
        any(marker in lowered for marker in _SENSITIVE_TEXT_MARKERS)
        or lowered.startswith("sha256:")
        or lowered.startswith("blake2:")
        or "\n" in value
        or "\r" in value
        or value.startswith("/")
        or "\\" in value
        or value.startswith("~")
        or not pattern.fullmatch(value)
        or (locator and ".." in value.split("/"))
    ):
        _raise_invalid(reason_code)


def _validate_reason_code(
    value: str,
    validation_error: str = "invalid_reason_code",
) -> None:
    _validate_bounded_text(value, validation_error, maximum=64)
    if not _REASON_CODE.fullmatch(value):
        _raise_invalid(validation_error)


def _validate_iso_date(value: str) -> None:
    if not isinstance(value, str) or not _ISO_DATE.fullmatch(value):
        _raise_invalid("invalid_checked_at")
    try:
        date.fromisoformat(value)
    except ValueError:
        raise ContextAdmissionValidationError("invalid_checked_at") from None


def _validate_tuple(value: object, reason_code: str) -> None:
    if not isinstance(value, tuple):
        _raise_invalid(reason_code)


def _validate_canonical_tuple(
    value: tuple[Any, ...],
    reason_code: str,
    *,
    key: Callable[[Any], Any],
) -> None:
    _validate_tuple(value, reason_code)
    if value != tuple(sorted(value, key=key)):
        _raise_invalid(reason_code)


def _validate_git_revision(value: str) -> None:
    if not isinstance(value, str) or not _GIT_REVISION.fullmatch(value):
        _raise_invalid("invalid_tested_revision")


def _validate_freshness_policy(value: str) -> None:
    if not isinstance(value, str) or value not in _FRESHNESS_POLICIES:
        _raise_invalid("invalid_freshness_policy")
    if value != "verify_on_version_or_configuration_change":
        _raise_invalid("unsupported_coverage_freshness_policy")


def _validate_expired_idempotency_tombstone(tombstone: Any) -> None:
    descriptor = tombstone.original_descriptor
    input_reservations = descriptor.input_reservations
    batch = descriptor.batch
    if (
        tombstone.namespace != descriptor.idempotency_namespace
        or tombstone.reservation_key.idempotency_namespace != tombstone.namespace
        or len(input_reservations) != 1
        or tombstone.reservation_key != input_reservations[0].key
        or tombstone.reservation_key.batch_id != batch.batch_id
        or tombstone.original_terminal_decision.window_epoch_id
        != tombstone.reservation_key.window_epoch_id
        or tombstone.original_terminal_decision.snapshot_sequence != descriptor.snapshot_sequence
    ):
        _raise_invalid("idempotency_tombstone_identity_mismatch")
    witness = tombstone.expiry_witness
    if (
        witness.kind is not WitnessKind.IDEMPOTENCY_EXPIRY
        or witness.window_epoch_id != tombstone.reservation_key.window_epoch_id
        or witness.window_epoch_number != tombstone.reservation_key.window_epoch_number
        or witness.snapshot_sequence != descriptor.snapshot_sequence
        or witness.request_id != batch.request_id
        or witness.batch_id != batch.batch_id
        or witness.representation_revision != batch.manifest.representation_revision
        or witness.representation_binding_id != batch.manifest.representation_binding_id
        or witness.occurrence_ids != batch.occurrence_ids
    ):
        _raise_invalid("idempotency_tombstone_witness_mismatch")


def _validate_context_admission_state_metadata(
    aggregate_revision: Any,
    admission_sequence: Any,
    processed_events: tuple[Any, ...],
    idempotency_records: tuple[Any, ...],
    expired_tombstones: tuple[Any, ...],
    closed_epochs: tuple[Any, ...],
) -> None:
    if len({record.event_id for record in processed_events}) != len(processed_events):
        _raise_invalid("duplicate_processed_event")
    processed_revisions = tuple(record.aggregate_revision.value for record in processed_events)
    processed_sequences = tuple(record.admission_sequence.value for record in processed_events)
    if (
        any(revision > aggregate_revision.value for revision in processed_revisions)
        or any(sequence > admission_sequence.value for sequence in processed_sequences)
        or any(
            later < earlier for earlier, later in zip(processed_sequences, processed_sequences[1:])
        )
    ):
        _raise_invalid("invalid_processed_event_coordinates")
    idempotency_keys = tuple(
        (record.namespace, record.reservation_key) for record in idempotency_records
    )
    if len(set(idempotency_keys)) != len(idempotency_keys):
        _raise_invalid("duplicate_idempotency_owner")
    processed_by_event_id = {record.event_id: record for record in processed_events}
    if any(
        record.publication_revision.value > aggregate_revision.value
        or (processed := processed_by_event_id.get(record.owning_event_id)) is None
        or record.publication_revision != processed.aggregate_revision
        for record in idempotency_records
    ):
        _raise_invalid("invalid_idempotency_publication_coordinates")
    tombstone_keys = tuple(
        (record.namespace, record.reservation_key) for record in expired_tombstones
    )
    if len(set(tombstone_keys)) != len(tombstone_keys):
        _raise_invalid("duplicate_idempotency_tombstone")
    epoch_keys = tuple(
        (audit.snapshot.window_epoch_id, audit.snapshot.window_epoch_number)
        for audit in closed_epochs
    )
    if len(set(epoch_keys)) != len(epoch_keys):
        _raise_invalid("duplicate_closed_epoch")


def _matches_declared_type(value: object, declared_type: object) -> bool:
    if declared_type is Any:
        return True
    origin = get_origin(declared_type)
    if origin in {Union, UnionType}:
        return any(_matches_declared_type(value, member) for member in get_args(declared_type))
    if origin is tuple:
        if type(value) is not tuple:
            return False
        members = get_args(declared_type)
        if len(members) == 2 and members[1] is Ellipsis:
            return all(_matches_declared_type(item, members[0]) for item in value)
        return len(value) == len(members) and all(
            _matches_declared_type(item, member)
            for item, member in zip(value, members, strict=True)
        )
    if origin is frozenset:
        if type(value) is not frozenset:
            return False
        (member_type,) = get_args(declared_type)
        return all(_matches_declared_type(item, member_type) for item in value)
    if declared_type is None or declared_type is type(None):
        return value is None
    if isinstance(declared_type, type):
        return type(value) is declared_type
    return False


def is_path_like_token(token: str) -> bool:
    return any(token.startswith(p) for p in _PATH_PREFIXES)


def extract_positional_args(skill_command: str) -> list[str]:
    """Extract all positional tokens from a skill_command string.

    Returns tokens after the skill name, tokenized with ``shlex.split`` so
    quoted arguments (including those containing whitespace or newlines)
    remain one logical argument. Unmatched quotes raise ``ValueError``.

    Path-like and non-path tokens are both included, preserving positional
    order.
    """
    stripped = skill_command.strip()
    m = _SKILL_CMD_RE.match(stripped)
    if m is None:
        return []
    remainder = stripped[m.end() :]
    if not remainder:
        return []
    return shlex.split(remainder)


def extract_path_arg(skill_command: str) -> str | None:
    """Extract the first path-like positional argument from a skill_command string.

    Tolerates trailing text (markdown headers, extra tokens, embedded newlines)
    after the path. Returns None if no path-like token is found.
    Strips enclosing quotes from the returned path token.
    """
    stripped = skill_command.strip()
    m = _SKILL_CMD_RE.match(stripped)
    if m is None:
        return None
    tokens = stripped[m.end() :].split()
    for token in tokens:
        cleaned = token.strip('"').strip("'")
        if is_path_like_token(cleaned):
            return cleaned
    return None


def extract_skill_name(skill_command: str) -> str | None:
    """Extract the bare skill name from a skill_command string.

    Handles both ``/autoskillit:make-plan ...`` and ``/make-plan ...`` forms.
    Returns None if the command is not a slash-command.
    """
    m = _SKILL_CMD_RE.match(skill_command.strip())
    return m.group(1) if m else None


def resolve_skill_name(skill_command: str) -> str | None:
    """Extract and validate skill name from command string.

    Handles both ``/name`` and ``/autoskillit:name`` forms. Returns None if
    no match, name contains template expressions, or is followed by a
    bash-style ``{placeholder}`` token.
    """
    stripped = skill_command.strip()
    match = _SKILL_RESOLVE_RE.search(stripped)
    if not match:
        return None
    name = match.group(1)
    if "${{" in name:
        return None
    remainder = stripped[match.end() :]
    if remainder.startswith("{") or remainder.startswith("${{"):
        return None
    return name


def resolve_target_skill(
    skill_command: str,
    resolver: SkillResolver,
    project_root: Path | None,
) -> tuple[str, str | None]:
    """Resolve a skill_command to the correct invocation namespace.

    Returns (resolved_command, skill_name).
    skill_name is None if skill_command is not a slash command.

    - Skills in ``skills/`` (BUNDLED) → ``/autoskillit:name`` namespace
    - Skills in ``skills_extended/`` (BUNDLED_EXTENDED) → ``/name`` namespace
    """
    name = extract_skill_name(skill_command)
    if name is None:
        return skill_command, None

    info = resolver.resolve_effective(name, project_root)
    if info is None or info.invalidities:
        return skill_command, name

    return render_target_skill_command(
        skill_command,
        info.source_ref or info.source,
    ), name


def render_target_skill_command(
    skill_command: str,
    source_ref: SkillSourceRef | SkillSource,
    conventions: BackendConventions | None = None,
) -> str:
    """Render a logical target from its effective source and backend conventions."""
    name = extract_skill_name(skill_command)
    if name is None:
        return skill_command

    source = source_ref.origin if isinstance(source_ref, SkillSourceRef) else source_ref
    configured_sigil = conventions.skill_sigil if conventions is not None else SKILL_COMMAND_PREFIX
    sigil = (
        configured_sigil
        if isinstance(configured_sigil, str) and configured_sigil
        else SKILL_COMMAND_PREFIX
    )
    match source:
        case SkillSource.BUNDLED:
            namespace = "autoskillit:" if sigil == SKILL_COMMAND_PREFIX else ""
        case SkillSource.BUNDLED_EXTENDED | SkillSource.PROJECT_LOCAL | SkillSource.THIRD_PARTY:
            namespace = ""
        case _ as unreachable:
            assert_never(unreachable)
    correct_prefix = f"{sigil}{namespace}{name}"

    # Reconstruct: replace the skill reference, preserve trailing arguments
    stripped = skill_command.strip()
    m = _SKILL_CMD_RE.match(stripped)
    if m is None:
        raise RuntimeError(f"regex failed after extract_skill_name succeeded: {stripped!r}")
    remainder = stripped[m.end() :]
    return correct_prefix + remainder


def truncate_text(text: str, max_len: int = 5000) -> str:
    """Truncate text to max_len, appending a count of truncated chars."""
    if len(text) <= max_len:
        return text
    return f"...[truncated {len(text) - max_len} chars]...\n" + text[-max_len:]


_CODE_BLOCK_RE = re.compile(r"(```|~~~).*?\1", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`[^`\n]*`")


def strip_markdown_code_regions(text: str) -> str:
    """Remove fenced code blocks and inline code spans from markdown text."""
    while True:
        stripped = _CODE_BLOCK_RE.sub("", text)
        stripped = _INLINE_CODE_RE.sub("", stripped)
        if stripped == text:
            return stripped
        text = stripped


def detect_body_marker(body: str, marker: str) -> bool:
    """Check whether *marker* appears in *body* outside markdown code regions."""
    return marker in strip_markdown_code_regions(body)


def fleet_error(
    code: str,
    message: str,
    *,
    details: dict[str, Any] | None = None,
) -> str:
    """Return canonical JSON error envelope for fleet dispatch failures.

    Validates that code is a registered FleetErrorCode. Raises ValueError
    for unregistered codes. The details dict must be JSON-serializable.
    """
    if code not in FLEET_ERROR_CODES:
        msg = f"Unregistered fleet error code: {code!r}"
        raise ValueError(msg)
    return json.dumps(
        {
            "success": False,
            "error": str(code),
            "user_visible_message": message,
            "details": details,
        }
    )


def session_type() -> SessionType:
    """Resolve current session type from AUTOSKILLIT_SESSION_TYPE env var.

    Raises ValueError for the removed 'leaf' alias.
    Fail-closed: returns SKILL on unset or invalid values.
    Transitional bridge: HEADLESS=1 without SESSION_TYPE emits DeprecationWarning.
    """
    raw = os.environ.get(SESSION_TYPE_ENV_VAR, "")
    if raw:
        raw_lower = raw.lower()
        if raw_lower == "leaf":
            raise ValueError(
                "AUTOSKILLIT_SESSION_TYPE='leaf' has been removed. Use 'skill' instead."
            )
        try:
            return SessionType(raw_lower)
        except ValueError:
            valid = ", ".join(m.value for m in SessionType)
            raise ValueError(
                f"AUTOSKILLIT_SESSION_TYPE={raw!r} is not a valid SessionType. "
                f"Valid values: {valid}. "
                f"CLI display labels ('cook', 'order') must not be used here."
            ) from None
    if os.environ.get(HEADLESS_ENV_VAR) == "1":
        warnings.warn(
            f"{HEADLESS_ENV_VAR}=1 without {SESSION_TYPE_ENV_VAR} set. "
            "Defaulting to SKILL. Set AUTOSKILLIT_SESSION_TYPE explicitly.",
            DeprecationWarning,
            stacklevel=2,
        )
    return SessionType.SKILL
