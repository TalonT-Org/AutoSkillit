"""Hook stdout and non-zero exit codes belong to the protocol emitter."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tests._hook_channel_scan import (
    HOOKS_DIR,
    ChannelScanError,
    all_registered_hook_defs,
    registered_scripts,
    scan_source_channels,
    scan_source_sinks,
)

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

_EMITTER = "_runtime/_hook_output.py"
_DISPATCH = "_dispatch.py"
_NON_PROTOCOL_STDOUT_OWNERS: dict[str, str] = {
    "_capture/_replay.py": "replays captured command bytes to the wrapped tool's stdout",
    "_capture_artifacts.py": "runs as a standalone artifact capture command",
}


def _hook_sources() -> dict[str, Path]:
    return {
        path.relative_to(HOOKS_DIR).as_posix(): path
        for path in HOOKS_DIR.rglob("*.py")
        if "__pycache__" not in path.parts
    }


def test_only_the_emitter_owns_hook_protocol_sinks() -> None:
    registered = registered_scripts(all_registered_hook_defs())
    exemptions = set(_NON_PROTOCOL_STDOUT_OWNERS)
    exemptions.add(_DISPATCH)
    failures: list[str] = []

    for relative, path in sorted(_hook_sources().items()):
        if relative == _EMITTER or relative in exemptions:
            continue
        source = path.read_text(encoding="utf-8")
        sinks = scan_source_sinks(source, filename=relative)
        for sink in sinks:
            failures.append(
                f"{relative}:{sink.line}:{sink.col} ({sink.kind}); "
                "use a channel function in hooks/_runtime/_hook_output.py"
            )

    assert not failures, "Hook protocol output has unowned sinks:\n" + "\n".join(failures)
    assert _EMITTER in _hook_sources()
    assert _EMITTER not in registered


def test_dispatcher_exemption_is_exact() -> None:
    path = HOOKS_DIR / _DISPATCH
    source = path.read_text(encoding="utf-8")
    sinks = scan_source_sinks(source, filename=_DISPATCH)

    assert [(sink.line, sink.kind) for sink in sinks] == [
        (53, "nonzero-exit"),
        (103, "nonzero-exit"),
    ]
    tree = ast.parse(source, filename=_DISPATCH)
    prints = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "print"
    ]
    assert [node.lineno for node in prints] == [46, 52]
    for call in prints:
        file_arg = next(
            (keyword.value for keyword in call.keywords if keyword.arg == "file"), None
        )
        assert (
            isinstance(file_arg, ast.Attribute)
            and file_arg.attr == "stderr"
            and isinstance(file_arg.value, ast.Name)
            and file_arg.value.id == "sys"
        )


def test_non_protocol_stdout_owners_are_narrow_and_live() -> None:
    assert set(_NON_PROTOCOL_STDOUT_OWNERS) == {
        "_capture/_replay.py",
        "_capture_artifacts.py",
    }
    registered = registered_scripts(all_registered_hook_defs())

    for relative, reason in _NON_PROTOCOL_STDOUT_OWNERS.items():
        assert reason
        assert relative not in registered
        sinks = scan_source_sinks(
            (HOOKS_DIR / relative).read_text(encoding="utf-8"),
            filename=relative,
        )
        assert sinks, f"stale stdout owner exemption for {relative}"


@pytest.mark.parametrize(
    ("label", "source"),
    [
        ("sys.stdout", "import sys\nsys.stdout.write('x')\n"),
        ("sys alias", "import sys as s\ns.stdout.write('x')\n"),
        ("sys.__stdout__", "import sys\nsys.__stdout__.flush()\n"),
        ("stdout import", "from sys import stdout\nstdout.write('x')\n"),
        ("__stdout__ import", "from sys import __stdout__ as out\nout.flush()\n"),
        ("stdout assignment alias", "import sys\nout = sys.stdout\nout.write('x')\n"),
        ("print default", "print('x')\n"),
        ("print explicit stdout", "import sys\nprint('x', file=sys.stdout)\n"),
        ("os.write fd one", "import os\nos.write(1, b'x')\n"),
        ("os alias", "import os as o\no.write(1, b'x')\n"),
        ("write import alias", "from os import write as raw_write\nraw_write(1, b'x')\n"),
        (
            "stdout file descriptor",
            "import os\nimport sys\nos.write(sys.stdout.fileno(), b'x')\n",
        ),
        ("sys.exit nonzero", "import sys\nsys.exit(2)\n"),
        ("sys alias exit dynamic", "import sys as s\nstatus = 2\ns.exit(status)\n"),
        ("exit import alias", "from sys import exit as stop\nstop(2)\n"),
        ("SystemExit", "raise SystemExit(2)\n"),
        ("built-in exit", "exit(2)\n"),
        ("built-in quit", "quit(2)\n"),
        ("os._exit", "import os\nos._exit(2)\n"),
        ("os exit alias", "from os import _exit as stop\nstop(status)\n"),
    ],
    ids=[
        "sys.stdout",
        "sys alias",
        "sys.__stdout__",
        "stdout import",
        "__stdout__ import",
        "stdout assignment alias",
        "print default",
        "print explicit stdout",
        "os.write fd one",
        "os alias",
        "write import alias",
        "stdout file descriptor",
        "sys.exit nonzero",
        "sys alias exit dynamic",
        "exit import alias",
        "SystemExit",
        "built-in exit",
        "built-in quit",
        "os._exit",
        "os exit alias",
    ],
)
def test_sink_scanner_finds_stdout_and_nonzero_exit_forms(label: str, source: str) -> None:
    assert scan_source_sinks(source, filename=f"{label}.py")


def test_stderr_output_is_not_a_protocol_sink() -> None:
    assert not scan_source_sinks(
        "import sys\nprint('diagnostic', file=sys.stderr)\n",
        filename="stderr.py",
    )
    assert not scan_source_sinks(
        "from sys import stderr as error_stream\nprint('diagnostic', file=error_stream)\n",
        filename="stderr-alias.py",
    )


def test_literal_zero_exits_are_not_protocol_sinks() -> None:
    assert not scan_source_sinks(
        "import os\nimport sys\nsys.exit(0)\nraise SystemExit(0)\nos._exit(0)\n",
        filename="fail-open.py",
    )


@pytest.mark.parametrize(
    "source",
    [
        pytest.param(
            "from _hook_output import deny_tool_use as deny\ndef hook():\n    deny('reason')\n",
            id="from-import-function-local",
        ),
        pytest.param("import _hook_output as output\noutput.block('reason')\n", id="module-alias"),
        pytest.param(
            "from autoskillit.hooks._runtime._hook_output import deny_tool_use\n"
            "deny_tool_use('reason')\n",
            id="package-from-import",
        ),
        pytest.param(
            "import autoskillit.hooks._runtime._hook_output\n"
            "autoskillit.hooks._runtime._hook_output.block('reason')\n",
            id="package-import",
        ),
        pytest.param(
            "from autoskillit.hooks._runtime import _hook_output as output\n"
            "output.add_context('PostToolUse', 'context')\n",
            id="package-module-import",
        ),
    ],
)
def test_channel_scanner_resolves_emitter_import_forms(source: str) -> None:
    channels = scan_source_channels(source)
    assert channels
    assert not scan_source_sinks(source)


@pytest.mark.parametrize(
    "source",
    [
        pytest.param(
            "import _hook_output as output\ngetattr(output, name)('reason')\n",
            id="dynamic-getattr",
        ),
        pytest.param("from _hook_output import *\n", id="star-import"),
        pytest.param(
            "from _hook_output import deny_tool_use\nsaved = deny_tool_use\n", id="saved-wrapper"
        ),
        pytest.param(
            "from _hook_output import render_block\nrender_block('reason')\n", id="render-call"
        ),
        pytest.param("import _hook_output as output\noutput.emit(emission)\n", id="emit-call"),
    ],
)
def test_channel_scanner_fails_closed_on_unresolved_emission(source: str) -> None:
    with pytest.raises(ChannelScanError):
        scan_source_channels(source)


def test_clean_channel_calls_are_not_direct_sinks() -> None:
    source = (
        "from _hook_output import deny_tool_use, block\n"
        "def hook():\n    deny_tool_use('reason')\n    block('reason')\n"
    )

    assert scan_source_channels(source) == {"deny": frozenset({None}), "block": frozenset({None})}
    assert not scan_source_sinks(source)
