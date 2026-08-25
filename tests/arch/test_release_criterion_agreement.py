"""Release availability and upgrade advancement must use one criterion."""

from __future__ import annotations

import ast
from typing import assert_never

import pytest

from autoskillit.core import (
    AdvanceVerdict,
    ReleaseChannel,
    ReleaseIdentity,
    advance_verdict,
    update_available,
)

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]


def _representative_pairs(
    channel: ReleaseChannel,
) -> tuple[tuple[ReleaseIdentity, ReleaseIdentity], ...]:
    match channel:
        case ReleaseChannel.RELEASED:
            installed = ReleaseIdentity(channel, version="1.0.0")
            return (
                (installed, ReleaseIdentity(channel, version="1.1.0")),
                (installed, ReleaseIdentity(channel, version="1.0.0")),
            )
        case ReleaseChannel.BRANCH:
            installed = ReleaseIdentity(channel, version="1.0.0", commit="a" * 40, ref="develop")
            return (
                (
                    installed,
                    ReleaseIdentity(channel, version="1.0.0", commit="b" * 40, ref="develop"),
                ),
                (installed, installed),
            )
        case ReleaseChannel.WORKING_TREE:
            installed = ReleaseIdentity(channel, version="1.0.0")
            return ((installed, ReleaseIdentity(channel, version="1.1.0")),)
        case unhandled:
            assert_never(unhandled)


@pytest.mark.parametrize("channel", list(ReleaseChannel))
def test_available_update_is_always_satisfiable(channel: ReleaseChannel) -> None:
    for installed, target in _representative_pairs(channel):
        verdict = advance_verdict(previous=installed, observed=target, target=target)
        if update_available(installed, target):
            assert verdict == AdvanceVerdict.ADVANCED
        else:
            assert verdict in (AdvanceVerdict.UNCHANGED, AdvanceVerdict.NOT_APPLICABLE)


class _ExhaustiveDispatchVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.has_match = False
        self.has_assert_never = False

    def visit_Match(self, node: ast.Match) -> None:
        self.has_match = True
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        if isinstance(node.func, ast.Name) and node.func.id == "assert_never":
            self.has_assert_never = True
        self.generic_visit(node)


@pytest.mark.parametrize("function_name", ["update_available", "advance_verdict"])
def test_release_channel_dispatch_is_exhaustive(function_name: str) -> None:
    from autoskillit.core import pkg_root

    source_path = pkg_root() / "core" / "_release_identity.py"
    tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
    function = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == function_name
    )
    visitor = _ExhaustiveDispatchVisitor()
    visitor.visit(ast.Module(body=function.body, type_ignores=[]))

    assert visitor.has_match, f"{function_name} must dispatch with match"
    assert visitor.has_assert_never, f"{function_name} must close with assert_never"
