"""Architectural invariant: every BackendCapabilities field must be consumed in production."""

import ast
import dataclasses
import re
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

# Fields that are explicitly forward-declared and have no consumer yet.
# Every entry must reference a tracking issue: 'field_name': '#NNNN'.
_FORWARD_DECLARED: dict[str, str] = {
    "supports_thinking_blocks": "#3298",  # planned for thinking-block rendering
    "supports_context_exhaustion_detection": "#3299",  # planned for context exhaustion handling
    "min_version": "#3300",  # planned for version validation in doctor
    "version_check_command": "#3301",  # planned for version validation in doctor
}

_ISSUE_REF_RE = re.compile(r"#\d+")


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
        f"(add a consumer or add to _FORWARD_DECLARED as 'field_name': '#NNNN' dict entry): "
        f"{sorted(unconsumed)}"
    )


def test_forward_declared_has_linked_issues():
    """Every _FORWARD_DECLARED entry must have a linked issue reference matching #\\d+."""
    missing = {
        field: ref for field, ref in _FORWARD_DECLARED.items() if not _ISSUE_REF_RE.search(ref)
    }
    assert not missing, (
        f"_FORWARD_DECLARED entries missing issue reference (need '#NNNN'): {missing}"
    )


def test_supports_tool_list_changed_field_exists():
    """BackendCapabilities must have a supports_tool_list_changed bool field."""
    from autoskillit.core import BackendCapabilities

    field_names = {f.name for f in dataclasses.fields(BackendCapabilities)}
    assert "supports_tool_list_changed" in field_names
