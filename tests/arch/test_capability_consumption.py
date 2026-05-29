"""Architectural invariant: every BackendCapabilities field must be consumed in production."""

import ast
import dataclasses
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

# Fields that are explicitly forward-declared and have no consumer yet.
# Adding a field here requires a linked issue number justifying the exception.
_FORWARD_DECLARED: frozenset[str] = frozenset(
    {
        "supports_thinking_blocks",  # planned for thinking-block rendering
        "supports_context_exhaustion_detection",  # planned for context exhaustion handling
        "mcp_config_capable",  # Codex sets True, planned for MCP config wiring
        "min_version",  # planned for version validation in doctor
        "version_check_command",  # planned for version validation in doctor
    }
)


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
        f"(add a consumer or add to _FORWARD_DECLARED with issue link): {sorted(unconsumed)}"
    )
