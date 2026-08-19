"""Call-binding validation, scope digests, and bounded page reads."""

from __future__ import annotations

import math
import secrets
import sys
import time
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Final, cast

from autoskillit.exploration import qualified_digest
from autoskillit.server.tools._evidence_reader._authority import (
    _capability_hash,
    _limits_from_authority,
    _open_authority,
    _snapshot_content,
)
from autoskillit.server.tools._evidence_reader._invocation import (
    _acquire_call_lock,
    _receipt_state,
    _release_call_lock,
    _write_receipt_state,
)
from autoskillit.server.tools._evidence_reader._startup import (
    EvidenceReaderError,
    EvidenceReaderPage,
)

if TYPE_CHECKING:
    from autoskillit.pipeline import ToolContext

_SCOPE_DOMAIN: Final = b"autoskillit.evidence-reader-scope.v1\0"
_CITATION_DOMAIN: Final = b"autoskillit.evidence-reader-citation.v1\0"


def _validate_call_binding(
    authority: Mapping[str, Any],
    *,
    caller_session_id: str,
    role: str,
    role_definition_digest: str,
    canonical_tool: str,
    bare_tool: str,
    policy: str,
    deadline: float,
) -> None:
    if not isinstance(deadline, (int, float)) or not math.isfinite(deadline):
        raise EvidenceReaderError("deadline_invalid")
    if time.monotonic() >= deadline:
        raise EvidenceReaderError("deadline_exceeded")
    if time.time() >= authority.get("expires_at", 0):
        raise EvidenceReaderError("authority_expired")
    bindings = (
        ("caller_session_id", caller_session_id),
        ("role", role),
        ("role_definition_digest", role_definition_digest),
        ("policy", policy),
    )
    if any(authority.get(name) != value for name, value in bindings):
        raise EvidenceReaderError("scope_mismatch")
    tools = authority.get("canonical_tools")
    bare_tools = authority.get("bare_tools")
    if (
        not isinstance(tools, list)
        or not isinstance(bare_tools, list)
        or canonical_tool not in tools
        or bare_tool not in bare_tools
        or tools.index(canonical_tool) != bare_tools.index(bare_tool)
    ):
        raise EvidenceReaderError("tool_not_authorized")


def _scope_digest(authority: Mapping[str, Any]) -> str:
    return qualified_digest(
        _SCOPE_DOMAIN,
        {
            "invocation_id": authority["invocation_id"],
            "caller_session_id": authority["caller_session_id"],
            "role": authority["role"],
            "role_definition_digest": authority["role_definition_digest"],
            "readers_root": authority["readers_root"],
            "repository_root": authority["repository_root"],
            "repository_identity_digest": authority["repository_identity_digest"],
            "revision": authority["revision"],
            "artifact_path": authority["artifact_path"],
            "snapshot_digest": authority["snapshot_digest"],
            "content_digest": authority["content_digest"],
            "size": authority["size"],
            "mode": authority["mode"],
            "index_records": authority["index_records"],
            "canonical_tools": authority["canonical_tools"],
            "bare_tools": authority["bare_tools"],
            "policy": authority["policy"],
            "limits": authority["limits"],
            "expires_at": authority["expires_at"],
        },
    )


def evidence_reader_scope_digest(
    tool_ctx: ToolContext,
    environment: Mapping[str, str],
) -> str:
    """Return the verified invocation-wide scope expected from the child."""

    return _scope_digest(_open_authority(tool_ctx, environment).authority)


def _page_end(content: bytes, offset: int, byte_limit: int, line_limit: int) -> int:
    end = min(len(content), offset + byte_limit)
    while end > offset:
        try:
            content[offset:end].decode("utf-8", errors="strict")
            break
        except UnicodeDecodeError:
            end -= 1
    if end == offset and offset < len(content):
        raise EvidenceReaderError("page_size_too_small")
    segment = content[offset:end]
    newline_positions = [index for index, value in enumerate(segment) if value == 10]
    if len(newline_positions) >= line_limit:
        line_end = newline_positions[line_limit - 1] + 1
        if line_end < len(segment):
            end = offset + line_end
    return end


def read_evidence_reader_page(
    tool_ctx: ToolContext,
    environment: Mapping[str, str],
    *,
    caller_session_id: str,
    role: str,
    role_definition_digest: str,
    canonical_tool: str,
    bare_tool: str,
    policy: str,
    page_size: int,
    continuation: str | None,
    deadline: float,
) -> EvidenceReaderPage:
    """Serve one bounded immutable page after reopening all disk authority."""

    opened = _open_authority(tool_ctx, environment)
    authority = opened.authority
    _validate_call_binding(
        authority,
        caller_session_id=caller_session_id,
        role=role,
        role_definition_digest=role_definition_digest,
        canonical_tool=canonical_tool,
        bare_tool=bare_tool,
        policy=policy,
        deadline=deadline,
    )
    limits = _limits_from_authority(authority)
    if (
        not isinstance(page_size, int)
        or isinstance(page_size, bool)
        or not 1 <= page_size <= limits.max_page_bytes
    ):
        raise EvidenceReaderError("page_size_invalid")
    lock_fd, lock_stat = _acquire_call_lock(opened.invocation_dir)
    try:
        state = _receipt_state(opened)
        if state.get("calls", limits.max_calls) >= limits.max_calls:
            raise EvidenceReaderError("call_budget_exhausted")
        if state.get("pages", limits.max_pages) >= limits.max_pages:
            raise EvidenceReaderError("page_budget_exhausted")
        scope_digest = _scope_digest(authority)
        if continuation is None:
            offset = 0
        else:
            if not isinstance(continuation, str) or not continuation:
                raise EvidenceReaderError("continuation_invalid")
            continuation_hash = _capability_hash(continuation)
            cursor = state.get("continuations", {}).pop(continuation_hash, None)
            expected = {
                "capability_hash": opened.capability_hash,
                "scope_digest": scope_digest,
                "snapshot_digest": authority["snapshot_digest"],
                "page_size": page_size,
            }
            if not isinstance(cursor, dict) or any(
                cursor.get(key) != value for key, value in expected.items()
            ):
                raise EvidenceReaderError("continuation_invalid")
            raw_offset = cursor.get("offset")
            if not isinstance(raw_offset, int) or isinstance(raw_offset, bool) or raw_offset < 0:
                raise EvidenceReaderError("continuation_invalid")
            offset = raw_offset
        content = _snapshot_content(opened.invocation_dir, authority)
        if offset > len(content):
            raise EvidenceReaderError("continuation_invalid")
        end = _page_end(content, offset, page_size, limits.max_page_lines)
        page_bytes = content[offset:end]
        if (
            state.get("output_bytes", limits.max_output_bytes) + len(page_bytes)
            > limits.max_output_bytes
        ):
            raise EvidenceReaderError("output_budget_exhausted")
        citation_id = qualified_digest(
            _CITATION_DOMAIN,
            {
                "invocation_id": authority["invocation_id"],
                "snapshot_digest": authority["snapshot_digest"],
                "byte_start": offset,
                "byte_end": end,
            },
        )
        next_token: str | None = None
        if end < len(content):
            next_token = secrets.token_urlsafe(32)
            state["continuations"][_capability_hash(next_token)] = {
                "capability_hash": opened.capability_hash,
                "scope_digest": scope_digest,
                "snapshot_digest": authority["snapshot_digest"],
                "offset": end,
                "page_size": page_size,
            }
        line_start = content[:offset].count(b"\n") + 1
        newline_count = page_bytes.count(b"\n")
        line_end = line_start + newline_count - int(page_bytes.endswith(b"\n"))
        line_end = max(line_start, line_end)
        state["calls"] += 1
        state["pages"] += 1
        state["output_bytes"] += len(page_bytes)
        receipt = {
            "sequence": state["calls"],
            "outcome": "complete",
            "citation_id": citation_id,
            "byte_start": offset,
            "byte_end": end,
            "recorded_at": time.time(),
        }
        state["receipts"].append(receipt)
        state["receipts"] = state["receipts"][-limits.max_receipts :]
        if time.monotonic() >= deadline:
            raise EvidenceReaderError("deadline_exceeded")
        _write_receipt_state(opened, state)
        return EvidenceReaderPage(
            content=page_bytes.decode("utf-8", errors="strict"),
            citation_id=citation_id,
            continuation=next_token,
            byte_start=offset,
            byte_end=end,
            line_start=line_start,
            line_end=line_end,
            snapshot_digest=authority["snapshot_digest"],
        )
    finally:
        tamper = _release_call_lock(opened.invocation_dir, lock_fd, lock_stat)
        if tamper is not None and sys.exc_info()[1] is None:
            raise tamper


def read_bound_evidence_reader_page(
    tool_ctx: ToolContext,
    environment: Mapping[str, str],
    *,
    canonical_tool: str,
    page_size: int,
    continuation: str | None,
    deadline: float,
) -> EvidenceReaderPage:
    """Read a page using bindings recovered from verified disk authority."""

    opened = _open_authority(tool_ctx, environment)
    authority = opened.authority
    canonical_tools = authority.get("canonical_tools")
    bare_tools = authority.get("bare_tools")
    if not isinstance(canonical_tools, list) or not isinstance(bare_tools, list):
        raise EvidenceReaderError("authority_tampered")
    try:
        tool_index = canonical_tools.index(canonical_tool)
        bare_tool = bare_tools[tool_index]
    except (ValueError, IndexError) as exc:
        raise EvidenceReaderError("tool_not_authorized") from exc
    raw_bindings = {
        name: authority.get(name)
        for name in (
            "caller_session_id",
            "role",
            "role_definition_digest",
            "policy",
        )
    }
    if not isinstance(bare_tool, str) or any(
        not isinstance(value, str) or not value for value in raw_bindings.values()
    ):
        raise EvidenceReaderError("authority_tampered")
    bindings = cast(dict[str, str], raw_bindings)
    return read_evidence_reader_page(
        tool_ctx,
        environment,
        caller_session_id=bindings["caller_session_id"],
        role=bindings["role"],
        role_definition_digest=bindings["role_definition_digest"],
        canonical_tool=canonical_tool,
        bare_tool=bare_tool,
        policy=bindings["policy"],
        page_size=page_size,
        continuation=continuation,
        deadline=deadline,
    )
