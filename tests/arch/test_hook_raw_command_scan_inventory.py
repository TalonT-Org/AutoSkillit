"""Every raw scan of a command-classifying hook's own command text is inventoried.

Rectify #4941 Part A makes the tokenizer (`_classification/_tokenizer.py`)
the only reader of the raw command; every other scanner should consume the
`evaluated_payloads`/`all_evaluated_segments`/`live_command_text` projection
instead. A guard or classifier that reads a `command`/`cmd`/`payload`
parameter directly -- via `shlex.split`/`shlex.shlex`, a `<NAME>_RE`/`re.*`
method call, `.lower()`/`.splitlines()`, a `"..." in <subject>` membership
check, or by handing it to another command-classification helper
(`tokenize_command_segments`, `tokenize_shell_payload_segments`,
`has_interpreter_wrapped_command`, `extract_shell_command_payloads`) -- must
appear in `_EXPECTED_RAW_COMMAND_SCANS` below. Adding a new site (or a
guard migrating off one, in a follow-on part) requires touching this file,
so the change is a conscious, reviewed diff rather than a silent bypass.

Scope: `src/autoskillit/hooks/_classification/*.py`,
`src/autoskillit/hooks/guards/*.py`, and the two hooks/-root classification
modules (`_command_classification.py`, `_github_mutation_analysis.py`). A
subject reached through `live_command_text(...)` or
`all_evaluated_segments(...)` is the sanctioned projection and is never
inventoried.
"""

from __future__ import annotations

import ast
from datetime import date
from pathlib import Path

import pytest

from tests._evaluation_shape_matrix import DEFERRED_SHAPES
from tests.arch._deferred_debt import (
    TrackedDeferral,
    assert_deferrals_have_regression_tests,
    assert_not_stale,
    assert_rationale_present,
)

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

REPO_ROOT = Path(__file__).resolve().parents[2]
HOOKS_ROOT = REPO_ROOT / "src" / "autoskillit" / "hooks"

_RAW_PARAM_NAMES = frozenset({"command", "cmd", "payload"})
# A subject reached through either of these is the sanctioned projection;
# resolution stops there rather than tracing back to the raw parameter.
_SANCTIONED_CALLS = frozenset({"live_command_text", "all_evaluated_segments"})
_RE_METHODS = frozenset({"search", "match", "sub", "subn", "finditer", "fullmatch", "split"})
# Command-classification helpers a guard can hand a raw parameter to (or a
# loop-carried variable seeded from one) instead of scanning it directly --
# indirection through one of these still counts as a raw-command read, and
# is exactly the escape hatch a private per-guard parser would otherwise use.
_HELPER_INDIRECTION_CALLS = frozenset(
    {
        "tokenize_shell_payload_segments",
        "has_interpreter_wrapped_command",
        "extract_shell_command_payloads",
        "tokenize_command_segments",
    }
)


def _callee_name(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _callee_dotted(node: ast.expr) -> str | None:
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
        return f"{node.value.id}.{node.attr}"
    if isinstance(node, ast.Name):
        return node.id
    return None


class _RawScanCollector(ast.NodeVisitor):
    """Walk one module, recording every raw-command-touching call per function."""

    def __init__(self, relpath: str) -> None:
        self.relpath = relpath
        self._func_stack: list[str] = []
        self.findings: list[tuple[str, str, str]] = []

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self._func_stack.append(node.name)
        args = node.args
        tracked = {a.arg for a in (*args.args, *args.posonlyargs, *args.kwonlyargs)}
        tracked &= _RAW_PARAM_NAMES
        self._scan_body(node.body, tracked)
        for child in node.body:
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self.visit(child)
        self._func_stack.pop()

    def _resolve_subject(self, node: ast.expr, tracked: set[str]) -> bool:
        """Return whether *node* resolves back to a tracked raw name.

        Unwraps at most one level of call-wrapping (e.g. a subject passed
        through a nested call) so `f(strip_heredoc_bodies(command))`-shaped
        expressions are still recognized; a call to a sanctioned projection
        stops resolution instead of tracing through it.
        """
        if isinstance(node, ast.Name):
            return node.id in tracked
        if isinstance(node, ast.Call):
            if _callee_name(node.func) in _SANCTIONED_CALLS:
                return False
            return any(self._resolve_subject(arg, tracked) for arg in node.args)
        return False

    def _scan_body(self, body: list[ast.stmt], tracked: set[str]) -> None:
        module = ast.Module(body=body, type_ignores=[])

        # Pass 1: propagate simple one-hop aliasing (NAME = <expr>) so a
        # locally renamed or re-stripped copy of the raw parameter is still
        # tracked when it is later scanned.
        for stmt in ast.walk(module):
            if not (isinstance(stmt, ast.Assign) and len(stmt.targets) == 1):
                continue
            target = stmt.targets[0]
            if not isinstance(target, ast.Name):
                continue
            value = stmt.value
            if isinstance(value, ast.Name) and value.id in tracked:
                tracked.add(target.id)
            elif isinstance(value, ast.Call):
                callee = _callee_name(value.func)
                if callee in _SANCTIONED_CALLS:
                    continue
                if any(isinstance(a, ast.Name) and a.id in tracked for a in value.args):
                    tracked.add(target.id)

        # Pass 2: record every raw-touching call/comparison, now that the
        # tracked-name set includes propagated aliases.
        for node in ast.walk(module):
            if isinstance(node, ast.Call):
                self._visit_call(node, tracked)
            elif isinstance(node, ast.Compare):
                self._visit_compare(node, tracked)

    def _record(self, primitive: str) -> None:
        self.findings.append((self.relpath, ".".join(self._func_stack), primitive))

    def _visit_call(self, node: ast.Call, tracked: set[str]) -> None:
        dotted = _callee_dotted(node.func)
        callee = _callee_name(node.func)

        if dotted in ("shlex.split", "shlex.shlex") and node.args:
            if self._resolve_subject(node.args[0], tracked):
                self._record(dotted)
            return

        if callee in _SANCTIONED_CALLS:
            return  # never trace into the sanctioned projection's own args

        if callee in _HELPER_INDIRECTION_CALLS and tracked:
            self._record(callee)
            return

        if isinstance(node.func, ast.Attribute) and node.func.attr in _RE_METHODS:
            obj = node.func.value
            if isinstance(obj, ast.Name) and (obj.id.endswith("_RE") or obj.id == "re"):
                subject_idx = 1 if obj.id == "re" else 0
                if len(node.args) > subject_idx and self._resolve_subject(
                    node.args[subject_idx], tracked
                ):
                    self._record(f"{obj.id}.{node.func.attr}")
            return

        if isinstance(node.func, ast.Attribute) and node.func.attr in ("lower", "splitlines"):
            obj = node.func.value
            if isinstance(obj, ast.Name) and obj.id in tracked:
                self._record(f"str.{node.func.attr}")

    def _visit_compare(self, node: ast.Compare, tracked: set[str]) -> None:
        if (
            len(node.ops) == 1
            and isinstance(node.ops[0], ast.In)
            and isinstance(node.left, ast.Constant)
            and self._resolve_subject(node.comparators[0], tracked)
        ):
            self._record("str.__contains__")


def _scoped_source_files() -> list[Path]:
    files = list((HOOKS_ROOT / "_classification").glob("*.py"))
    files += list((HOOKS_ROOT / "guards").glob("*.py"))
    files += [
        HOOKS_ROOT / "_runtime" / "_command_classification.py",
        HOOKS_ROOT / "_runtime" / "_github_mutation_analysis.py",
    ]
    return sorted(set(files))


def _scan_module(path: Path, relpath: str) -> list[tuple[str, str, str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    collector = _RawScanCollector(relpath)
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            collector.visit(node)
        elif isinstance(node, ast.ClassDef):
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    collector.visit(sub)
    return collector.findings


def _observed_raw_scans() -> tuple[tuple[str, str, str], ...]:
    findings: list[tuple[str, str, str]] = []
    for path in _scoped_source_files():
        relpath = "hooks/" + str(path.relative_to(HOOKS_ROOT))
        findings.extend(_scan_module(path, relpath))
    return tuple(sorted(set(findings)))


# Materialized baseline as of rectify #4941 Part B. Every guard's private
# parser named in Part A's baseline has now migrated onto
# `all_evaluated_segments`/`live_command_text`/`interpreter_invokes`, and
# each such call is sanctioned (never inventoried) by this scanner. The
# handful of entries below are the two kinds of unavoidable exception the
# raw-scan census is designed to surface as a conscious, reviewed diff:
# (1) `_classification/_tokenizer.py`'s own sites (the sole general parsing
# authority for command text) and `_interpreters.py`'s own internal
# tokenizer use in building that authority, and (2) `compose_pr_body_guard.py`'s
# private per-evaluated-payload segmentation walk, needed because
# `all_evaluated_segments` flattens across payloads while `$VAR` resolution
# must stay scoped to the one payload that defines it.
_EXPECTED_RAW_COMMAND_SCANS: frozenset[tuple[str, str, str]] = frozenset(
    {
        # _classification/_interpreters.py -- the authority's own internal
        # tokenizer use (all_evaluated_segments/tokenize_shell_payload_segments
        # feeding each other and the tokenizer facade).
        (
            "hooks/_classification/_interpreters.py",
            "all_evaluated_segments",
            "tokenize_command_segments",
        ),
        (
            "hooks/_classification/_interpreters.py",
            "all_evaluated_segments",
            "tokenize_shell_payload_segments",
        ),
        (
            "hooks/_classification/_interpreters.py",
            "tokenize_shell_payload_segments",
            "extract_shell_command_payloads",
        ),
        (
            "hooks/_classification/_interpreters.py",
            "tokenize_shell_payload_segments",
            "tokenize_command_segments",
        ),
        (
            "hooks/_classification/_interpreters.py",
            "interpreter_invokes",
            "tokenize_command_segments",
        ),
        # git_ops_guard.py's own outer-segment/nested-payload tokenization,
        # unchanged since Part A (its structural_mutation/_raw_target_mutations
        # inputs were migrated to live_command_text; these two calls are the
        # sanctioned tokenizer entry points feeding the per-segment cd/git
        # classification loop and the additional_segments builder).
        (
            "hooks/guards/git_ops_guard.py",
            "_preflight_checked_out_ref_mutation",
            "tokenize_command_segments",
        ),
        (
            "hooks/guards/git_ops_guard.py",
            "_preflight_checked_out_ref_mutation",
            "tokenize_shell_payload_segments",
        ),
        # compose_pr_body_guard.py -- per-evaluated-payload segmentation
        # (Part B 2.10): `all_evaluated_segments` flattens every payload's
        # segments into one list, losing which segments belong to which
        # payload; a $VAR lookup for a nested bash -c/heredoc/pipe body's
        # `gh pr create` must resolve only from that same payload's own
        # assignments, so this guard tokenizes each evaluated payload
        # independently rather than consuming the shared authority.
        (
            "hooks/guards/compose_pr_body_guard.py",
            "_iter_evaluated_payload_segments",
            "extract_shell_command_payloads",
        ),
        (
            "hooks/guards/compose_pr_body_guard.py",
            "_iter_evaluated_payload_segments",
            "tokenize_command_segments",
        ),
    }
)


def test_hook_raw_command_scan_inventory_is_complete() -> None:
    """A new raw-command scan under hooks/ must join this file's baseline."""
    observed = set(_observed_raw_scans())
    unexpected = sorted(observed - _EXPECTED_RAW_COMMAND_SCANS)
    missing = sorted(_EXPECTED_RAW_COMMAND_SCANS - observed)
    details = []
    if unexpected:
        details.append(
            "New raw-command scans not yet in _EXPECTED_RAW_COMMAND_SCANS "
            f"(add them here with a rationale, or route through evaluated_payloads/"
            f"all_evaluated_segments/live_command_text instead): {unexpected}"
        )
    if missing:
        details.append(
            "Baseline entries no longer observed (the site was migrated or removed -- "
            f"delete these rows): {missing}"
        )
    assert not details, "\n".join(details)


def test_raw_command_scan_detector_catches_direct_shlex_split() -> None:
    """Canary: a synthetic direct shlex.split(command) must be detected."""
    tree = ast.parse("def f(command):\n    return shlex.split(command)\n")
    collector = _RawScanCollector("synthetic.py")
    collector.visit(tree.body[0])
    assert collector.findings == [("synthetic.py", "f", "shlex.split")]


def test_raw_command_scan_detector_catches_helper_indirection() -> None:
    """Canary: handing the raw parameter to a classification helper counts too."""
    tree = ast.parse("def f(command):\n    return tokenize_command_segments(command)\n")
    collector = _RawScanCollector("synthetic.py")
    collector.visit(tree.body[0])
    assert collector.findings == [("synthetic.py", "f", "tokenize_command_segments")]


def test_raw_command_scan_detector_does_not_flag_sanctioned_projection() -> None:
    """A subject reached through live_command_text/all_evaluated_segments is exempt."""
    tree = ast.parse(
        "def f(command):\n"
        "    segments = all_evaluated_segments(command)\n"
        "    text = live_command_text(command)\n"
        "    return _SOME_RE.search(text)\n"
    )
    collector = _RawScanCollector("synthetic.py")
    collector.visit(tree.body[0])
    assert collector.findings == []


# ---------------------------------------------------------------------------
# Deferred stdin-literal regex limitations (tracking issue #4973)
# ---------------------------------------------------------------------------

_DEFERRED_STDIN_LITERAL_SHAPES: dict[str, TrackedDeferral] = {
    "two-heredocs-one-line": TrackedDeferral(
        issue=4973,
        rationale=(
            "_HEREDOC_BODY_RE matches one heredoc per opening line; a second "
            "`<<` operator on the same line is not recognized as a distinct "
            "heredoc, so its body is never captured or bound to a consumer."
        ),
        added_date=date(2026, 9, 11),
        regression_test=(
            "tests/hooks/test_command_classification.py::"
            "TestDeferredStdinLiteralShapes::test_two_heredocs_one_line"
        ),
    ),
    "backslash-quoted-delimiter": TrackedDeferral(
        issue=4973,
        rationale=(
            "_HEREDOC_BODY_RE's quote group is `['\"]?`, which does not "
            "recognize a backslash-quoted delimiter (`<<\\EOF`) as quoted -- "
            "outer_expansion is derived only for unquoted and whole-quoted "
            "word delimiters."
        ),
        added_date=date(2026, 9, 11),
        regression_test=(
            "tests/hooks/test_command_classification.py::"
            "TestDeferredStdinLiteralShapes::test_backslash_quoted_delimiter"
        ),
    ),
    "partially-quoted-delimiter": TrackedDeferral(
        issue=4973,
        rationale=(
            'A delimiter word with only part of it quoted (`<<E"OF"`) is '
            "not recognized by _HEREDOC_BODY_RE's single leading/trailing "
            "quote-character group, so its outer_expansion cannot be derived."
        ),
        added_date=date(2026, 9, 11),
        regression_test=(
            "tests/hooks/test_command_classification.py::"
            "TestDeferredStdinLiteralShapes::test_partially_quoted_delimiter"
        ),
    ),
    "unterminated-heredoc": TrackedDeferral(
        issue=4973,
        rationale=(
            "With no matching terminator line, _HEREDOC_BODY_RE never "
            "matches at all, so the body is not captured as a StdinLiteral "
            "and its lines tokenize as ordinary outer command text instead."
        ),
        added_date=date(2026, 9, 11),
        regression_test=(
            "tests/hooks/test_command_classification.py::"
            "TestDeferredStdinLiteralShapes::test_unterminated_heredoc"
        ),
    ),
    "non-word-delimiter": TrackedDeferral(
        issue=4973,
        rationale=(
            "_HEREDOC_BODY_RE's delimiter class is `\\w+`, which rejects a "
            "delimiter containing non-word characters such as a hyphen "
            "(`<<'END-OF-DOC'`), so the heredoc is not recognized at all."
        ),
        added_date=date(2026, 9, 11),
        regression_test=(
            "tests/hooks/test_command_classification.py::"
            "TestDeferredStdinLiteralShapes::test_non_word_delimiter"
        ),
    ),
}


def test_deferred_stdin_literal_shapes_match_matrix_registry() -> None:
    """The deferral registry here must name exactly DEFERRED_SHAPES's keys."""
    assert set(_DEFERRED_STDIN_LITERAL_SHAPES) == set(DEFERRED_SHAPES)


def test_deferred_stdin_literal_shapes_are_rationale_and_current() -> None:
    assert_rationale_present(
        _DEFERRED_STDIN_LITERAL_SHAPES, registry_name="deferred stdin-literal shapes"
    )
    assert_not_stale(_DEFERRED_STDIN_LITERAL_SHAPES, registry_name="deferred stdin-literal shapes")


def test_deferred_stdin_literal_shapes_have_regression_tests(
    request: pytest.FixtureRequest,
) -> None:
    collected = {item.nodeid for item in request.session.items}
    assert_deferrals_have_regression_tests(
        _DEFERRED_STDIN_LITERAL_SHAPES,
        registry_name="deferred stdin-literal shapes",
        collected_node_ids=collected,
    )
