"""Tests for the single hook protocol output emitter."""

from __future__ import annotations

import ast
import importlib
import sys
from pathlib import Path
from typing import Any

import pytest

from tests._hook_protocol_oracle import EXPECTED_EFFECT, STATUS_COMPLETED

pytestmark = [pytest.mark.layer("hooks"), pytest.mark.small]

_EMITTER = "autoskillit.hooks._runtime._hook_output"
_EXPECTED_CHANNEL_EVENTS = {
    "deny": frozenset({"PreToolUse"}),
    "block": frozenset(
        {"PreToolUse", "PostToolUse", "PostToolUseFailure", "Stop", "SubagentStop"}
    ),
    "context": frozenset({"SessionStart", "PreToolUse", "PostToolUse", "UserPromptExpansion"}),
    "rewrite_input": frozenset({"PreToolUse"}),
    "rewrite_mcp_output": frozenset({"PostToolUse"}),
    "notify": frozenset({"Stop", "PostToolUse"}),
    "halt": frozenset({"PreCompact"}),
}
_EXPECTED_EMITTER_CHANNELS = {
    "deny_tool_use": "deny",
    "block": "block",
    "add_context": "context",
    "allow_with_updated_input": "rewrite_input",
    "rewrite_mcp_tool_output": "rewrite_mcp_output",
    "notify": "notify",
    "halt_session": "halt",
}
_KNOWN_UNSUPPORTED = frozenset(
    {
        ("codex", "rewrite_mcp_output", "PostToolUse"),
        ("claude", "halt", "PreCompact"),
    }
)


def _emitter() -> Any:
    """Import only when a test executes so this module still collects while red."""
    return importlib.import_module(_EMITTER)


def _render(emitter: Any, channel: str, event: str) -> Any:
    if channel == "deny":
        return emitter.render_deny("permission denied by test")
    if channel == "block":
        return emitter.render_block("blocked by test")
    if channel == "context":
        return emitter.render_context(event, "context from test")
    if channel == "rewrite_input":
        return emitter.render_allow_with_updated_input({"command": "safe"})
    if channel == "rewrite_mcp_output":
        return emitter.render_mcp_tool_output({"formatted": True})
    if channel == "notify":
        kwargs = {"context": "context from test"} if event == "PostToolUse" else {}
        return emitter.render_notify(event, "notice from test", **kwargs)
    if channel == "halt":
        return emitter.render_halt("stop requested by test", system_message="test stop")
    raise AssertionError(f"unknown channel {channel}")


def _verdict(backend: str, event: str, emission: Any) -> Any:
    from tests._hook_protocol_oracle import claude_verdict, codex_verdict

    oracle = codex_verdict if backend == "codex" else claude_verdict
    return oracle(
        event,
        exit_code=emission.exit_code,
        stdout=emission.stdout,
        stderr=emission.stderr,
    )


def test_channel_renderings_have_the_declared_backend_effect() -> None:
    emitter = _emitter()
    from tests._hook_protocol_oracle import (
        CODEX_EVENTS,
        EXPECTED_EFFECT,
        claude_verdict,
        codex_verdict,
    )

    assert emitter.CHANNEL_EVENTS == _EXPECTED_CHANNEL_EVENTS
    assert emitter.EMITTER_CHANNELS == _EXPECTED_EMITTER_CHANNELS

    for channel, events in emitter.CHANNEL_EVENTS.items():
        for event in sorted(events):
            for backend in ("codex", "claude"):
                if backend == "codex" and event not in CODEX_EVENTS:
                    continue
                emission = _render(emitter, channel, event)
                oracle = codex_verdict if backend == "codex" else claude_verdict
                verdict = oracle(
                    event,
                    exit_code=emission.exit_code,
                    stdout=emission.stdout,
                    stderr=emission.stderr,
                )
                unsupported = (backend, channel, event) in _KNOWN_UNSUPPORTED
                if unsupported:
                    assert not EXPECTED_EFFECT[channel](verdict)
                else:
                    assert EXPECTED_EFFECT[channel](verdict), (
                        f"{backend} did not honor channel {channel} on {event}: {verdict}"
                    )


def test_codex_oracle_rejects_events_outside_its_domain() -> None:
    from tests._hook_protocol_oracle import codex_verdict

    with pytest.raises(ValueError, match="event"):
        codex_verdict("UserPromptExpansion", exit_code=0, stdout="", stderr="")


def test_known_unsupported_channels_do_not_achieve_their_effect() -> None:
    emitter = _emitter()
    for backend, channel, event in _KNOWN_UNSUPPORTED:
        emission = _render(emitter, channel, event)
        verdict = _verdict(backend, event, emission)
        assert not EXPECTED_EFFECT[channel](verdict)


def test_render_block_uses_stderr_and_exit_two() -> None:
    emission = _emitter().render_block("blocked by test")

    assert emission.stdout == ""
    assert emission.stderr == "blocked by test\n"
    assert emission.exit_code == 2


@pytest.mark.parametrize("value", ["", "  "])
def test_mcp_replacement_preserves_empty_strings(value: str) -> None:
    emission = _emitter().render_mcp_tool_output(value)
    verdict = _verdict("claude", "PostToolUse", emission)

    assert verdict.status == STATUS_COMPLETED
    assert verdict.mcp_output == value


@pytest.mark.parametrize(
    ("render_name", "args", "kwargs"),
    [
        ("render_deny", (" \t",), {}),
        ("render_block", ("  ",), {}),
        ("render_context", ("Stop", "context"), {}),
        ("render_context", ("PreToolUse", " \n"), {}),
        ("render_allow_with_updated_input", ({},), {}),
        ("render_allow_with_updated_input", ({"command": "safe"},), {"context": "  "}),
        ("render_notify", ("Stop", "\n"), {}),
        ("render_notify", ("Stop", "notice"), {"context": "context"}),
        ("render_halt", (" \t",), {"system_message": "message"}),
        ("render_halt", ("stop",), {"system_message": "  "}),
    ],
)
def test_renderers_reject_empty_text_and_invalid_events(
    render_name: str, args: tuple[object, ...], kwargs: dict[str, object]
) -> None:
    with pytest.raises(ValueError):
        getattr(_emitter(), render_name)(*args, **kwargs)


def test_emit_writes_both_streams_and_exits(capsys: pytest.CaptureFixture[str]) -> None:
    emitter = _emitter()

    with pytest.raises(SystemExit) as raised:
        emitter.emit(emitter.HookEmission("hook output\n", "diagnostic\n", 2))

    captured = capsys.readouterr()
    assert captured.out == "hook output\n"
    assert captured.err == "diagnostic\n"
    assert raised.value.code == 2


def test_emitter_depends_only_on_the_standard_library() -> None:
    path = Path(_emitter().__file__)
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0, f"relative import at line {node.lineno}"
            if node.module:
                imports.add(node.module.split(".", 1)[0])

    assert "autoskillit" not in imports
    assert imports <= sys.stdlib_module_names


def test_emitter_channel_table_covers_every_public_emitter() -> None:
    emitter = _emitter()

    assert set(emitter.EMITTER_CHANNELS.values()) <= set(emitter.CHANNEL_EVENTS)
    tree = ast.parse(Path(emitter.__file__).read_text(encoding="utf-8"))
    public_emitters = {
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and not node.name.startswith("_")
        and node.name != "emit"
        and not node.name.startswith("render_")
    }
    assert public_emitters == set(emitter.EMITTER_CHANNELS)
