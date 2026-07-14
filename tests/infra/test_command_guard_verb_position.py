"""Structural ratchet: guards must not perform raw substring membership on shell command text.

Direct ``in`` / ``not in`` checks against raw command strings or simple
aliases derived from them (``.lower()``, ``or``-joined variants) miss the
verb position entirely. A guard that searches for the substring ``"pr"``
inside ``"printer"`` will silently false-positive. This ratchet enforces
that command-inspecting guards tokenise shell text before comparing.

Scope: only guard scripts returned by ``_find_command_inspecting_guards()``
from ``test_command_guard_completeness``. Shared command-classification
internals live outside this ratchet — they are the implementation that
guards must consume.

The detector must be non-vacuous: synthetic self-tests prove that the
parser rejects literal-membership, generator-expression aliases, and the
post-shlex-tokenization pattern, and accepts token-list / positional-arg
comparisons.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("infra"), pytest.mark.small]


# Comparison operators that indicate raw substring membership.
_RAW_MEMBERSHIP_OPS = (ast.In, ast.NotIn)


def _find_command_inspecting_guards() -> list[tuple[str, Path]]:
    """Reuse the discovery helper from the structural completion test.

    Importing the function (rather than re-implementing it) ensures the two
    ratchets stay in lockstep.
    """
    from tests.infra.test_command_guard_completeness import (
        _find_command_inspecting_guards as impl,
    )

    return impl()


def _collect_taint_targets(tree: ast.AST) -> set[str]:
    """Seed taint with function parameters named cmd/command and tool_input reads."""
    seeds: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            for arg in (*node.args.args, *node.args.kwonlyargs):
                if arg.arg in {"cmd", "command"}:
                    seeds.add(arg.arg)
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and len(node.args) >= 1
        ):
            first = node.args[0]
            if isinstance(first, ast.Constant) and first.value in {"cmd", "command"}:
                seeds.add("<tool_input>")
    return seeds


def _tainted_names(tree: ast.AST, seeds: set[str]) -> set[str]:
    """Propagate taint through direct aliases, ``or``, and casefold methods.

    A name is tainted when it is assigned from a tainted name or from an
    expression whose sub-expressions are all tainted. Lower() / casefold()
    chains preserve taint; calls to other functions do not.
    """
    tainted: set[str] = set(seeds)
    changed = True
    while changed:
        changed = False
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign):
                continue
            if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
                continue
            target_name = node.targets[0].id
            if target_name in tainted:
                continue
            value = node.value
            if _expr_is_tainted(value, tainted):
                tainted.add(target_name)
                changed = True
    return tainted


def _expr_is_tainted(expr: ast.expr, tainted: set[str]) -> bool:
    """Return True when *expr* is built entirely from tainted sub-expressions."""
    if isinstance(expr, ast.Name):
        return expr.id in tainted
    if isinstance(expr, ast.BinOp) and isinstance(expr.op, ast.BitOr):
        # Allow ``or`` joins of command reads.
        return _expr_is_tainted(expr.left, tainted) and _expr_is_tainted(expr.right, tainted)
    if isinstance(expr, ast.Call) and isinstance(expr.func, ast.Attribute):
        if expr.func.attr in {"lower", "casefold"} and isinstance(expr.func.value, ast.Name):
            return expr.func.value.id in tainted
        return False
    return False


def _raw_membership_sites(source: str, cmd_seeds: set[str] | None = None) -> list[tuple[int, str]]:
    """Return (line_number, fragment) for each tainted raw-membership comparison.

    ``cmd_seeds`` lets synthetic tests inject function parameters named cmd/command
    even when the source string does not start with a ``def f(cmd):`` signature
    that the AST would otherwise pick up. Repository scans pass the default None
    and rely on signature discovery.
    """
    tree = ast.parse(source)
    seeded = _collect_taint_targets(tree)
    if cmd_seeds:
        seeded |= cmd_seeds
    # ``<tool_input>`` is a synthetic sentinel; skip it from variable taint
    # because it represents a Call result, not a Name identifier.
    tainted_vars = _tainted_names(tree, seeded - {"<tool_input>"})

    sites: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare):
            continue
        if not any(isinstance(op, _RAW_MEMBERSHIP_OPS) for op in node.ops):
            continue
        for comparator in node.comparators:
            name = _comparator_name(comparator)
            if name is not None and name in tainted_vars:
                sites.append((node.lineno, ast.unparse(node.comparators[0])))
                break
    return sites


def _comparator_name(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        if isinstance(node.func.value, ast.Name):
            return node.func.value.id
    return None


class TestRawCommandMembershipRatchet:
    """Repository-wide scan rejecting raw substring membership in guards."""

    def test_no_raw_membership_in_command_inspecting_guards(self) -> None:
        guards = _find_command_inspecting_guards()
        assert guards, "Discovery helper must return at least one guard (non-vacuous ratchet)"

        offenders: list[str] = []
        for guard_name, script_path in guards:
            source = script_path.read_text()
            for lineno, fragment in _raw_membership_sites(source):
                offenders.append(
                    f"{guard_name}:{lineno} — raw substring membership ({fragment!r})"
                )

        assert not offenders, (
            "Guards must not perform raw substring membership on shell command text. "
            "Use tokenize_shell_payload_segments() and command_verb_and_args() instead. "
            "Offending sites:\n  " + "\n  ".join(offenders)
        )


class TestDetectorSyntheticSelfTests:
    """Synthetic cases proving the detector is non-vacuous."""

    def test_rejects_literal_in_cmd(self) -> None:
        source = 'def f(cmd):\n    return "create" in cmd\n'
        assert _raw_membership_sites(source, {"cmd"})

    def test_rejects_generator_expression_alias(self) -> None:
        source = (
            "def f(cmd):\n"
            "    cmd_lower = cmd.lower()\n"
            "    return any('gh' in cmd_lower and 'pr' in cmd_lower for _ in [1])\n"
        )
        # Taint must propagate through the .lower() alias.
        sites = _raw_membership_sites(source, {"cmd"})
        assert sites, "Taint must propagate through cmd.lower() aliases"

    def test_rejects_membership_after_unrelated_shlex_split(self) -> None:
        source = (
            "import shlex\n"
            "def f(cmd):\n"
            "    tokens = shlex.split(cmd)\n"
            "    return 'gh' in cmd and 'pr' in cmd\n"
        )
        assert _raw_membership_sites(source, {"cmd"})

    def test_accepts_membership_against_token_list(self) -> None:
        source = "def f(cmd):\n    tokens = cmd.split()\n    return 'gh' in tokens\n"
        assert not _raw_membership_sites(source, {"cmd"})

    def test_accepts_positional_argument_classifier(self) -> None:
        source = (
            "from autoskillit.hooks._command_classification import command_verb_and_args\n"
            "def f(cmd):\n"
            "    tokens = cmd.split()\n"
            "    verb, args = command_verb_and_args(tokens)\n"
            "    return verb == 'gh' and args[:2] == ['pr', 'create']\n"
        )
        assert not _raw_membership_sites(source, {"cmd"})

    def test_rejects_or_joined_taint(self) -> None:
        source = "def f(cmd):\n    alt = cmd or ''\n    return 'create' in alt\n"
        sites = _raw_membership_sites(source, {"cmd"})
        assert sites, "Taint must propagate through ``or`` joins"
