"""REQ-CONFIG-IL-002: every new module under config/ may only import from
core/ + stdlib.

Mirrors the import-linter contract IL-002 (config/ may not import from
pipeline/execution/recipe/etc.). This contract enforces the same rule at the
per-file granularity the import-linter tool applies at the package level.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("contracts"), pytest.mark.small]

NEW_MODULES = [
    "_automation_config.py",
    "_coercion.py",
    "_coherence.py",
    "_retired_keys.py",
    "_validation.py",
    "_writer.py",
    "_dataclasses_errors.py",
    "_dataclasses_test_gating.py",
    "_dataclasses_execution.py",
    "_dataclasses_workflow.py",
    "_dataclasses_diagnostics.py",
    "_dataclasses_github.py",
    "_dataclasses_surfaces.py",
    "_dataclasses_fleet.py",
    "_dataclasses_providers.py",
]

FORBIDDEN_PACKAGES = {
    "autoskillit.pipeline",
    "autoskillit.execution",
    "autoskillit.workspace",
    "autoskillit.recipe",
    "autoskillit.migration",
    "autoskillit.server",
    "autoskillit.cli",
    "autoskillit.report",
    "autoskillit.planner",
    "autoskillit.fleet",
    "autoskillit.skills_extended",
    "autoskillit.hooks",
}


@pytest.mark.parametrize("module_name", NEW_MODULES)
def test_module_only_imports_allowed_packages(module_name: str) -> None:
    path = Path(f"src/autoskillit/config/{module_name}")
    if not path.exists():
        pytest.skip(f"{module_name} not present yet")
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            full = node.module
            if not full.startswith("autoskillit."):
                continue
            # Permit imports from siblings in config/ and from autoskillit.core.*
            top_two = ".".join(full.split(".")[:2])
            if top_two == "autoskillit.core":
                continue
            if top_two == "autoskillit.config":
                continue
            if top_two in FORBIDDEN_PACKAGES:
                pytest.fail(f"{module_name} imports forbidden package {top_two}")
