"""Architectural invariant: every env-var-set constant must have a production consumer."""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

_ENV_CANONICAL_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]*(?:_ENV_FORWARD_VARS|_REQUIRED_ENV)$")


def _find_env_set_constants(constants_file: Path) -> list[str]:
    """Find all module-level names matching env-var-set patterns in the constants file."""
    tree = ast.parse(constants_file.read_text())
    names: list[str] = []
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if _ENV_CANONICAL_PATTERN.match(node.target.id):
                names.append(node.target.id)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and _ENV_CANONICAL_PATTERN.match(target.id):
                    names.append(target.id)
    return names


def _has_production_import(src_root: Path, constant_name: str, definition_file: Path) -> bool:
    """Check if any production file (excluding the definition) imports the constant."""
    for py_file in src_root.rglob("*.py"):
        if py_file == definition_file:
            continue
        if py_file.name.startswith("test_"):
            continue
        try:
            tree = ast.parse(py_file.read_text())
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    actual_name = alias.asname if alias.asname else alias.name
                    if actual_name == constant_name or alias.name == constant_name:
                        return True
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == constant_name:
                        return True
    return False


def test_env_forward_constants_have_production_consumer() -> None:
    """Every env-var-set constant must be imported by at least one production module."""
    from autoskillit.core import paths

    src_root = paths.pkg_root()
    constants_file = src_root / "core" / "types" / "_type_constants_env.py"
    constants = _find_env_set_constants(constants_file)
    assert constants, "No env-var-set constants found — test premise broken"

    unconsumed = [
        name for name in constants if not _has_production_import(src_root, name, constants_file)
    ]
    assert not unconsumed, (
        f"Env-var-set constants (*_ENV_FORWARD_VARS / *_REQUIRED_ENV) with zero production "
        f"consumers: {unconsumed}. Each env-var-set constant must be imported and consumed "
        f"by production code to prevent dead-canonical-constant drift."
    )


_REGISTRY_CANONICAL_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]*(?:_REGISTRY|_TOOLS|_TAGS|_NAMES)$")

_REGISTRY_EXEMPTIONS: dict[str, str] = {
    "FREE_RANGE_TOOLS": (
        "alias-derived: backing constant for UNGATED_TOOLS which is imported "
        "in pipeline/gate.py and server/tools/tools_recipe.py; "
        "direct import would be redundant"
    ),
    "FLEET_TOOLS": (
        "test-consumed: architectural enforcement constant imported by "
        "test_layer_enforcement.py, test_transforms_hygiene.py, and "
        "test_lifespan_fleet_boot.py for fleet tool-tag parity guards; "
        "no runtime production consumer needed"
    ),
}


def _find_registry_constants(constants_file: Path) -> list[str]:
    """Find all module-level names matching registry/tools/tags/names patterns."""
    tree = ast.parse(constants_file.read_text())
    names: list[str] = []
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if _REGISTRY_CANONICAL_PATTERN.match(node.target.id):
                names.append(node.target.id)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and _REGISTRY_CANONICAL_PATTERN.match(target.id):
                    names.append(target.id)
    return names


def test_registry_constants_have_production_consumer() -> None:
    """Every registry/tools/tags/names constant must be imported by production code or exempted."""
    from autoskillit.core import paths

    src_root = paths.pkg_root()
    constants_files = [
        src_root / "core" / "types" / "_type_constants_registries.py",
        src_root / "core" / "types" / "_type_constants.py",
    ]

    all_constants: list[tuple[str, Path]] = []
    for cf in constants_files:
        for name in _find_registry_constants(cf):
            all_constants.append((name, cf))

    assert all_constants, "No registry constants found — test premise broken"

    unconsumed = []
    for name, def_file in all_constants:
        if name in _REGISTRY_EXEMPTIONS:
            continue
        if not _has_production_import(src_root, name, def_file):
            unconsumed.append(name)

    assert not unconsumed, (
        f"Registry constants (*_REGISTRY / *_TOOLS / *_TAGS / *_NAMES) with zero "
        f"production consumers and no exemption: {unconsumed}. Each constant must be "
        f"imported by production src/ code or documented in _REGISTRY_EXEMPTIONS "
        f"with a rationale."
    )


def test_exemption_rationales_are_nonempty() -> None:
    """Every exemption must have a non-empty rationale string."""
    empty = [k for k, v in _REGISTRY_EXEMPTIONS.items() if not v.strip()]
    assert not empty, f"Exemptions with empty rationales: {empty}"


def test_exemptions_reference_real_constants() -> None:
    """Every exempted name must exist as a constant in the scanned files."""
    from autoskillit.core import paths

    src_root = paths.pkg_root()
    constants_files = [
        src_root / "core" / "types" / "_type_constants_registries.py",
        src_root / "core" / "types" / "_type_constants.py",
    ]
    all_names: set[str] = set()
    for cf in constants_files:
        all_names.update(_find_registry_constants(cf))

    stale = set(_REGISTRY_EXEMPTIONS.keys()) - all_names
    assert not stale, (
        f"Exemption dict contains names not found in constants files: {sorted(stale)}. "
        f"Remove stale entries."
    )


def test_fleet_dispatch_tools_subset_of_gated_tools() -> None:
    """FLEET_DISPATCH_TOOLS must be a subset of GATED_TOOLS."""
    from autoskillit.core import FLEET_DISPATCH_TOOLS
    from autoskillit.pipeline import GATED_TOOLS

    extra = FLEET_DISPATCH_TOOLS - GATED_TOOLS
    assert not extra, (
        f"FLEET_DISPATCH_TOOLS must be a subset of GATED_TOOLS — extra: {sorted(extra)}"
    )
