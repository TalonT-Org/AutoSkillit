"""Unit tests for ``iter_codex_hook_commands``, the single Codex hook parser."""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("hooks"), pytest.mark.small]


def _writer_shape_table(dispatch_path: Path) -> dict[str, list[dict]]:
    return {
        "PreToolUse": [
            {
                "matcher": "Bash",
                "hooks": [
                    {
                        "type": "command",
                        "command": f"python3 -B {dispatch_path} guards/example_guard",
                        "trusted_hash": "deadbeef",
                    },
                    {
                        "type": "command",
                        "command": "echo foreign",
                    },
                ],
            }
        ],
        "Stop": [
            {
                "hooks": [
                    {
                        "type": "command",
                        "command": f"python3 -B {dispatch_path} guards/stop_guard",
                    }
                ],
            }
        ],
    }


def test_iter_codex_hook_commands_yields_writer_shape_fields(tmp_path: Path) -> None:
    from autoskillit.execution.backends._codex_hooks import iter_codex_hook_commands

    dispatch_path = tmp_path / "hooks" / "_dispatch.py"
    commands = list(iter_codex_hook_commands(_writer_shape_table(dispatch_path)))

    bash_command = next(c for c in commands if c.event == "PreToolUse" and "example" in c.command)
    assert bash_command.matcher == "Bash"
    assert bash_command.command == f"python3 -B {dispatch_path} guards/example_guard"
    assert bash_command.dispatcher == dispatch_path
    assert bash_command.logical_name == "guards/example_guard"

    stop_command = next(c for c in commands if c.event == "Stop")
    assert stop_command.matcher is None
    assert stop_command.command == f"python3 -B {dispatch_path} guards/stop_guard"
    assert stop_command.dispatcher == dispatch_path
    assert stop_command.logical_name == "guards/stop_guard"


def test_iter_codex_hook_commands_yields_foreign_commands_too(tmp_path: Path) -> None:
    from autoskillit.execution.backends._codex_hooks import (
        is_autoskillit_hook_command,
        iter_codex_hook_commands,
    )

    dispatch_path = tmp_path / "hooks" / "_dispatch.py"
    commands = list(iter_codex_hook_commands(_writer_shape_table(dispatch_path)))

    foreign = [c for c in commands if not is_autoskillit_hook_command(c.command)]
    assert len(foreign) == 1
    assert foreign[0].command == "echo foreign"
    assert foreign[0].logical_name is None

    autoskillit = [c for c in commands if is_autoskillit_hook_command(c.command)]
    assert len(autoskillit) == 2
    assert all(c.logical_name is not None for c in autoskillit)


def test_iter_codex_hook_commands_ignores_malformed_shapes() -> None:
    from autoskillit.execution.backends._codex_hooks import iter_codex_hook_commands

    assert list(iter_codex_hook_commands(None)) == []
    assert list(iter_codex_hook_commands([])) == []
    assert list(iter_codex_hook_commands({"PreToolUse": "not-a-list"})) == []
    assert list(iter_codex_hook_commands({"PreToolUse": [{"hooks": "not-a-list"}]})) == []
    assert list(iter_codex_hook_commands({"PreToolUse": [{"hooks": [{"command": 7}]}]})) == []
    assert list(iter_codex_hook_commands({"PreToolUse": ["not-a-dict-entry"]})) == []


def test_dispatcher_is_none_for_unparseable_or_single_token_commands() -> None:
    from autoskillit.execution.backends._codex_hooks import iter_codex_hook_commands

    table = {
        "PreToolUse": [
            {
                "hooks": [
                    {"type": "command", "command": 'python3 -B "unterminated'},
                    {"type": "command", "command": "singleword"},
                ]
            }
        ]
    }
    commands = list(iter_codex_hook_commands(table))
    assert len(commands) == 2
    for command in commands:
        assert command.dispatcher is None
        assert command.logical_name is None


def test_is_autoskillit_hook_entry_and_rendered_guard_scripts_agree_with_iterator(
    tmp_path: Path,
) -> None:
    from autoskillit.execution.backends._codex_hooks import (
        _is_autoskillit_hook_entry,
        iter_codex_hook_commands,
    )
    from autoskillit.execution.backends._codex_managed_route import (
        _rendered_codex_guard_scripts,
    )

    dispatch_path = tmp_path / "hooks" / "_dispatch.py"
    table = _writer_shape_table(dispatch_path)

    entry_flags = {
        event: [_is_autoskillit_hook_entry(entry) for entry in entries]
        for event, entries in table.items()
    }
    assert entry_flags == {"PreToolUse": [True], "Stop": [True]}

    iterator_logical_names = {
        hook.logical_name
        for hook in iter_codex_hook_commands(table)
        if hook.logical_name is not None
    }
    assert _rendered_codex_guard_scripts(table) == {
        name.removeprefix("guards/") for name in iterator_logical_names
    }
