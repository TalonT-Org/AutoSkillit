"""AST ratchet for server-owned audit-authority construction and serialization."""

from __future__ import annotations

import ast
from collections import defaultdict
from pathlib import Path
from typing import NamedTuple

import pytest

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SOURCE_ROOT = _REPO_ROOT / "src" / "autoskillit"


class AuthorityOwnershipSite(NamedTuple):
    operation: str
    relative_path: str
    lineno: int


_ALLOWED_PATHS_BY_OPERATION = {
    "canonical_bytes": frozenset({"src/autoskillit/server/_audit_authority_materializer.py"}),
    "constructor": frozenset(),
    "create": frozenset({"src/autoskillit/server/_audit_authority_materializer.py"}),
    "from_dict": frozenset({"src/autoskillit/core/audit_cycle_verifier.py"}),
    "replace": frozenset(),
}


def _qualified_tail(expr: ast.expr) -> str | None:
    if isinstance(expr, ast.Name):
        return expr.id
    if isinstance(expr, ast.Attribute):
        return expr.attr
    return None


def _looks_like_authority(expr: ast.expr) -> bool:
    tail = _qualified_tail(expr)
    return tail is not None and "authority" in tail.lower()


def _scan_source(source: str, relative_path: str) -> set[AuthorityOwnershipSite]:
    tree = ast.parse(source, filename=relative_path)
    sites: set[AuthorityOwnershipSite] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            if node.attr == "canonical_bytes" and _looks_like_authority(node.value):
                sites.add(AuthorityOwnershipSite("canonical_bytes", relative_path, node.lineno))
            continue
        if not isinstance(node, ast.Call):
            continue

        func = node.func
        if _qualified_tail(func) == "AuditCycleAuthority":
            sites.add(AuthorityOwnershipSite("constructor", relative_path, node.lineno))
            continue
        if isinstance(func, ast.Attribute):
            owner = _qualified_tail(func.value)
            if owner == "AuditCycleAuthority" and func.attr in {"create", "from_dict"}:
                sites.add(AuthorityOwnershipSite(func.attr, relative_path, node.lineno))
                continue
        if (
            _qualified_tail(func) == "replace"
            and node.args
            and _looks_like_authority(node.args[0])
        ):
            sites.add(AuthorityOwnershipSite("replace", relative_path, node.lineno))
    return sites


def _scan_production_sites() -> set[AuthorityOwnershipSite]:
    sites: set[AuthorityOwnershipSite] = set()
    for source_path in _SOURCE_ROOT.rglob("*.py"):
        relative_path = str(source_path.relative_to(_REPO_ROOT))
        sites.update(_scan_source(source_path.read_text(), relative_path))
    return sites


def _assert_only_sanctioned_sites(sites: set[AuthorityOwnershipSite]) -> None:
    actual: defaultdict[str, set[str]] = defaultdict(set)
    for site in sites:
        actual[site.operation].add(site.relative_path)

    violations: list[str] = []
    for operation, allowed_paths in _ALLOWED_PATHS_BY_OPERATION.items():
        found_paths = actual[operation]
        if found_paths != allowed_paths:
            violations.append(
                f"{operation}: expected {sorted(allowed_paths)}, found {sorted(found_paths)}"
            )
    unknown_operations = set(actual) - set(_ALLOWED_PATHS_BY_OPERATION)
    if unknown_operations:
        violations.append(f"unknown operations: {sorted(unknown_operations)}")
    assert not violations, "\n".join(violations)


def test_only_sanctioned_modules_construct_or_serialize_audit_authority() -> None:
    _assert_only_sanctioned_sites(_scan_production_sites())


def test_synthetic_alternate_authority_producer_fails_ratchet() -> None:
    synthetic_source = """
def forge(authority):
    AuditCycleAuthority()
    AuditCycleAuthority.create()
    AuditCycleAuthority.from_dict({})
    replace(authority)
    return authority.canonical_bytes
"""
    synthetic_path = "src/autoskillit/server/tools/tools_forged_authority.py"
    synthetic_sites = _scan_source(synthetic_source, synthetic_path)

    assert {site.operation for site in synthetic_sites} == set(_ALLOWED_PATHS_BY_OPERATION)
    with pytest.raises(AssertionError, match="tools_forged_authority"):
        _assert_only_sanctioned_sites(_scan_production_sites() | synthetic_sites)
