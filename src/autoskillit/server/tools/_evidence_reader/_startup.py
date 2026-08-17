"""Reader declarations plus startup validation, receipts, and revocation."""

from __future__ import annotations

import os
import shutil
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from autoskillit.core import (
    EVIDENCE_READER_AUTHORITY_PATH_ENV_VAR,
    EVIDENCE_READER_TOOLS,
    canonical_reader_tools_to_bare,
)

if TYPE_CHECKING:
    from autoskillit.pipeline import ToolContext


class EvidenceReaderError(RuntimeError):
    """A fail-closed evidence-reader authority rejection."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class EvidenceReaderLimits:
    max_calls: int = 32
    max_pages: int = 32
    max_output_bytes: int = 1_000_000
    max_page_bytes: int = 64_000
    max_page_lines: int = 1_000
    max_receipts: int = 64

    def __post_init__(self) -> None:
        values = (
            self.max_calls,
            self.max_pages,
            self.max_output_bytes,
            self.max_page_bytes,
            self.max_page_lines,
            self.max_receipts,
        )
        maximums = (32, 32, 1_000_000, 64_000, 1_000, 64)
        if any(
            not isinstance(value, int) or isinstance(value, bool) or value <= 0 for value in values
        ) or any(value > maximum for value, maximum in zip(values, maximums, strict=True)):
            raise ValueError("evidence reader limits must be positive integers within maxima")


@dataclass(frozen=True, slots=True)
class EvidenceReaderInvocation:
    invocation_dir: Path
    environment: tuple[tuple[str, str], ...]
    expires_at: float


@dataclass(frozen=True, slots=True)
class EvidenceReaderPage:
    content: str
    citation_id: str
    continuation: str | None
    byte_start: int
    byte_end: int
    line_start: int
    line_end: int
    snapshot_digest: str


@dataclass(frozen=True, slots=True)
class EvidenceReaderReceipt:
    sequence: int
    outcome: str
    citation_id: str
    byte_start: int
    byte_end: int
    recorded_at: float


def validate_evidence_reader_startup(
    tool_ctx: ToolContext,
    environment: Mapping[str, str],
) -> None:
    """Reopen and authenticate one complete reader authority before visibility."""

    from autoskillit.server.tools._evidence_reader._authority import (  # circular-break
        _open_authority,
    )
    from autoskillit.server.tools._evidence_reader._invocation import (  # circular-break
        _receipt_state,
    )

    opened = _open_authority(tool_ctx, environment)
    authority = opened.authority
    if time.time() >= authority["expires_at"]:
        raise EvidenceReaderError("authority_expired")
    canonical_tools = authority.get("canonical_tools")
    bare_tools = authority.get("bare_tools")
    if (
        not isinstance(canonical_tools, list)
        or not canonical_tools
        or not isinstance(bare_tools, list)
        or frozenset(bare_tools) != EVIDENCE_READER_TOOLS
        or len(bare_tools) != len(EVIDENCE_READER_TOOLS)
    ):
        raise EvidenceReaderError("tool_not_authorized")
    try:
        expected_bare = canonical_reader_tools_to_bare(tuple(canonical_tools))
    except (TypeError, ValueError) as exc:
        raise EvidenceReaderError("tool_not_authorized") from exc
    if tuple(bare_tools) != expected_bare:
        raise EvidenceReaderError("tool_not_authorized")
    _receipt_state(opened)


def load_evidence_reader_receipts(
    tool_ctx: ToolContext,
    environment: Mapping[str, str],
    *,
    max_receipts: int = 64,
) -> tuple[EvidenceReaderReceipt, ...]:
    """Load a bounded suffix of verified receipts for one invocation."""

    from autoskillit.server.tools._evidence_reader._authority import (  # circular-break
        _open_authority,
    )
    from autoskillit.server.tools._evidence_reader._invocation import (  # circular-break
        _receipt_state,
    )

    if (
        not isinstance(max_receipts, int)
        or isinstance(max_receipts, bool)
        or not 1 <= max_receipts <= 64
    ):
        raise EvidenceReaderError("receipt_limit_invalid")
    opened = _open_authority(tool_ctx, environment)
    state = _receipt_state(opened)
    raw_receipts = state.get("receipts")
    if not isinstance(raw_receipts, list):
        raise EvidenceReaderError("authority_tampered")
    try:
        receipts = tuple(EvidenceReaderReceipt(**item) for item in raw_receipts[-max_receipts:])
    except (TypeError, ValueError) as exc:
        raise EvidenceReaderError("authority_tampered") from exc
    return receipts


def revoke_evidence_reader_invocation(
    tool_ctx: ToolContext,
    environment: Mapping[str, str],
) -> None:
    """Synchronously revoke one verified authority and prove its directory absent."""

    from autoskillit.server.tools._evidence_reader._authority import (  # circular-break
        _AUTHORITY_FILE,
        _environment,
        _open_authority,
        _verified_readers_root,
    )
    from autoskillit.server.tools._evidence_reader._invocation import (  # circular-break
        _acquire_call_lock,
        _release_call_lock,
    )

    env = _environment(environment)
    authority_path = Path(env[EVIDENCE_READER_AUTHORITY_PATH_ENV_VAR])
    if not authority_path.exists() and not authority_path.is_symlink():
        invocation_dir = authority_path.parent
        root = _verified_readers_root(invocation_dir.parent)
        if (
            not authority_path.is_absolute()
            or authority_path.name != _AUTHORITY_FILE
            or root.name != "evidence-readers"
            or invocation_dir.exists()
            or invocation_dir.is_symlink()
        ):
            raise EvidenceReaderError("authority_path_invalid")
        return
    opened = _open_authority(tool_ctx, env)
    lock_fd, lock_stat = _acquire_call_lock(opened.invocation_dir)
    try:
        shutil.rmtree(opened.invocation_dir)
    except Exception as exc:
        tamper: EvidenceReaderError | None = None
        if opened.invocation_dir.exists():
            tamper = _release_call_lock(opened.invocation_dir, lock_fd, lock_stat)
        else:
            os.close(lock_fd)
        if tamper is not None:
            raise tamper from exc
        raise
    else:
        os.close(lock_fd)
    if opened.invocation_dir.exists() or opened.invocation_dir.is_symlink():
        raise EvidenceReaderError("revocation_failed")
