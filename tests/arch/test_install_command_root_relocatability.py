"""Architectural guard: install-command construction may never hardcode the
shared, non-versioned uv tool root.

Issue #4597's Phase 3 (immutable version-addressed install roots) makes every
install destination a caller-supplied, per-generation store path rather than
the single shared uv tool root ``uv tool install`` defaults to. C-7 promoted
the shared test-only environment-pinned-path denylist. This test
(T-C10) closes the corresponding regression path: a future edit to
``cli/install/_install_info.py``'s ``upgrade_command()`` (or any other
install-command construction site) that hand-builds a
``UV_TOOL_DIR``/``UV_TOOL_BIN_DIR`` value naming the shared, non-versioned
root instead of deriving it from ``install_root_destination`` would silently
reintroduce the single-shared-root hazard Phase 3 eliminated.

Pattern: ``ast.walk()`` over each known install-command-construction file,
inspecting every string constant for one of the environment-pinned-path
segments. Mirrors ``test_maintenance_install_argv_contract.py``'s mechanism
(a standalone scanner, following the ARCH-007/ARCH-011 precedent for rules
that don't fit the shared ``ArchitectureViolationVisitor``).

Residual gap: this is a purely syntactic string-literal scanner, exactly like
its sibling in ``test_maintenance_install_argv_contract.py``. It flags a
literal only when a single string constant contains a forbidden segment; a
hand-built path assembled from multiple separately-literal segments (e.g.
``Path.home() / "uv" / "tools" / "autoskillit"``, where no single literal
contains the substring ``"uv/tools"``) would defeat this scan. That is an
accepted residual gap, not something this test attempts to close.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tests.contracts._relocatability_helpers import environment_pinned_path_segments

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

_REPO_ROOT = Path(__file__).resolve().parents[2]

# Every known install-command-construction site: the function(s) that build
# the argv/env values ultimately passed to a child `uv tool install`/`uv tool
# upgrade` process. See the module docstring for what this guard protects
# against.
_INSTALL_COMMAND_CONSTRUCTION_FILES: tuple[Path, ...] = (
    _REPO_ROOT / "src/autoskillit/cli/install/_install_info.py",
    _REPO_ROOT / "src/autoskillit/cli/update/_transaction.py",
)


def _scan_for_forbidden_path_literals(
    tree: ast.AST, forbidden_segments: tuple[str, ...]
) -> list[str]:
    """Return "line: literal" violations for string constants containing a
    forbidden environment-pinned path segment.
    """
    violations: list[str] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
            continue
        for segment in forbidden_segments:
            if segment and segment in node.value:
                violations.append(f"line {node.lineno}: {node.value!r} contains {segment!r}")
                break
    return violations


def test_scan_detects_forbidden_path_literal() -> None:
    tree = ast.parse('argv = "uv/tools/autoskillit"')
    violations = _scan_for_forbidden_path_literals(tree, ("uv/tools",))
    assert len(violations) == 1
    assert "uv/tools" in violations[0]


def test_scan_ignores_unrelated_literals() -> None:
    tree = ast.parse('argv = ["uv", "tool", "install", "--force"]')
    violations = _scan_for_forbidden_path_literals(tree, ("uv/tools",))
    assert violations == []


def test_no_install_command_targets_a_shared_mutable_root() -> None:
    """No install-command-construction site may hand-build a string literal
    naming the shared, non-versioned uv tool root.

    ``upgrade_command()`` builds its ``UV_TOOL_DIR``/``UV_TOOL_BIN_DIR``
    values dynamically from the caller-supplied ``install_root_destination``
    (a per-generation store path), never from a hardcoded shared-root
    literal — this test is a fitness function, not a fix: it exists to keep
    that property true through future edits, not because a violation exists
    today.
    """
    forbidden_segments = environment_pinned_path_segments()
    violations: list[str] = []
    for py_file in _INSTALL_COMMAND_CONSTRUCTION_FILES:
        assert py_file.is_file(), f"expected install-command-construction file to exist: {py_file}"
        source = py_file.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(py_file))
        violations.extend(
            f"{py_file}:{v}" for v in _scan_for_forbidden_path_literals(tree, forbidden_segments)
        )
    assert not violations, (
        "Install-command construction must derive every destination from the "
        "per-generation store (install_root_destination), never hardcode the "
        "shared uv tool root:\n" + "\n".join(violations)
    )
