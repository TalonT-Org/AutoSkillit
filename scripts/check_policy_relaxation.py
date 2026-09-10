#!/usr/bin/env python3
# autoskillit: policy-authority -- repository acceptance policy enforcement.
"""Fail any change that loosens a registered acceptance-policy surface without approval.

Every surface value is read by AST from two revisions -- the trusted base and the
candidate -- and never executed. A difference that raises what the repository will
accept needs a PolicyRelaxationApproval introduced by the same diff; a difference
that lowers it is always free.

The surface registry itself is read from the base revision, so narrowing the
registry is checked the same way any other relaxation is.
"""

from __future__ import annotations

import argparse
import ast
import dataclasses
import subprocess
import sys
from collections.abc import Callable, Iterable
from pathlib import Path

SURFACES_PATH = "tests/arch/_acceptance_policy_surfaces.py"
HUMAN_REQUIRED_MARKER = "AUTOSKILLIT_HUMAN_REQUIRED:"
_SURFACE_KINDS = ("int_scalar", "int_map", "exemption_map")
_GIT_TIMEOUT_SECONDS = 10


class UnsupportedSurfaceShape(Exception):
    """A declared surface is not written in a shape the checker can read."""


class SurfaceMissing(Exception):
    """A declared symbol has no module-level assignment in the given source."""


@dataclasses.dataclass(frozen=True)
class PolicySurface:
    path: str
    symbol: str
    kind: str
    default: int | None = None


@dataclasses.dataclass(frozen=True)
class PolicyRelaxationApproval:
    path: str
    symbol: str
    key: str | None
    before: str
    after: str
    issue: int
    approved_by: str


@dataclasses.dataclass(frozen=True)
class SurfaceValue:
    limit: int
    predicate_source: str | None = None


@dataclasses.dataclass(frozen=True)
class Relaxation:
    path: str
    symbol: str
    key: str | None
    before: str
    after: str
    reason: str


@dataclasses.dataclass(frozen=True)
class RegistrySnapshot:
    surfaces: tuple[PolicySurface, ...]
    authority_paths: tuple[str, ...]
    reviewed_paths: tuple[str, ...]


def _module_assignment(source: str, symbol: str) -> ast.expr:
    """Return the value expression of the module-level assignment to *symbol*."""
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise UnsupportedSurfaceShape(f"{symbol}: source does not parse ({exc})") from exc
    for node in tree.body:
        if isinstance(node, ast.Assign):
            targets = node.targets
            if len(targets) == 1 and isinstance(targets[0], ast.Name) and targets[0].id == symbol:
                return node.value
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name) and node.target.id == symbol and node.value:
                return node.value
    raise SurfaceMissing(f"{symbol}: no module-level assignment")


def _constant(node: ast.expr, want: type, context: str) -> object:
    if not isinstance(node, ast.Constant) or not isinstance(node.value, want):
        raise UnsupportedSurfaceShape(f"{context}: expected a literal {want.__name__}")
    return node.value


def _int_constant(node: ast.expr, context: str) -> int:
    value = _constant(node, int, context)
    if isinstance(value, bool):
        raise UnsupportedSurfaceShape(f"{context}: expected a literal int")
    assert isinstance(value, int)
    return value


def _str_constant(node: ast.expr, context: str) -> str:
    value = _constant(node, str, context)
    assert isinstance(value, str)
    return value


def _dict_node(node: ast.expr, context: str) -> ast.Dict:
    if not isinstance(node, ast.Dict):
        raise UnsupportedSurfaceShape(f"{context}: expected a dict literal")
    return node


def _exemption_value(node: ast.expr, context: str) -> SurfaceValue:
    if not isinstance(node, ast.Call):
        raise UnsupportedSurfaceShape(f"{context}: expected an exemption constructor call")
    limit: int | None = None
    if node.args:
        limit = _int_constant(node.args[0], context)
    predicate_source: str | None = None
    for keyword in node.keywords:
        if keyword.arg == "limit":
            limit = _int_constant(keyword.value, context)
        elif keyword.arg == "predicate":
            predicate_source = ast.unparse(keyword.value)
    if limit is None:
        raise UnsupportedSurfaceShape(f"{context}: exemption has no literal limit")
    return SurfaceValue(limit=limit, predicate_source=predicate_source)


def extract_surface_values(source: str, surface: PolicySurface) -> dict[str | None, SurfaceValue]:
    """Read one surface's accepted values out of *source* without executing it."""
    context = f"{surface.path}::{surface.symbol}"
    value = _module_assignment(source, surface.symbol)
    if surface.kind == "int_scalar":
        return {None: SurfaceValue(limit=_int_constant(value, context))}
    mapping = _dict_node(value, context)
    entries: dict[str | None, SurfaceValue] = {}
    for key_node, value_node in zip(mapping.keys, mapping.values):
        if key_node is None:
            raise UnsupportedSurfaceShape(f"{context}: dict unpacking is not readable")
        key = _str_constant(key_node, context)
        if surface.kind == "int_map":
            entries[key] = SurfaceValue(limit=_int_constant(value_node, context))
        else:
            entries[key] = _exemption_value(value_node, context)
    return entries


def render(value: SurfaceValue | None, kind: str) -> str:
    """Render one value exactly as an approval must quote it."""
    if value is None:
        return "absent"
    if kind == "exemption_map":
        return f"limit={value.limit} predicate={value.predicate_source}"
    return str(value.limit)


def _string_tuple(
    node: ast.expr, context: str, resolved: dict[str, tuple[str, ...]]
) -> tuple[str, ...]:
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = node.left
        if not isinstance(left, ast.Name) or left.id not in resolved:
            raise UnsupportedSurfaceShape(f"{context}: unresolvable tuple concatenation")
        return resolved[left.id] + _string_tuple(node.right, context, resolved)
    if not isinstance(node, ast.Tuple):
        raise UnsupportedSurfaceShape(f"{context}: expected a tuple literal")
    return tuple(_str_constant(element, context) for element in node.elts)


def _surface_row(node: ast.expr, context: str) -> PolicySurface:
    if not isinstance(node, ast.Call):
        raise UnsupportedSurfaceShape(f"{context}: expected a PolicySurface(...) call")
    positional = ["path", "symbol", "kind", "default"]
    fields: dict[str, object] = {}
    for name, arg in zip(positional, node.args):
        fields[name] = (
            _int_constant(arg, context) if name == "default" else _str_constant(arg, context)
        )
    for keyword in node.keywords:
        if keyword.arg is None or keyword.arg not in positional:
            raise UnsupportedSurfaceShape(f"{context}: unexpected surface argument")
        if keyword.arg == "default":
            fields["default"] = _int_constant(keyword.value, context)
        else:
            fields[keyword.arg] = _str_constant(keyword.value, context)
    for name in ("path", "symbol", "kind"):
        if name not in fields:
            raise UnsupportedSurfaceShape(f"{context}: surface is missing {name}")
    kind = fields["kind"]
    if kind not in _SURFACE_KINDS:
        raise UnsupportedSurfaceShape(f"{context}: unknown surface kind {kind!r}")
    default = fields.get("default")
    assert isinstance(fields["path"], str)
    assert isinstance(fields["symbol"], str)
    assert isinstance(kind, str)
    assert default is None or isinstance(default, int)
    return PolicySurface(fields["path"], fields["symbol"], kind, default)


def _approval_row(node: ast.expr, context: str) -> PolicyRelaxationApproval:
    if not isinstance(node, ast.Call):
        raise UnsupportedSurfaceShape(f"{context}: expected a PolicyRelaxationApproval(...) call")
    names = ["path", "symbol", "key", "before", "after", "issue", "approved_by"]
    fields: dict[str, object] = {}
    for name, arg in zip(names, node.args):
        fields[name] = _approval_field(name, arg, context)
    for keyword in node.keywords:
        if keyword.arg is None or keyword.arg not in names:
            raise UnsupportedSurfaceShape(f"{context}: unexpected approval argument")
        fields[keyword.arg] = _approval_field(keyword.arg, keyword.value, context)
    if set(fields) != set(names):
        raise UnsupportedSurfaceShape(f"{context}: approval is missing fields")
    key = fields["key"]
    issue = fields["issue"]
    assert key is None or isinstance(key, str)
    assert isinstance(issue, int)
    return PolicyRelaxationApproval(
        path=str(fields["path"]),
        symbol=str(fields["symbol"]),
        key=key,
        before=str(fields["before"]),
        after=str(fields["after"]),
        issue=issue,
        approved_by=str(fields["approved_by"]),
    )


def _approval_field(name: str, node: ast.expr, context: str) -> object:
    if name == "issue":
        return _int_constant(node, context)
    if name == "key" and isinstance(node, ast.Constant) and node.value is None:
        return None
    return _str_constant(node, context)


def extract_registry(source: str) -> RegistrySnapshot:
    """Read the surface registry and the code-owned path lists out of *source*."""
    context = SURFACES_PATH
    surfaces_node = _module_assignment(source, "POLICY_SURFACES")
    if not isinstance(surfaces_node, ast.Tuple):
        raise UnsupportedSurfaceShape(f"{context}: POLICY_SURFACES must be a tuple literal")
    surfaces = tuple(_surface_row(element, context) for element in surfaces_node.elts)
    authority = _string_tuple(_module_assignment(source, "POLICY_AUTHORITY_PATHS"), context, {})
    reviewed = _string_tuple(
        _module_assignment(source, "CODEOWNER_REVIEWED_PATHS"),
        context,
        {"POLICY_AUTHORITY_PATHS": authority},
    )
    return RegistrySnapshot(surfaces, authority, reviewed)


def extract_approvals(source: str) -> tuple[PolicyRelaxationApproval, ...]:
    """Read the approval ledger out of *source*."""
    node = _module_assignment(source, "POLICY_RELAXATION_APPROVALS")
    if not isinstance(node, ast.Tuple):
        raise UnsupportedSurfaceShape(f"{SURFACES_PATH}: approvals must be a tuple literal")
    return tuple(_approval_row(element, SURFACES_PATH) for element in node.elts)


def _registry_relaxation(
    symbol: str, key: str, before: str, after: str, reason: str
) -> Relaxation:
    return Relaxation(SURFACES_PATH, symbol, key, before, after, reason)


def _render_row(surface: PolicySurface) -> str:
    return f"kind={surface.kind} default={surface.default}"


def classify_registry(base: RegistrySnapshot, head: RegistrySnapshot) -> list[Relaxation]:
    """Report every way the candidate narrowed what the gate itself covers."""
    relaxations: list[Relaxation] = []
    head_rows = {(row.path, row.symbol): row for row in head.surfaces}
    for row in base.surfaces:
        key = f"{row.path}::{row.symbol}"
        current = head_rows.get((row.path, row.symbol))
        if current is None:
            relaxations.append(
                _registry_relaxation(
                    "POLICY_SURFACES", key, _render_row(row), "absent", "surface removed"
                )
            )
            continue
        if current.kind != row.kind:
            relaxations.append(
                _registry_relaxation(
                    "POLICY_SURFACES",
                    key,
                    _render_row(row),
                    _render_row(current),
                    "surface kind changed",
                )
            )
            continue
        if row.default is None and current.default is not None:
            relaxations.append(
                _registry_relaxation(
                    "POLICY_SURFACES", key, _render_row(row), _render_row(current), "default added"
                )
            )
        elif (
            row.default is not None
            and current.default is not None
            and current.default > row.default
        ):
            relaxations.append(
                _registry_relaxation(
                    "POLICY_SURFACES",
                    key,
                    _render_row(row),
                    _render_row(current),
                    "default raised",
                )
            )
    for symbol, base_paths, head_paths in (
        ("POLICY_AUTHORITY_PATHS", base.authority_paths, head.authority_paths),
        ("CODEOWNER_REVIEWED_PATHS", base.reviewed_paths, head.reviewed_paths),
    ):
        for path in base_paths:
            if path not in head_paths:
                relaxations.append(
                    _registry_relaxation(symbol, path, path, "absent", "authority path removed")
                )
    return relaxations


def classify(
    base: dict[str | None, SurfaceValue],
    head: dict[str | None, SurfaceValue],
    surface: PolicySurface,
) -> list[Relaxation]:
    """Report every difference that raises what *surface* accepts."""
    relaxations: list[Relaxation] = []
    for key in sorted(set(base) | set(head), key=lambda item: (item is not None, item or "")):
        before, after = base.get(key), head.get(key)
        reason = _classify_entry(before, after, surface)
        if reason is not None:
            relaxations.append(
                Relaxation(
                    surface.path,
                    surface.symbol,
                    key,
                    render(before, surface.kind),
                    render(after, surface.kind),
                    reason,
                )
            )
    return relaxations


def _classify_entry(
    before: SurfaceValue | None,
    after: SurfaceValue | None,
    surface: PolicySurface,
) -> str | None:
    if before is None and after is None:
        return None
    if before is None:
        assert after is not None
        if surface.kind == "exemption_map":
            return "entry added"
        if surface.default is not None and after.limit > surface.default:
            return "entry added above default"
        return None
    if after is None:
        if surface.kind == "exemption_map":
            return None
        if surface.default is None:
            return "entry removed"
        if surface.default > before.limit:
            return "entry removed below default"
        return None
    if after.limit > before.limit:
        return "limit increased" if surface.kind == "exemption_map" else "value increased"
    if surface.kind == "exemption_map":
        if before.predicate_source is None and after.predicate_source is not None:
            return "predicate added"
        if (
            before.predicate_source is not None
            and after.predicate_source is not None
            and before.predicate_source != after.predicate_source
        ):
            return "predicate changed"
    return None


def unapproved(
    relaxations: Iterable[Relaxation],
    new_approvals: Iterable[PolicyRelaxationApproval],
) -> list[Relaxation]:
    """Drop every relaxation an approval introduced by this diff matches exactly."""
    approved = {
        (approval.path, approval.symbol, approval.key, approval.before, approval.after)
        for approval in new_approvals
    }
    return [
        relaxation
        for relaxation in relaxations
        if (
            relaxation.path,
            relaxation.symbol,
            relaxation.key,
            relaxation.before,
            relaxation.after,
        )
        not in approved
    ]


def _git(repo_root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        timeout=_GIT_TIMEOUT_SECONDS,
        check=False,
    )


def git_show(repo_root: Path, rev: str, path: str) -> str | None:
    """Return one file's content at *rev*, or None when it does not exist there."""
    result = _git(repo_root, "show", f"{rev}:{path}")
    return result.stdout if result.returncode == 0 else None


def merge_base(repo_root: Path, ref: str) -> str | None:
    """Return the merge base of HEAD and *ref*, or None when git cannot resolve it."""
    result = _git(repo_root, "merge-base", "HEAD", ref)
    return result.stdout.strip() or None if result.returncode == 0 else None


def _diagnostic(relaxation: Relaxation) -> str:
    location = f"{relaxation.path}::{relaxation.symbol}"
    if relaxation.key is not None:
        location = f"{location}[{relaxation.key}]"
    return (
        f"{HUMAN_REQUIRED_MARKER} policy relaxation {location} "
        f"{relaxation.before} -> {relaxation.after} ({relaxation.reason}) has no approval.\n"
        "  Automated repair must not change this value; satisfy the constraint (decompose or "
        "reorganize so every applicable size and organization rule holds). A human records "
        "PolicyRelaxationApproval(path, symbol, key, before, after, issue=#NNNN, approved_by) "
        f"in {SURFACES_PATH}; the diff then needs code-owner review."
    )


def evaluate(
    repo_root: Path,
    base_rev: str,
    head_source_for: Callable[[str], str | None],
) -> list[str]:
    """Return one diagnostic per unapproved relaxation, or per fail-closed condition."""
    head_surfaces_source = head_source_for(SURFACES_PATH)
    if head_surfaces_source is None:
        return [f"{HUMAN_REQUIRED_MARKER} {SURFACES_PATH} is missing at HEAD."]
    base_surfaces_source = git_show(repo_root, base_rev, SURFACES_PATH)
    try:
        head_registry = extract_registry(head_surfaces_source)
        base_registry = (
            extract_registry(base_surfaces_source)
            if base_surfaces_source is not None
            else RegistrySnapshot((), (), ())
        )
        head_approvals = extract_approvals(head_surfaces_source)
        base_approvals = (
            extract_approvals(base_surfaces_source) if base_surfaces_source is not None else ()
        )
    except (UnsupportedSurfaceShape, SurfaceMissing) as exc:
        return [f"{HUMAN_REQUIRED_MARKER} the surface registry is unreadable: {exc}"]

    relaxations = classify_registry(base_registry, head_registry)
    diagnostics: list[str] = []
    for surface in _surfaces_to_check(base_registry, head_registry):
        surface_relaxations, surface_diagnostics = _check_surface(
            repo_root, base_rev, surface, head_source_for
        )
        relaxations.extend(surface_relaxations)
        diagnostics.extend(surface_diagnostics)

    new_approvals = [
        approval for approval in head_approvals if approval not in set(base_approvals)
    ]
    diagnostics.extend(_diagnostic(item) for item in unapproved(relaxations, new_approvals))
    return diagnostics


def _surfaces_to_check(
    base_registry: RegistrySnapshot,
    head_registry: RegistrySnapshot,
) -> list[PolicySurface]:
    """Every surface either revision declares; the base's declaration wins."""
    surfaces = {(row.path, row.symbol): row for row in head_registry.surfaces}
    surfaces.update({(row.path, row.symbol): row for row in base_registry.surfaces})
    return list(surfaces.values())


def _check_surface(
    repo_root: Path,
    base_rev: str,
    surface: PolicySurface,
    head_source_for: Callable[[str], str | None],
) -> tuple[list[Relaxation], list[str]]:
    location = f"{surface.path}::{surface.symbol}"
    head_source = head_source_for(surface.path)
    if head_source is None:
        return [], [f"{HUMAN_REQUIRED_MARKER} registered surface {location} is missing at HEAD."]
    base_source = git_show(repo_root, base_rev, surface.path)
    try:
        head_values = extract_surface_values(head_source, surface)
    except SurfaceMissing:
        return [], [f"{HUMAN_REQUIRED_MARKER} registered surface {location} is missing at HEAD."]
    except UnsupportedSurfaceShape as exc:
        return [], [f"{HUMAN_REQUIRED_MARKER} registered surface {location} is unreadable: {exc}"]
    if base_source is None:
        return [], []
    try:
        base_values = extract_surface_values(base_source, surface)
    except SurfaceMissing:
        return [], []
    except UnsupportedSurfaceShape as exc:
        return [], [
            f"{HUMAN_REQUIRED_MARKER} registered surface {location} is unreadable at the base "
            f"revision: {exc}"
        ]
    return classify(base_values, head_values, surface), []


def _working_tree_reader(repo_root: Path) -> Callable[[str], str | None]:
    def read(path: str) -> str | None:
        candidate = repo_root / path
        if not candidate.is_file():
            return None
        return candidate.read_text(encoding="utf-8")

    return read


def _index_reader(repo_root: Path) -> Callable[[str], str | None]:
    def read(path: str) -> str | None:
        result = _git(repo_root, "show", f":{path}")
        return result.stdout if result.returncode == 0 else None

    return read


def main(argv: list[str]) -> int:
    """Check the candidate against its base and return a shell-compatible status."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", help="Compare against the merge base of HEAD and this ref.")
    parser.add_argument(
        "--staged",
        action="store_true",
        help="Compare the staged index against HEAD.",
    )
    parser.add_argument(
        "--repo-root",
        default=str(Path(__file__).resolve().parent.parent),
        help="Repository to check; required when this script runs from a copy outside it.",
    )
    args = parser.parse_args(argv)
    repo_root = Path(args.repo_root).resolve()

    if args.staged:
        base_rev: str | None = "HEAD"
        head_source_for = _index_reader(repo_root)
    elif args.base:
        base_rev = merge_base(repo_root, args.base)
        head_source_for = _working_tree_reader(repo_root)
    else:
        print("Specify --base REF or --staged.", file=sys.stderr)
        return 1

    if base_rev is None:
        print(f"Unable to resolve a base revision from {args.base!r}.", file=sys.stderr)
        return 1

    try:
        diagnostics = evaluate(repo_root, base_rev, head_source_for)
    except subprocess.TimeoutExpired:
        print("git timed out while reading the base revision.", file=sys.stderr)
        return 1
    if diagnostics:
        print("Acceptance-policy relaxations require human approval:\n")
        for diagnostic in diagnostics:
            print(diagnostic)
        print(f"\nTotal: {len(diagnostics)} unapproved relaxation(s)")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
