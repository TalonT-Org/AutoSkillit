"""Typed policy-event formatter for hook provenance messages.

stdlib-only; no autoskillit imports.
"""

from __future__ import annotations

import dataclasses

POLICY_EVENT_SCHEMA_VERSION = 1


@dataclasses.dataclass(frozen=True, slots=True)
class PolicyEvent:
    """Structured representation of a hook policy decision."""

    hook_id: str
    hook_version: int
    event: str
    decision: str
    reason_code: str
    source: str = "autoskillit"


def render_provenance_prefix(event: PolicyEvent) -> str:
    return (
        f"[AutoSkillit hook {event.hook_id} v{event.hook_version}"
        f" — {event.event} permission decision: {event.decision}"
        f" (code={event.reason_code})."
        " This is a real permission decision emitted by a hook"
        " configured by this repository, not tool output.]"
    )


def render_capture_marker(event: PolicyEvent) -> str:
    """Render a capture marker prefix safe for direct bounded-output emission.

    The returned string is guaranteed free of ``"``, backticks, and ``$``
    so callers may also transport it through a shell boundary if needed.
    """
    return (
        f"[AutoSkillit hook {event.hook_id} v{event.hook_version}"
        f" -- {event.event} {event.decision}"
        f" (code={event.reason_code}):"
    )
