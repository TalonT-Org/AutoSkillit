#!/usr/bin/env python3
"""Enforce a diff-scoped cyclomatic-complexity ratchet across src/, tests/, and scripts/.

A function in a changed file may not exceed max(MAX_COMPLEXITY, its complexity at the base
revision); a function with no base counterpart -- a genuinely new function -- may not
exceed MAX_COMPLEXITY. An unchanged function can never violate; a function that shrinks
stays clean; one that grows past its own prior ceiling is the "patch logic in instead of
restructuring" signature this ratchet exists to catch. Growth is judged against the same
qualname's complexity at the base revision, following git rename detection, or -- when a
function vanished from its own base file in this diff -- among same-qualname functions that
vanished elsewhere in the diff, so a whole-module rename or a moved function inherits its
prior ceiling instead of being judged as brand new.

Two modes, matching scripts/check_file_lengths.py's own dual-mode shape:
``--staged`` compares the index against HEAD, for the local pre-commit hook; ``--base REF``
compares the working tree against the merge base of HEAD and REF, for CI. Both routes read
the changed-file set themselves rather than trusting pass-through filenames, because the
project's mandatory ``pre-commit run --all-files`` would otherwise turn the hook into a
full-tree scan (scripts/check_file_lengths.py:13-16) -- hence ``pass_filenames: false`` on
both local hooks and each script doing its own staged-diff discovery.

Ships in ENFORCEMENT = "warn" (Phase 1): every violation is reported and annotated but the
process always exits 0. Phase 2 promotion is the one-line change ENFORCEMENT = "fail";
nothing else in this file, the pre-commit hook, or the CI step needs to change -- the CI
step already propagates whatever exit code this script returns. Because ENFORCEMENT and
MAX_COMPLEXITY are themselves capable of being loosened, the CI step (.github/workflows/
tests.yml, "Cyclomatic complexity gate") runs the *base revision's* copy of this script
against the working tree with --repo-root, exactly as the preceding "Policy relaxation
gate" step does for scripts/check_policy_relaxation.py -- so a candidate diff cannot weaken
the checker that judges it.

Compatibility note: this counter is pinned to Ruff's C901 by a parity test
(tests/infra/test_check_complexity_ruff_parity.py), which checks it against Ruff 0.15.15's
get_complexity_number (https://github.com/astral-sh/ruff/blob/0.15.15/crates/ruff_linter/
src/rules/mccabe/rules/function_is_too_complex.rs#L75). Expression-level constructs --
boolean operators, conditional expressions, comprehensions, assertions, and lambdas -- never
increment this counter, because only statements are walked; an unguarded, unpacked-free
final `match` case (`case _`, a bare name, or an `|` alternative containing one) receives an
else-like discount of 1, since it can never fail to match.
"""

from __future__ import annotations

import argparse
import ast
import dataclasses
import io
import os
import subprocess
import sys
import tokenize
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Literal

PROJECT_ROOT = Path(__file__).resolve().parent.parent
LIMITS_PATH = Path("tests") / "arch" / "_complexity_limits.py"
SCAN_ROOTS: tuple[str, ...] = ("src", "tests", "scripts")
_GIT_TIMEOUT_SECONDS = 30

Enforcement = Literal["warn", "fail"]
ENFORCEMENT: Enforcement = "warn"  # Phase 1. Phase 2 promotion: "fail". Nothing else changes.


class GitFailure(RuntimeError):
    """A required git operation failed, timed out, or could not be launched."""


class PolicyUnavailable(RuntimeError):
    """The complexity policy data module could not be read as literal data."""


@dataclasses.dataclass(frozen=True, slots=True)
class ComplexityPolicy:
    max_complexity: int
    min_rationale_chars: int
    exemptions: Mapping[str, tuple[int, str]]  # literal (limit, rationale) pairs


@dataclasses.dataclass(frozen=True, slots=True)
class FunctionMetrics:
    complexity: int
    lineno: int
    end_lineno: int


@dataclasses.dataclass(frozen=True, slots=True)
class ChangedFile:
    """A: (path, None, False). M: (path, path, False). D: (path, path, True) -- `path` is
    the file's only known location. R: (new, old, False). `deleted` is the sole
    discriminator for "no head-side counterpart"; `base_path is None` means added."""

    path: str
    base_path: str | None
    deleted: bool = False


@dataclasses.dataclass(frozen=True, slots=True)
class Violation:
    key: str
    path: str
    qualname: str
    lineno: int
    end_lineno: int
    complexity: int
    allowed: int
    base_complexity: int | None  # scalar fed to allowed_complexity
    base_path: str | None  # where base_complexity came from (report only)
    exemption: tuple[int, str] | None


# --- Policy: read tests/arch/_complexity_limits.py as literal data, never execute it -------


def _assignment_target(node: ast.stmt, symbol: str) -> ast.expr | None:
    """Return *symbol*'s assigned value if *node* is its module-level assignment."""
    if isinstance(node, ast.Assign):
        targets = node.targets
        if len(targets) == 1 and isinstance(targets[0], ast.Name) and targets[0].id == symbol:
            return node.value
        return None
    if isinstance(node, ast.AnnAssign):
        if (
            isinstance(node.target, ast.Name)
            and node.target.id == symbol
            and node.value is not None
        ):
            return node.value
        return None
    if (
        isinstance(node, ast.AugAssign)
        and isinstance(node.target, ast.Name)
        and node.target.id == symbol
    ):
        raise PolicyUnavailable(f"{symbol}: augmented assignment is not readable")
    return None


def _module_assignment(source: str, symbol: str) -> ast.expr:
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise PolicyUnavailable(f"{symbol}: policy source does not parse ({exc})") from exc
    value: ast.expr | None = None
    for node in tree.body:
        found = _assignment_target(node, symbol)
        if found is not None:
            if value is not None:
                raise PolicyUnavailable(f"{symbol}: multiple module-level assignments")
            value = found
    if value is None:
        raise PolicyUnavailable(f"{symbol}: no module-level assignment")
    return value


def _int_literal(node: ast.expr, context: str) -> int:
    if (
        not isinstance(node, ast.Constant)
        or isinstance(node.value, bool)
        or not isinstance(node.value, int)
    ):
        raise PolicyUnavailable(f"{context}: expected a literal int")
    if node.value <= 0:
        raise PolicyUnavailable(f"{context}: expected a positive int")
    return node.value


def _str_literal(node: ast.expr, context: str) -> str:
    if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
        raise PolicyUnavailable(f"{context}: expected a literal str")
    return node.value


def _exemption_call(node: ast.expr, context: str) -> tuple[int, str]:
    if not isinstance(node, ast.Call):
        raise PolicyUnavailable(f"{context}: expected a ComplexityExemption(...) call")
    if len(node.args) > 2:
        raise PolicyUnavailable(f"{context}: too many positional arguments")
    names = ("limit", "rationale")
    fields: dict[str, ast.expr] = dict(zip(names, node.args))
    for keyword in node.keywords:
        if keyword.arg is None or keyword.arg not in names:
            raise PolicyUnavailable(f"{context}: unsupported constructor argument")
        if keyword.arg in fields:
            raise PolicyUnavailable(f"{context}: duplicate argument {keyword.arg!r}")
        fields[keyword.arg] = keyword.value
    if set(fields) != set(names):
        raise PolicyUnavailable(f"{context}: expected exactly limit and rationale")
    return _int_literal(fields["limit"], context), _str_literal(fields["rationale"], context)


def _exemption_dict(node: ast.expr, context: str) -> dict[str, tuple[int, str]]:
    if not isinstance(node, ast.Dict):
        raise PolicyUnavailable(f"{context}: expected a dict literal")
    exemptions: dict[str, tuple[int, str]] = {}
    for key_node, value_node in zip(node.keys, node.values):
        if key_node is None:
            raise PolicyUnavailable(f"{context}: dict unpacking is not readable")
        key = _str_literal(key_node, context)
        if key in exemptions:
            raise PolicyUnavailable(f"{context}: duplicate key {key!r}")
        exemptions[key] = _exemption_call(value_node, f"{context}[{key!r}]")
    return exemptions


def load_policy(source_for: Callable[[str], str | None]) -> ComplexityPolicy:
    """Read the candidate's literal policy through *source_for*; never import or exec it."""
    path = LIMITS_PATH.as_posix()
    source = source_for(path)
    if source is None:
        raise PolicyUnavailable(f"{path}: not found")
    max_complexity = _int_literal(_module_assignment(source, "MAX_COMPLEXITY"), "MAX_COMPLEXITY")
    min_rationale_chars = _int_literal(
        _module_assignment(source, "MIN_RATIONALE_CHARS"), "MIN_RATIONALE_CHARS"
    )
    exemptions = _exemption_dict(
        _module_assignment(source, "COMPLEXITY_EXEMPTIONS"), "COMPLEXITY_EXEMPTIONS"
    )
    return ComplexityPolicy(max_complexity, min_rationale_chars, exemptions)


# --- Counter: exactly ruff's C901 semantics, one isinstance branch per node class -----------


def _is_irrefutable(pattern: ast.pattern) -> bool:
    if isinstance(pattern, ast.MatchAs):
        return pattern.pattern is None or _is_irrefutable(pattern.pattern)
    if isinstance(pattern, ast.MatchOr):
        return any(_is_irrefutable(alternative) for alternative in pattern.patterns)
    return False


def _match_complexity(stmt: ast.Match) -> int:
    count = len(stmt.cases)
    last = stmt.cases[-1] if stmt.cases else None
    if last is not None and last.guard is None and _is_irrefutable(last.pattern):
        count -= 1
    return count + sum(_body_complexity(case.body) for case in stmt.cases)


def _try_complexity(stmt: ast.Try | ast.TryStar) -> int:
    total = _body_complexity(stmt.body)
    for handler in stmt.handlers:
        total += 1 + _body_complexity(handler.body)
    if stmt.orelse:
        total += 1
    return total + _body_complexity(stmt.orelse) + _body_complexity(stmt.finalbody)


def _stmt_complexity(stmt: ast.stmt) -> int:
    if isinstance(stmt, ast.If):
        return 1 + _body_complexity(stmt.body) + _body_complexity(stmt.orelse)
    if isinstance(stmt, (ast.For, ast.AsyncFor, ast.While)):
        return 1 + _body_complexity(stmt.body) + _body_complexity(stmt.orelse)
    if isinstance(stmt, (ast.With, ast.AsyncWith)):
        return _body_complexity(stmt.body)
    if isinstance(stmt, (ast.Try, ast.TryStar)):
        return _try_complexity(stmt)
    if isinstance(stmt, ast.Match):
        return _match_complexity(stmt)
    if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return 1 + _body_complexity(stmt.body)
    if isinstance(stmt, ast.ClassDef):
        return _body_complexity(stmt.body)
    return 0


def _body_complexity(stmts: Sequence[ast.stmt]) -> int:
    return sum(_stmt_complexity(stmt) for stmt in stmts)


def cyclomatic_complexity(node: ast.FunctionDef | ast.AsyncFunctionDef) -> int:
    return 1 + _body_complexity(node.body)


# --- Function inventory: stable path::qualname keys, nested defs reported on their own -----


def _record_function(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    prefix: tuple[str, ...],
    metrics: dict[str, FunctionMetrics],
) -> None:
    qualname = ".".join((*prefix, node.name))
    complexity = cyclomatic_complexity(node)
    existing = metrics.get(qualname)
    if existing is None or complexity > existing.complexity:
        metrics[qualname] = FunctionMetrics(
            complexity, node.lineno, node.end_lineno or node.lineno
        )


def _walk(node: ast.AST, prefix: tuple[str, ...], metrics: dict[str, FunctionMetrics]) -> None:
    for child in ast.iter_child_nodes(node):
        if isinstance(child, ast.expr):
            continue
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
            _record_function(child, prefix, metrics)
            _walk(child, (*prefix, child.name, "<locals>"), metrics)
        elif isinstance(child, ast.ClassDef):
            _walk(child, (*prefix, child.name), metrics)
        else:
            _walk(child, prefix, metrics)


def function_metrics(tree: ast.Module) -> dict[str, FunctionMetrics]:
    metrics: dict[str, FunctionMetrics] = {}
    _walk(tree, (), metrics)
    return metrics


def in_scope(path: str) -> bool:
    return path.endswith(".py") and path.split("/", 1)[0] in SCAN_ROOTS


def _parse_metrics(source: str, path: str) -> dict[str, FunctionMetrics]:
    try:
        tree = ast.parse(source, filename=path)
    except SyntaxError as exc:
        print(f"note: {path} does not parse ({exc}); contributes no metrics", file=sys.stderr)
        return {}
    return function_metrics(tree)


# --- Git plumbing: shapes of scripts/check_policy_relaxation.py's _git/git_show/merge_base -


def _git(repo_root: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
    """Run one git subprocess; never raises for a non-zero exit, only for a broken launch."""
    try:
        return subprocess.run(
            ["git", *args],
            cwd=str(repo_root),
            capture_output=True,
            timeout=_GIT_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise GitFailure(f"git {' '.join(args)}: {exc}") from exc


def _plumbing_text(result: subprocess.CompletedProcess[bytes]) -> str:
    """Decode plumbing output -- paths, revisions -- which is never itself Python source."""
    return result.stdout.decode("utf-8", errors="replace")


def _decode_source(data: bytes) -> str:
    """Decode a Python blob using its own encoding cookie/BOM, like the stdlib tokenizer."""
    encoding, _ = tokenize.detect_encoding(io.BytesIO(data).readline)
    return data.decode(encoding)


def git_show(repo_root: Path, rev: str, path: str) -> str | None:
    """Return one Python blob's decoded source at *rev*, or None when it does not exist."""
    result = _git(repo_root, "show", f"{rev}:{path}")
    return _decode_source(result.stdout) if result.returncode == 0 else None


def merge_base(repo_root: Path, ref: str) -> str | None:
    result = _git(repo_root, "merge-base", "HEAD", ref)
    return _plumbing_text(result).strip() or None if result.returncode == 0 else None


def _working_tree_reader(repo_root: Path) -> Callable[[str], str | None]:
    def read(path: str) -> str | None:
        candidate = repo_root / path
        if not candidate.is_file():
            return None
        return _decode_source(candidate.read_bytes())

    return read


def _index_reader(repo_root: Path) -> Callable[[str], str | None]:
    def read(path: str) -> str | None:
        result = _git(repo_root, "show", f":{path}")
        return _decode_source(result.stdout) if result.returncode == 0 else None

    return read


def _revision_reader(repo_root: Path, rev: str) -> Callable[[str], str | None]:
    def read(path: str) -> str | None:
        return git_show(repo_root, rev, path)

    return read


def _parse_name_status(output: str) -> list[ChangedFile]:
    tokens = output.split("\0")
    changes: list[ChangedFile] = []
    i = 0
    while i < len(tokens) and tokens[i]:
        status = tokens[i]
        if status.startswith("R"):
            changes.append(ChangedFile(path=tokens[i + 2], base_path=tokens[i + 1]))
            i += 3
        elif status == "D":
            changes.append(ChangedFile(path=tokens[i + 1], base_path=tokens[i + 1], deleted=True))
            i += 2
        else:
            base_path = tokens[i + 1] if status == "M" else None
            changes.append(ChangedFile(path=tokens[i + 1], base_path=base_path))
            i += 2
    return changes


def _in_scope_change(change: ChangedFile) -> bool:
    if in_scope(change.path):
        return True
    if change.base_path is not None and in_scope(change.base_path):
        return True
    return change.deleted and change.path.endswith(".py")


def changed_files(repo_root: Path, *, staged: bool, base_rev: str) -> list[ChangedFile]:
    if staged:
        diff_args = ["diff", "--cached", "--name-status", "-M", "-z", "--diff-filter=AMRD", "HEAD"]
    else:
        diff_args = ["diff", "--name-status", "-M", "-z", "--diff-filter=AMRD", base_rev]
    result = _git(repo_root, *diff_args)
    if result.returncode != 0:
        raise GitFailure(f"git {' '.join(diff_args)} failed: {_plumbing_text(result)}")
    changes = _parse_name_status(_plumbing_text(result))
    if not staged:
        untracked = _git(repo_root, "ls-files", "--others", "--exclude-standard", "-z")
        if untracked.returncode != 0:
            raise GitFailure("git ls-files --others --exclude-standard failed")
        for name in _plumbing_text(untracked).split("\0"):
            if name:
                changes.append(ChangedFile(path=name, base_path=None))
    return [change for change in changes if _in_scope_change(change)]


# --- Evaluation: base complexity by same-file lookup, then by vanished-function inheritance


def allowed_complexity(
    base_complexity: int | None, exemption: tuple[int, str] | None, max_complexity: int
) -> int:
    return exemption[0] if exemption is not None else max(max_complexity, base_complexity or 0)


def _read_required(source_for: Callable[[str], str | None], path: str, what: str) -> str:
    source = source_for(path)
    if source is None:
        raise GitFailure(f"required {what} source missing: {path}")
    return source


def _collect_metrics(
    changes: Sequence[ChangedFile],
    head_source_for: Callable[[str], str | None],
    base_source_for: Callable[[str], str | None],
) -> tuple[dict[str, dict[str, FunctionMetrics]], dict[str, dict[str, FunctionMetrics]]]:
    heads: dict[str, dict[str, FunctionMetrics]] = {}
    bases: dict[str, dict[str, FunctionMetrics]] = {}
    for change in changes:
        if not change.deleted and in_scope(change.path):
            source = _read_required(head_source_for, change.path, "head")
            heads[change.path] = _parse_metrics(source, change.path)
        if change.base_path is not None:
            source = _read_required(base_source_for, change.base_path, "base")
            bases[change.base_path] = _parse_metrics(source, change.base_path)
    return heads, bases


def _collect_vanished(
    changes: Sequence[ChangedFile],
    heads: Mapping[str, Mapping[str, FunctionMetrics]],
    bases: Mapping[str, Mapping[str, FunctionMetrics]],
) -> dict[str, tuple[int, str]]:
    head_counterpart = {
        change.base_path: change.path for change in changes if change.base_path is not None
    }
    vanished: dict[str, tuple[int, str]] = {}
    for base_path, functions in bases.items():
        head_functions = heads.get(head_counterpart.get(base_path, ""), {})
        for qualname, metrics in functions.items():
            if qualname in head_functions:
                continue
            candidate = (metrics.complexity, base_path)
            if qualname not in vanished or candidate > vanished[qualname]:
                vanished[qualname] = candidate
    return vanished


def _base_for(
    qualname: str,
    same_file_base: Mapping[str, FunctionMetrics] | None,
    base_path: str | None,
    vanished: Mapping[str, tuple[int, str]],
) -> tuple[int | None, str | None]:
    if same_file_base is not None and qualname in same_file_base:
        return same_file_base[qualname].complexity, base_path
    if qualname in vanished:
        return vanished[qualname]
    return None, None


def _violations_for_change(
    change: ChangedFile,
    head_functions: Mapping[str, FunctionMetrics],
    bases: Mapping[str, Mapping[str, FunctionMetrics]],
    vanished: Mapping[str, tuple[int, str]],
    policy: ComplexityPolicy,
) -> list[Violation]:
    same_file_base = bases.get(change.base_path) if change.base_path is not None else None
    violations: list[Violation] = []
    for qualname, metrics in head_functions.items():
        base_complexity, base_path = _base_for(
            qualname, same_file_base, change.base_path, vanished
        )
        key = f"{change.path}::{qualname}"
        exemption = policy.exemptions.get(key)
        allowed = allowed_complexity(base_complexity, exemption, policy.max_complexity)
        if metrics.complexity > allowed:
            violations.append(
                Violation(
                    key=key,
                    path=change.path,
                    qualname=qualname,
                    lineno=metrics.lineno,
                    end_lineno=metrics.end_lineno,
                    complexity=metrics.complexity,
                    allowed=allowed,
                    base_complexity=base_complexity,
                    base_path=base_path,
                    exemption=exemption,
                )
            )
    return violations


def evaluate(
    changes: Sequence[ChangedFile],
    head_source_for: Callable[[str], str | None],
    base_source_for: Callable[[str], str | None],
    policy: ComplexityPolicy,
) -> list[Violation]:
    heads, bases = _collect_metrics(changes, head_source_for, base_source_for)
    vanished = _collect_vanished(changes, heads, bases)
    violations: list[Violation] = []
    for change in changes:
        if change.deleted or not in_scope(change.path):
            continue
        violations.extend(
            _violations_for_change(change, heads.get(change.path, {}), bases, vanished, policy)
        )
    violations.sort(key=lambda violation: (violation.path, violation.lineno))
    return violations


# --- Exemption validity: self-invalidating on absence, on dropping below, or on outgrowing -


def _validate_key_shape(
    key: str, rationale: str, limit: int, policy: ComplexityPolicy
) -> str | None:
    if "::" not in key:
        return "malformed key -- expected path::qualname"
    path, qualname = key.split("::", 1)
    if not qualname:
        return "malformed key -- empty qualname"
    if ".." in Path(path).parts or not in_scope(path) or path != Path(path).as_posix():
        return "malformed key -- path is not an in-scope repository-relative POSIX path"
    if len(rationale.strip()) < policy.min_rationale_chars:
        return f"rationale must be at least {policy.min_rationale_chars} characters"
    if limit <= policy.max_complexity:
        return f"limit {limit} must exceed MAX_COMPLEXITY={policy.max_complexity}"
    return None


def _validate_entry(
    key: str,
    limit: int,
    rationale: str,
    policy: ComplexityPolicy,
    source_for: Callable[[str], str | None],
) -> str | None:
    shape_problem = _validate_key_shape(key, rationale, limit, policy)
    if shape_problem is not None:
        return shape_problem
    path, qualname = key.split("::", 1)
    source = source_for(path)
    if source is None:
        return "stale -- file not found -- remove the entry or fix the key"
    metrics = _parse_metrics(source, path).get(qualname)
    if metrics is None:
        return "stale -- function not found -- remove the entry or fix the key"
    if metrics.complexity <= policy.max_complexity:
        return (
            f"stale -- complexity {metrics.complexity} no longer exceeds "
            f"MAX_COMPLEXITY={policy.max_complexity}; remove the entry"
        )
    if metrics.complexity > limit:
        return f"limit {limit} is below the current complexity {metrics.complexity}"
    return None


def validate_exemptions(
    policy: ComplexityPolicy, source_for: Callable[[str], str | None]
) -> list[str]:
    problems: list[str] = []
    for key, (limit, rationale) in policy.exemptions.items():
        problem = _validate_entry(key, limit, rationale, policy, source_for)
        if problem is not None:
            problems.append(f"{key}: {problem}")
    return problems


# --- Reporting: refactor first, exemption last, one instructional snippet per violation ----


def _reason(violation: Violation, max_complexity: int) -> str:
    if violation.exemption is not None:
        return f"exempted up to {violation.exemption[0]}"
    if violation.base_complexity is None:
        return f"new function; MAX_COMPLEXITY is {max_complexity}"
    reason = (
        f"was {violation.base_complexity} at the base revision; a function already above "
        f"MAX_COMPLEXITY={max_complexity} must not grow"
    )
    if violation.base_path is not None and violation.base_path != violation.path:
        reason += f" (in {violation.base_path})"
    return reason


def _violation_lines(violation: Violation, max_complexity: int) -> str:
    location = (
        f"  {violation.path}:{violation.lineno}-{violation.end_lineno}  {violation.qualname}"
    )
    metric = (
        f"      complexity {violation.complexity} > allowed {violation.allowed}  "
        f"({_reason(violation, max_complexity)})"
    )
    return f"{location}\n{metric}"


def _exemption_snippet(violation: Violation) -> str:
    return (
        f'            "{violation.key}": ComplexityExemption(\n'
        f"                limit={violation.complexity},\n"
        f'                rationale="<which refactor you tried and why it made the code worse>",\n'
        f"            ),"
    )


_REFACTOR_INSTRUCTIONS = """\
  1. Refactor first -- this is the expected outcome. Reduce decision points (if/elif,
     for, while, except, match case, nested def) while keeping behaviour identical:
     remove redundant conditions, combine branches that perform the same operation, use
     lookup data for genuinely data-driven choices, or extract ONE cohesive responsibility
     that owns a complete branch family. Match cases and guard-clause if statements still
     count: changing syntax or flattening nesting alone does not reduce this metric. Do not
     split a function into trivially named fragments just to move the number: that hides
     complexity instead of removing it, and review will send it back."""

_EXEMPTION_INTRO = """\
  2. Exemption -- last resort, human-approved. Only when a genuine refactor attempt shows
     the complexity is essential (a parser, state machine, or exhaustive validator whose
     branches mirror the domain) and removing it would make the code harder to follow:
       a. add the entry to COMPLEXITY_EXEMPTIONS in tests/arch/_complexity_limits.py:"""

_EXEMPTION_APPROVAL = """\
          The rationale must describe the attempted refactor and why it was not viable
          (at least {min_rationale} characters). An entry whose function drops to MAX_COMPLEXITY or
          below, disappears, or exceeds its own limit is reported as stale and fails this
          check until it is removed or corrected.
       b. an exemption is an acceptance-policy relaxation: it also needs a
          PolicyRelaxationApproval in tests/arch/_acceptance_policy_surfaces.py naming a
          tracking issue and the human who approved it (tests/AGENTS.md, "Acceptance
          Policy Surfaces"). That file is human-edited policy authority -- an automated
          session must not add the approval itself; stop and hand the decision to a human."""


def render_report(
    violations: Sequence[Violation], enforcement: Enforcement, policy: ComplexityPolicy
) -> str:
    banner = (
        "Cyclomatic complexity check FAILED."
        if enforcement == "fail"
        else (
            "Cyclomatic complexity check -- WARNING ONLY: nothing is blocked yet, but this check\n"
            "will be promoted to a hard failure; fix these now while it is cheap."
        )
    )
    sections = [
        banner,
        "",
        f"{len(violations)} function(s) exceed their allowed complexity:",
        "",
        "\n".join(_violation_lines(v, policy.max_complexity) for v in violations),
        "",
        "What to do, in this order:",
        "",
        _REFACTOR_INSTRUCTIONS,
        "",
        _EXEMPTION_INTRO,
        "\n".join(_exemption_snippet(v) for v in violations),
        _EXEMPTION_APPROVAL.format(min_rationale=policy.min_rationale_chars),
        "",
        f"Total: {len(violations)} violation(s)",
    ]
    return "\n".join(sections)


def _escape_message(text: str) -> str:
    return text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def _escape_property(text: str) -> str:
    return _escape_message(text).replace(":", "%3A").replace(",", "%2C")


def _annotation(violation: Violation, level: str, max_complexity: int) -> str:
    reason = _reason(violation, max_complexity)
    message = _escape_message(
        f"{violation.qualname}: complexity {violation.complexity} exceeds allowed "
        f"{violation.allowed} ({reason}). Refactor first; an exemption is a human-approved "
        "last resort -- see this step's log."
    )
    path = _escape_property(violation.path)
    return (
        f"::{level} file={path},line={violation.lineno},endLine={violation.end_lineno},"
        f"title=Cyclomatic complexity::{message}"
    )


def render_annotations(
    violations: Sequence[Violation], enforcement: Enforcement, max_complexity: int
) -> list[str]:
    if os.environ.get("GITHUB_ACTIONS") != "true":
        return []
    level = "error" if enforcement == "fail" else "warning"
    return [_annotation(violation, level, max_complexity) for violation in violations]


# --- CLI ------------------------------------------------------------------------------------


def _resolve_mode(
    repo_root: Path, *, staged: bool, base: str | None
) -> tuple[str, Callable[[str], str | None]]:
    if staged:
        return "HEAD", _index_reader(repo_root)
    assert base is not None
    resolved = merge_base(repo_root, base)
    if resolved is None:
        raise GitFailure(f"unable to resolve a base revision from {base!r}")
    return resolved, _working_tree_reader(repo_root)


def _run(repo_root: Path, *, staged: bool, base: str | None) -> int:
    base_rev, head_source_for = _resolve_mode(repo_root, staged=staged, base=base)
    base_source_for = _revision_reader(repo_root, base_rev)
    policy = load_policy(head_source_for)
    exemption_problems = validate_exemptions(policy, head_source_for)
    if exemption_problems:
        for problem in exemption_problems:
            print(problem, file=sys.stderr)
        return 2
    changes = changed_files(repo_root, staged=staged, base_rev=base_rev)
    violations = evaluate(changes, head_source_for, base_source_for, policy)
    if not violations:
        return 0
    for line in render_annotations(violations, ENFORCEMENT, policy.max_complexity):
        print(line)
    print(render_report(violations, ENFORCEMENT, policy))
    return 1 if ENFORCEMENT == "fail" else 0


def main(argv: list[str]) -> int:
    """Check the candidate against its base and return a shell-compatible status."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--staged", action="store_true", help="Compare the staged index against HEAD."
    )
    parser.add_argument(
        "--base", help="Compare the working tree against the merge base of HEAD and this ref."
    )
    parser.add_argument(
        "--repo-root",
        default=str(PROJECT_ROOT),
        help="Repository to check; required when this script runs from a copy outside it.",
    )
    args = parser.parse_args(argv)
    if args.staged == bool(args.base):
        print("Specify exactly one of --staged or --base REF.", file=sys.stderr)
        return 2
    repo_root = Path(args.repo_root).resolve()
    try:
        return _run(repo_root, staged=args.staged, base=args.base)
    except (PolicyUnavailable, GitFailure) as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
