"""Pin signal authority to the shell-capture process owner."""

from __future__ import annotations

import ast
from collections import Counter
from pathlib import Path

import pytest

from tests.arch._helpers import SRC_ROOT

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

_SRC = Path(SRC_ROOT)
_SIGNAL_CALLEES = frozenset(
    {
        "kill",
        "killpg",
        "send_signal",
        "terminate",
        "signal_group",
        "_signal_process_group",
    }
)
_EXPECTED_SIGNAL_SITES: dict[tuple[str, str, str], tuple[int, str]] = {
    ("hooks/_capture_process.py", "OwnedProcessGroup.terminate", "signal_group"): (
        1,
        "the public terminate operation declares runner authority",
    ),
    ("hooks/_capture_process.py", "OwnedProcessGroup.kill", "signal_group"): (
        1,
        "the public kill operation declares runner authority",
    ),
    (
        "hooks/_capture_process.py",
        "OwnedProcessGroup.signal_group",
        "_signal_process_group",
    ): (
        1,
        "all owned group signals pass through the validated process-group primitive",
    ),
    ("hooks/_capture_process.py", "OwnedProcessGroup.settle", "signal_group"): (
        2,
        "settlement has one terminate and one kill escalation",
    ),
    (
        "hooks/_capture_process.py",
        "OwnedProcessGroup._settle_remaining_group",
        "signal_group",
    ): (1, "post-completion grace may request runner-owned termination"),
    (
        "hooks/_capture_process.py",
        "OwnedProcessGroup._release_lifeline",
        "signal_group",
    ): (2, "fallback kills cover an anchor that fails to exit or fire its lifeline"),
    (
        "hooks/_capture_process.py",
        "_install_signal_forwarding.forward",
        "signal_group",
    ): (1, "host signals are relayed with forwarded authority"),
    (
        "hooks/_capture_process.py",
        "_take_foreground_process_group",
        "killpg",
    ): (1, "direct-mode terminal adoption resumes the owned group"),
    ("hooks/_capture_process.py", "_probe_process_group", "killpg"): (
        1,
        "signal zero probes group existence without delivering a signal",
    ),
    ("hooks/_capture_process.py", "_signal_process_group", "killpg"): (
        1,
        "the validated low-level primitive is the sole process-group signal syscall",
    ),
    ("hooks/_capture_spawn.py", "_finish_owned_spawn", "kill"): (
        1,
        "identity failure kills the leader before ownership is established",
    ),
    ("hooks/_capture_spawn.py", "_abandon_anchor", "kill"): (
        1,
        "failed pre-ownership lifeline cleanup kills its anchor",
    ),
    ("hooks/_capture/_replay.py", "settle_failed_capture", "terminate"): (
        1,
        "the non-owned Popen fallback first requests termination",
    ),
    ("hooks/_capture/_replay.py", "settle_failed_capture", "kill"): (
        2,
        "the non-owned Popen fallback has two bounded kill paths",
    ),
}


def _capture_paths() -> list[Path]:
    hooks = _SRC / "hooks"
    capture = hooks / "_capture"
    return [
        hooks / "_capture_process.py",
        hooks / "_capture_spawn.py",
        *sorted(capture.glob("*.py")),
    ]


def _callee_name(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


class _SignalSiteVisitor(ast.NodeVisitor):
    def __init__(self, relative_path: str) -> None:
        self.relative_path = relative_path
        self.scope: list[str] = []
        self.sites: list[tuple[str, str, str]] = []

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()

    def visit_Call(self, node: ast.Call) -> None:
        callee = _callee_name(node.func)
        if callee in _SIGNAL_CALLEES:
            self.sites.append((self.relative_path, ".".join(self.scope) or "<module>", callee))
        self.generic_visit(node)


def _signal_sites(path: Path) -> list[tuple[str, str, str]]:
    relative = path.relative_to(_SRC).as_posix()
    visitor = _SignalSiteVisitor(relative)
    visitor.visit(ast.parse(path.read_text(encoding="utf-8")))
    return visitor.sites


def test_capture_signal_sites_match_inventory() -> None:
    observed = Counter(site for path in _capture_paths() for site in _signal_sites(path))
    expected = Counter({site: count for site, (count, _reason) in _EXPECTED_SIGNAL_SITES.items()})
    if observed != expected:
        differences = [
            f"{path}::{scope}::{callee}: expected {expected[(path, scope, callee)]}, "
            f"observed {observed[(path, scope, callee)]}; {reason}"
            for (path, scope, callee), (_count, reason) in _EXPECTED_SIGNAL_SITES.items()
            if observed[(path, scope, callee)] != expected[(path, scope, callee)]
        ]
        differences.extend(
            f"unexpected {path}::{scope}::{callee} x{count}"
            for (path, scope, callee), count in (observed - expected).items()
        )
        pytest.fail("capture signal inventory changed:\n" + "\n".join(differences))


def test_capture_drain_is_signal_free() -> None:
    drain = _SRC / "hooks" / "_capture" / "_drain.py"
    assert _signal_sites(drain) == []


def test_signal_group_calls_declare_origin() -> None:
    violations: list[str] = []
    for path in _capture_paths():
        relative = path.relative_to(_SRC).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or _callee_name(node.func) != "signal_group":
                continue
            origin = next(
                (keyword.value for keyword in node.keywords if keyword.arg == "origin"),
                None,
            )
            if not (
                isinstance(origin, ast.Attribute)
                and origin.attr in {"RUNNER", "FORWARDED"}
                and isinstance(origin.value, ast.Name)
                and origin.value.id == "SignalOrigin"
            ):
                violations.append(f"{relative}:{node.lineno}: {ast.unparse(node)}")

    assert not violations, "signal_group calls need an explicit SignalOrigin:\n" + "\n".join(
        violations
    )
