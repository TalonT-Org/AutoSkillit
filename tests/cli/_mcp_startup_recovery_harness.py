"""In-memory adapter for deterministic MCP startup-recovery traces."""

from __future__ import annotations

from dataclasses import dataclass, field

from autoskillit.cli import _prompts


@dataclass(slots=True)
class McpStartupRecoveryHarness:
    """Drive the production reducer without copying prompt policy."""

    spec: _prompts._McpStartupRecoverySpec = _prompts._MCP_STARTUP_RECOVERY_SPEC
    events: list[_prompts.McpStartupRecoveryEvent] = field(default_factory=list)
    dispatch_attempts: int = 0
    terminal: bool = False
    user_visible_events: list[str] = field(default_factory=list)

    def pre_dispatch_failure(self) -> _prompts.McpStartupRecoveryEvent:
        if self.terminal:
            raise RuntimeError("startup recovery already reached a terminal state")
        self.dispatch_attempts += 1
        event = self.spec.reduce_pre_dispatch_failure(self.dispatch_attempts)
        self.events.append(event)
        if event.kind is _prompts.McpStartupRecoveryEventKind.EXHAUSTED:
            self.terminal = True
            assert event.message is not None
            self.user_visible_events.append(event.message)
        return event

    def received_result(
        self,
        kind: _prompts.McpStartupRecoveryEventKind,
    ) -> _prompts.McpStartupRecoveryEvent:
        if self.terminal:
            raise RuntimeError("startup recovery already reached a terminal state")
        self.dispatch_attempts += 1
        event = self.spec.reduce_received_result(kind)
        self.events.append(event)
        self.terminal = True
        return event


def assert_quiet_bounded_trace(harness: McpStartupRecoveryHarness) -> None:
    """Assert the shared silence, cap, and terminal-event invariants."""
    assert harness.dispatch_attempts <= harness.spec.attempt_cap
    assert len(harness.user_visible_events) <= 1
    if harness.user_visible_events:
        assert harness.user_visible_events == [harness.spec.exhaustion_message]
    assert all(
        event.kind
        in {
            _prompts.McpStartupRecoveryEventKind.RETRY,
            _prompts.McpStartupRecoveryEventKind.EXHAUSTED,
            _prompts.McpStartupRecoveryEventKind.TOOL_ERROR_RESULT,
            _prompts.McpStartupRecoveryEventKind.APPLICATION_RESULT,
        }
        for event in harness.events
    )
