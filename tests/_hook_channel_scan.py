"""Static scans for hook protocol channels and output sinks."""

from __future__ import annotations

import ast
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from autoskillit.hooks._runtime._hook_output import EMITTER_CHANNELS

HOOKS_DIR = Path(__file__).resolve().parents[1] / "src" / "autoskillit" / "hooks"

_CHANNEL_WRAPPERS = EMITTER_CHANNELS
_CHANNELS_WITH_EVENT_POSITION = frozenset({"context", "notify"})
_EMITTER_MODULES = frozenset(
    {
        "_hook_output",
        "autoskillit.hooks._runtime._hook_output",
    }
)
_HELPER_MODULES = frozenset(
    {
        "_session_scope_authority",
        "_runtime._session_scope_authority",
        "autoskillit.hooks._runtime._session_scope_authority",
    }
)

# Shared hook helpers can emit on behalf of a caller. Keep this list explicit:
# adding a helper requires a review of every caller that inherits its channel.
EMITTING_HELPERS: dict[tuple[str, str], frozenset[str]] = {
    ("_runtime/_session_scope_authority.py", "enforce_script_session_scope"): frozenset({"deny"}),
}


class ChannelScanError(ValueError):
    """Raised when a hook's protocol behavior cannot be resolved statically."""


@dataclass(frozen=True)
class OutputSink:
    line: int
    col: int
    kind: str


@dataclass
class _Bindings:
    wrappers: dict[str, str]
    modules: dict[str, str]
    helper_names: dict[str, str]
    forbidden_names: set[str]
    sys_names: set[str]
    os_names: set[str]
    stdout_names: set[str]
    stderr_names: set[str]
    sys_exit_names: set[str]
    os_write_names: set[str]
    os_exit_names: set[str]


def _is_emitter_module(module: str | None) -> bool:
    return bool(module and (module in _EMITTER_MODULES or module.endswith("._hook_output")))


def _is_helper_module(module: str | None) -> bool:
    return bool(
        module and (module in _HELPER_MODULES or module.endswith("._session_scope_authority"))
    )


def _bind_module(bindings: _Bindings, alias: ast.alias) -> None:
    module = alias.name
    if _is_emitter_module(module) or _is_helper_module(module):
        # An unaliased dotted import binds its root name.
        root = module.split(".", 1)[0]
        bindings.modules[alias.asname or root] = module if alias.asname else root
    if module == "sys":
        bindings.sys_names.add(alias.asname or "sys")
    elif module == "os":
        bindings.os_names.add(alias.asname or "os")


def _bind_member(bindings: _Bindings, module: str | None, alias: ast.alias) -> None:
    local = alias.asname or alias.name
    if _is_emitter_module(module):
        if alias.name == "*":
            raise ChannelScanError("star import from _hook_output cannot be resolved")
        if alias.name in _CHANNEL_WRAPPERS:
            bindings.wrappers[local] = _CHANNEL_WRAPPERS[alias.name]
        else:
            bindings.forbidden_names.add(local)
    elif _is_helper_module(module):
        if alias.name != "*":
            bindings.helper_names[local] = alias.name
    else:
        members = {
            ("sys", "stdout"): bindings.stdout_names,
            ("sys", "__stdout__"): bindings.stdout_names,
            ("sys", "stderr"): bindings.stderr_names,
            ("sys", "exit"): bindings.sys_exit_names,
            ("os", "write"): bindings.os_write_names,
            ("os", "_exit"): bindings.os_exit_names,
        }
        names = members.get((module, alias.name))
        if names is not None:
            names.add(local)
    if (
        module
        and (
            module in {"_runtime", "autoskillit.hooks._runtime"}
            or module.endswith(".hooks._runtime")
        )
        and alias.name in {"_hook_output", "_session_scope_authority"}
    ):
        bindings.modules[local] = f"{module}.{alias.name}"


def _bindings(tree: ast.AST) -> _Bindings:
    bindings = _Bindings(
        wrappers={},
        modules={},
        helper_names={},
        forbidden_names=set(),
        sys_names={"sys"},
        os_names={"os"},
        stdout_names=set(),
        stderr_names=set(),
        sys_exit_names={"sys.exit"},
        os_write_names={"os.write"},
        os_exit_names={"os._exit"},
    )
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                _bind_module(bindings, alias)
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                _bind_member(bindings, node.module, alias)
    return bindings


def _dotted_name(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _dotted_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else None
    return None


def _module_target(expression: ast.expr, bindings: _Bindings) -> tuple[str, str] | None:
    dotted = _dotted_name(expression)
    if dotted is None:
        return None
    for local, module in bindings.modules.items():
        if dotted == local:
            return local, module
        if dotted.startswith(local + "."):
            return local, module + dotted[len(local) :]
    return None


def _resolve_wrapper(expression: ast.expr, bindings: _Bindings) -> str | None:
    if isinstance(expression, ast.Name):
        return bindings.wrappers.get(expression.id)
    dotted = _dotted_name(expression)
    if dotted is None:
        return None
    if isinstance(expression, ast.Attribute):
        owner = _module_target(expression.value, bindings)
        if owner and _is_emitter_module(owner[1]):
            return _CHANNEL_WRAPPERS.get(expression.attr)
    return None


def _called_name(expression: ast.expr, bindings: _Bindings) -> str | None:
    if isinstance(expression, ast.Name):
        return expression.id
    dotted = _dotted_name(expression)
    if dotted:
        return dotted
    return None


def _event_argument(call: ast.Call, channel: str) -> str | None:
    for keyword in call.keywords:
        if keyword.arg == "event":
            return (
                keyword.value.value
                if isinstance(keyword.value, ast.Constant) and isinstance(keyword.value.value, str)
                else None
            )
    if channel in _CHANNELS_WITH_EVENT_POSITION and call.args:
        event = call.args[0]
        return (
            event.value
            if isinstance(event, ast.Constant) and isinstance(event.value, str)
            else None
        )
    return None


class _ChannelVisitor(ast.NodeVisitor):
    def __init__(self, bindings: _Bindings, *, include_helpers: bool = True) -> None:
        self.bindings = bindings
        self.include_helpers = include_helpers
        self.channels: dict[str, set[str | None]] = {}
        self._parents: dict[ast.AST, ast.AST] = {}

    def collect(self, node: ast.AST) -> dict[str, frozenset[str | None]]:
        for parent in ast.walk(node):
            for child in ast.iter_child_nodes(parent):
                self._parents[child] = parent
        self.visit(node)
        return {name: frozenset(events) for name, events in self.channels.items()}

    def _call_channel(self, node: ast.Call) -> str | None:
        function_name = _called_name(node.func, self.bindings)
        module_target = (
            _module_target(node.func.value, self.bindings)
            if isinstance(node.func, ast.Attribute)
            else _module_target(node.func, self.bindings)
        )
        if isinstance(node.func, ast.Name) and node.func.id in self.bindings.forbidden_names:
            raise ChannelScanError(f"calls {node.func.id}, bypassing the channel wrappers")
        if module_target and _is_emitter_module(module_target[1]):
            member = node.func.attr if isinstance(node.func, ast.Attribute) else ""
            if member == "emit" or member.startswith("render_"):
                raise ChannelScanError(
                    f"calls _hook_output.{member}, bypassing the channel wrappers"
                )
            channel = _CHANNEL_WRAPPERS.get(member)
            if channel is None:
                raise ChannelScanError(f"unresolved call to _hook_output.{member}")
        else:
            channel = _resolve_wrapper(node.func, self.bindings)
        if function_name in {"getattr", "builtins.getattr"} and node.args:
            target = _module_target(node.args[0], self.bindings)
            if target and _is_emitter_module(target[1]):
                raise ChannelScanError("getattr on _hook_output cannot be resolved statically")
        return channel

    def _add_helper_channels(self, node: ast.Call) -> None:
        if not self.include_helpers:
            return
        helper = None
        if isinstance(node.func, ast.Name):
            helper = self.bindings.helper_names.get(node.func.id)
        elif isinstance(node.func, ast.Attribute):
            owner = _module_target(node.func.value, self.bindings)
            if owner and _is_helper_module(owner[1]):
                helper = node.func.attr
        for (_path, name), channels in EMITTING_HELPERS.items():
            if name == helper:
                for inherited in channels:
                    self.channels.setdefault(inherited, set()).add(None)

    def visit_Call(self, node: ast.Call) -> None:
        channel = self._call_channel(node)
        if channel:
            self.channels.setdefault(channel, set()).add(_event_argument(node, channel))
        self._add_helper_channels(node)
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        if node.id in self.bindings.forbidden_names:
            parent = self._parents.get(node)
            if not (isinstance(parent, ast.Call) and parent.func is node):
                raise ChannelScanError(
                    f"emitter function {node.id} is referenced without being called"
                )
        if node.id in self.bindings.wrappers:
            parent = self._parents.get(node)
            if not (isinstance(parent, ast.Call) and parent.func is node):
                raise ChannelScanError(
                    f"emitter wrapper {node.id} is referenced without being called"
                )
        if node.id in self.bindings.modules:
            parent = self._parents.get(node)
            current: ast.AST = node
            while isinstance(parent, ast.Attribute) and parent.value is current:
                current = parent
                parent = self._parents.get(parent)
            module = _module_target(current, self.bindings)
            owner = (
                _module_target(current.value, self.bindings)
                if isinstance(current, ast.Attribute)
                else module
            )
            if not (
                (module and _is_emitter_module(module[1]))
                or (owner and _is_emitter_module(owner[1]))
            ):
                return
            if not (isinstance(parent, ast.Call) and parent.func is current):
                raise ChannelScanError(
                    f"emitter module {node.id} is referenced outside a direct call"
                )


def scan_source_channels(
    source: str, *, script_rel: str = "<source>"
) -> dict[str, frozenset[str | None]]:
    """Return statically visible channel calls in hook source text."""
    try:
        tree = ast.parse(source, filename=script_rel)
    except SyntaxError as exc:
        raise ChannelScanError(f"cannot parse {script_rel}: {exc}") from exc
    return _ChannelVisitor(_bindings(tree)).collect(tree)


def scan_script_channels(script_rel: str) -> dict[str, frozenset[str | None]]:
    """Scan one path relative to the hook source directory."""
    path = HOOKS_DIR / script_rel
    try:
        source = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ChannelScanError(f"cannot read hook script {script_rel}: {exc}") from exc
    return scan_source_channels(source, script_rel=script_rel)


def validate_emitting_helper_inventory() -> None:
    """Require the explicit helper inventory to match direct emitter calls."""
    paths = sorted((HOOKS_DIR / "_runtime").glob("*.py"))
    paths.extend(
        HOOKS_DIR / name for name in ("_session_binding.py", "_join_ledger.py", "_write_scope.py")
    )
    actual: dict[tuple[str, str], frozenset[str]] = {}
    for path in paths:
        if not path.exists():
            continue
        relative = path.relative_to(HOOKS_DIR).as_posix()
        source = path.read_text(encoding="utf-8")
        try:
            tree = ast.parse(source, filename=relative)
        except SyntaxError as exc:
            raise ChannelScanError(f"cannot parse helper {relative}: {exc}") from exc
        bindings = _bindings(tree)
        for node in tree.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            channels = _ChannelVisitor(bindings, include_helpers=False).collect(node)
            if channels:
                actual[(relative, node.name)] = frozenset(channels)
    if actual != EMITTING_HELPERS:
        missing = {
            key: value for key, value in EMITTING_HELPERS.items() if actual.get(key) != value
        }
        unexpected = {
            key: value for key, value in actual.items() if EMITTING_HELPERS.get(key) != value
        }
        raise ChannelScanError(
            f"emitting helper inventory is stale; missing={missing}, unexpected={unexpected}"
        )


def registered_scripts(emitted_defs: Iterable[Any]) -> frozenset[str]:
    """Flatten ``HookDef.scripts`` from an iterable of emitted hook definitions."""
    return frozenset(script for hook_def in emitted_defs for script in hook_def.scripts)


def all_registered_hook_defs() -> tuple[Any, ...]:
    """Return registry definitions plus every managed-route injection."""
    from autoskillit.execution.backends._codex_hooks import (
        MANAGED_CODEX_ROUTE_NAMES,
        _managed_route_hook_defs,
    )
    from autoskillit.hook_registry import HOOK_REGISTRY

    return tuple(HOOK_REGISTRY) + tuple(
        hook_def
        for route in MANAGED_CODEX_ROUTE_NAMES
        for hook_def in _managed_route_hook_defs(route)
    )


def _is_stdout_expression(expression: ast.expr, bindings: _Bindings) -> bool:
    if isinstance(expression, ast.Attribute) and expression.attr in {"stdout", "__stdout__"}:
        dotted = _dotted_name(expression.value)
        return dotted in bindings.sys_names
    return isinstance(expression, ast.Name) and expression.id in bindings.stdout_names


def _is_stderr_expression(expression: ast.expr, bindings: _Bindings) -> bool:
    if isinstance(expression, ast.Attribute) and expression.attr == "stderr":
        dotted = _dotted_name(expression.value)
        return dotted in bindings.sys_names
    return isinstance(expression, ast.Name) and expression.id in bindings.stderr_names


def _is_sys_exit(expression: ast.expr, bindings: _Bindings) -> bool:
    if isinstance(expression, ast.Name):
        return expression.id in bindings.sys_exit_names
    dotted = _dotted_name(expression)
    if dotted in bindings.sys_exit_names:
        return True
    if isinstance(expression, ast.Attribute) and expression.attr == "exit":
        owner = _dotted_name(expression.value)
        return owner in bindings.sys_names
    return False


def _is_os_write(expression: ast.expr, bindings: _Bindings) -> bool:
    if isinstance(expression, ast.Name):
        return expression.id in bindings.os_write_names
    if isinstance(expression, ast.Attribute) and expression.attr == "write":
        return _dotted_name(expression.value) in bindings.os_names
    return False


def _is_os_exit(expression: ast.expr, bindings: _Bindings) -> bool:
    if isinstance(expression, ast.Name):
        return expression.id in bindings.os_exit_names
    if isinstance(expression, ast.Attribute) and expression.attr == "_exit":
        return _dotted_name(expression.value) in bindings.os_names
    return False


def _is_stdout_fileno(expression: ast.expr, bindings: _Bindings) -> bool:
    return (
        isinstance(expression, ast.Call)
        and isinstance(expression.func, ast.Attribute)
        and expression.func.attr == "fileno"
        and _is_stdout_expression(expression.func.value, bindings)
    )


def _call_sink_kinds(node: ast.Call, bindings: _Bindings) -> list[str]:
    kinds: list[str] = []
    if isinstance(node.func, ast.Name) and node.func.id == "print":
        file_arg = next((kw.value for kw in node.keywords if kw.arg == "file"), None)
        if file_arg is None or not _is_stderr_expression(file_arg, bindings):
            kinds.append("print-stdout")
    if _is_os_write(node.func, bindings) and node.args:
        fd = node.args[0]
        if (
            isinstance(fd, ast.Constant) and type(fd.value) is int and fd.value == 1
        ) or _is_stdout_fileno(fd, bindings):
            kinds.append("os-write-stdout")
    exit_call = _is_sys_exit(node.func, bindings) or _is_os_exit(node.func, bindings)
    exit_call = exit_call or (
        isinstance(node.func, ast.Name) and node.func.id in {"SystemExit", "exit", "quit"}
    )
    if exit_call and node.args:
        status = node.args[0]
        if not (
            isinstance(status, ast.Constant) and type(status.value) is int and status.value == 0
        ):
            kinds.append("nonzero-exit")
    return kinds


def scan_source_sinks(source: str, *, filename: str = "<source>") -> tuple[OutputSink, ...]:
    """Return stdout and non-zero-exit protocol sinks in Python source text."""
    try:
        tree = ast.parse(source, filename=filename)
    except SyntaxError as exc:
        raise ChannelScanError(f"cannot parse {filename}: {exc}") from exc
    bindings = _bindings(tree)
    sinks: list[OutputSink] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in {"stdout", "__stdout__"}:
            owner = _dotted_name(node.value)
            if owner in bindings.sys_names:
                sinks.append(OutputSink(node.lineno, node.col_offset, "stdout"))
        elif isinstance(node, ast.ImportFrom) and node.module == "sys":
            for alias in node.names:
                if alias.name in {"stdout", "__stdout__"}:
                    sinks.append(OutputSink(node.lineno, node.col_offset, "stdout-import"))
        elif isinstance(node, ast.Call):
            sinks.extend(
                OutputSink(node.lineno, node.col_offset, kind)
                for kind in _call_sink_kinds(node, bindings)
            )
    return tuple(sorted(sinks, key=lambda sink: (sink.line, sink.col, sink.kind)))
