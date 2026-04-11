"""REQ-CONFIG-001: every sub-config dataclass field must be referenced in from_dynaconf.

Finding 9.1 — gate that prevents silent omissions when new fields are added to any
*Config dataclass in settings.py without a corresponding val(...) line in
AutomationConfig.from_dynaconf (or in a _build_* helper directly called from it).
"""

import ast
import dataclasses
from pathlib import Path

import pytest

import autoskillit.config.settings as settings_mod
from autoskillit.config.settings import AutomationConfig

_SUBCONFIG_DATACLASSES = [
    cls
    for cls in vars(settings_mod).values()
    if isinstance(cls, type) and dataclasses.is_dataclass(cls) and cls is not AutomationConfig
]


def _collect_referenced_names(tree: ast.Module) -> set[str]:
    """Collect all string constants and keyword argument names from from_dynaconf
    and any _build_* module-level helper functions it calls directly.

    Rationale: some sub-configs (e.g. SubsetsConfig, PacksConfig) are built by
    dedicated _build_* helpers that contain the actual val(...) / .get(...) calls.
    These helpers are called directly from from_dynaconf and are part of the same
    construction pipeline — their field references are equally authoritative.
    """
    # Collect all module-level function definitions by name
    top_level_fns: dict[str, ast.FunctionDef] = {}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef):
            top_level_fns[node.name] = node

    fn: ast.FunctionDef | None = top_level_fns.get("from_dynaconf")
    assert fn is not None, "from_dynaconf must exist"

    # Collect _build_* function names called from from_dynaconf
    helper_names: set[str] = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Call):
            func = node.func
            name = (
                func.id
                if isinstance(func, ast.Name)
                else func.attr
                if isinstance(func, ast.Attribute)
                else None
            )
            if name and name.startswith("_build_"):
                helper_names.add(name)

    # Walk from_dynaconf + all called _build_* helpers
    nodes_to_walk: list[ast.AST] = [fn]
    for helper_name in helper_names:
        helper_fn = top_level_fns.get(helper_name)
        if helper_fn is not None:
            nodes_to_walk.append(helper_fn)

    referenced: set[str] = set()
    for root in nodes_to_walk:
        for node in ast.walk(root):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                referenced.add(node.value)
            if isinstance(node, ast.keyword) and node.arg:
                referenced.add(node.arg)
    return referenced


@pytest.mark.parametrize("dc", _SUBCONFIG_DATACLASSES, ids=lambda c: c.__name__)
def test_every_subconfig_field_referenced_in_from_dynaconf(dc: type) -> None:
    """REQ-CONFIG-001: every dataclass field declared in any *Config
    dataclass in settings.py must appear at least once as a string
    constant or kwarg name inside AutomationConfig.from_dynaconf (or in
    a _build_* helper function directly called from it).
    Catches silent omissions when a new field is added without a
    corresponding val(...) line."""
    src = Path(settings_mod.__file__).read_text()
    tree = ast.parse(src)

    referenced = _collect_referenced_names(tree)

    missing = [f.name for f in dataclasses.fields(dc) if f.name not in referenced]
    assert not missing, (
        f"{dc.__name__} fields missing from from_dynaconf/_build_* helpers: {missing}. "
        f"Add an explicit val(...) line for each."
    )
