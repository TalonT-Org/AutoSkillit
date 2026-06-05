"""Architectural invariant: every BackendCapabilities field must be consumed in production."""

from __future__ import annotations

import ast
import dataclasses
from datetime import date
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]


@dataclasses.dataclass(frozen=True)
class ForwardDeclaredField:
    """Structured forward-declaration for a BackendCapabilities field without a consumer."""

    issue: int
    rationale: str
    added_date: date


_STALENESS_THRESHOLD_DAYS = 180

_FORWARD_DECLARED: dict[str, ForwardDeclaredField] = {
    "supports_thinking_blocks": ForwardDeclaredField(
        issue=3497,
        rationale="thinking-block rendering gating",
        added_date=date(2026, 5, 31),
    ),
    "supports_context_exhaustion_detection": ForwardDeclaredField(
        issue=3384,
        rationale="context exhaustion and recovery paths",
        added_date=date(2026, 5, 31),
    ),
    "min_version": ForwardDeclaredField(
        issue=3122,
        rationale="version validation via BackendCapabilities fields",
        added_date=date(2026, 5, 31),
    ),
    "mcp_env_forward_vars": ForwardDeclaredField(
        issue=3458,
        rationale="MCP env forwarding — enforcement arch test exists, awaiting src/ consumer",
        added_date=date(2026, 5, 31),
    ),
    "inspector_capable": ForwardDeclaredField(
        issue=3533,
        rationale="Health Inspector capability gating — production consumer in #3574",
        added_date=date(2026, 6, 1),
    ),
    "required_session_files": ForwardDeclaredField(
        issue=3134,
        rationale=(
            "production consumer moved to CodexBackend.setup_session_dir — "
            "field retained for validate_session_layout"
        ),
        added_date=date(2026, 6, 2),
    ),
    "session_dir_symlinks": ForwardDeclaredField(
        issue=3134,
        rationale=(
            "production consumer moved to CodexBackend.setup_session_dir — "
            "field retained for validate_session_layout"
        ),
        added_date=date(2026, 6, 2),
    ),
    "patch_format": ForwardDeclaredField(
        issue=3776,
        rationale="patch path extraction routing — P2-A3-WP1 (#3787) co-lands consumer",
        added_date=date(2026, 6, 5),
    ),
}


def _collect_attribute_reads(src_root: Path, field_names: frozenset[str]) -> dict[str, list[str]]:
    """Scan src/ for .field_name attribute access, excluding definition file."""
    reads: dict[str, list[str]] = {name: [] for name in field_names}
    definition_file = src_root / "core" / "types" / "_type_backend.py"
    for py_file in src_root.rglob("*.py"):
        if py_file == definition_file:
            continue
        relpath = str(py_file.relative_to(src_root))
        try:
            tree = ast.parse(py_file.read_text())
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr in field_names:
                reads[node.attr].append(f"{relpath}:{node.lineno}")
    return reads


def test_all_capability_fields_have_production_consumers():
    """Every BackendCapabilities field must be read somewhere in src/ (excluding definition)."""
    from autoskillit.core import BackendCapabilities, paths

    src_root = paths.pkg_root()
    field_names = frozenset(f.name for f in dataclasses.fields(BackendCapabilities))
    reads = _collect_attribute_reads(src_root, field_names)

    unconsumed = {
        name for name, sites in reads.items() if not sites and name not in _FORWARD_DECLARED
    }
    assert not unconsumed, (
        f"BackendCapabilities fields with zero production read sites "
        f"(add a consumer or add to _FORWARD_DECLARED as "
        f"ForwardDeclaredField(issue=NNNN, rationale='...', added_date=date(YYYY, M, D))): "
        f"{sorted(unconsumed)}"
    )


def test_forward_declared_has_linked_issues():
    """Every _FORWARD_DECLARED entry must have a positive issue number."""
    invalid = {
        field: entry.issue for field, entry in _FORWARD_DECLARED.items() if entry.issue <= 0
    }
    assert not invalid, (
        f"_FORWARD_DECLARED entries with invalid issue number (need positive int): {invalid}"
    )


def test_forward_declared_fields_have_no_consumers():
    """_FORWARD_DECLARED entries must NOT have production consumers.

    If a field gains a consumer in src/, it must be removed from
    _FORWARD_DECLARED — the exemption is no longer needed.
    """
    from autoskillit.core import BackendCapabilities, paths

    src_root = paths.pkg_root()
    field_names = frozenset(f.name for f in dataclasses.fields(BackendCapabilities))
    reads = _collect_attribute_reads(src_root, field_names)

    stale = {name: sites for name, sites in reads.items() if name in _FORWARD_DECLARED and sites}
    assert not stale, (
        f"_FORWARD_DECLARED entries that now have production consumers "
        f"(remove from _FORWARD_DECLARED): {stale}"
    )


def test_forward_declared_fields_exist_on_dataclass():
    """Every _FORWARD_DECLARED key must be a real field on BackendCapabilities."""
    from autoskillit.core import BackendCapabilities

    real_fields = frozenset(f.name for f in dataclasses.fields(BackendCapabilities))
    unknown = frozenset(_FORWARD_DECLARED.keys()) - real_fields
    assert not unknown, (
        f"_FORWARD_DECLARED keys that are not BackendCapabilities fields: {sorted(unknown)}"
    )


def test_forward_declared_entries_not_stale():
    """Time-bomb: forward-declared fields older than 180 days require re-justification.

    If a field has been forward-declared for > 180 days, either:
    - add a production consumer and remove from _FORWARD_DECLARED, or
    - update the added_date to reset the clock (with a current tracking issue)
    """
    today = date.today()
    stale = [
        f"{name} (added={entry.added_date}, age={(today - entry.added_date).days}d)"
        for name, entry in _FORWARD_DECLARED.items()
        if (today - entry.added_date).days > _STALENESS_THRESHOLD_DAYS
    ]
    assert not stale, (
        f"_FORWARD_DECLARED entries older than {_STALENESS_THRESHOLD_DAYS} days "
        f"(add a consumer, or update added_date with a fresh tracking issue): {stale}"
    )
