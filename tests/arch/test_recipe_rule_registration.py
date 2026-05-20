"""REQ-RECIPE-001: every recipe/rules_*.py file must be imported by recipe/__init__.py.

Finding 14.1 — gate that prevents orphan rule modules whose @semantic_rule decorators
never register because the module is never imported.
"""

import ast
from pathlib import Path


def test_every_rules_module_imported_by_recipe_init() -> None:
    """REQ-RECIPE-001: every recipe/rules_*.py file must be imported by
    recipe/__init__.py so its @semantic_rule decorators register at
    import time. Catches accidental orphan rule modules."""
    src = Path(__file__).resolve().parents[2] / "src" / "autoskillit" / "recipe"
    rules_files = sorted(p.stem for p in (src / "rules").glob("rules_*.py"))
    init_text = (src / "__init__.py").read_text()
    init_tree = ast.parse(init_text)

    imported: set[str] = set()
    for node in ast.walk(init_tree):
        if isinstance(node, ast.ImportFrom):
            # `from . import rules_X` → node.module is None, names hold rules_X
            for name in node.names:
                imported.add(name.name)
            if node.module:
                imported.add(node.module.split(".")[-1])
        if isinstance(node, ast.Import):
            for name in node.names:
                imported.add(name.name.split(".")[-1])

    missing = [r for r in rules_files if r not in imported]
    assert not missing, (
        f"recipe/__init__.py must import these rules modules so their "
        f"@semantic_rule decorators register: {missing}"
    )


def test_load_cache_entry_has_rule_registry_hash_guard() -> None:
    """_LoadCacheEntry must have a rule_registry_hash field and the cache hit
    validation must reference it — prevents silent removal in future refactors."""
    import dataclasses

    src = Path(__file__).resolve().parents[2] / "src" / "autoskillit" / "recipe"
    api_src = src / "_api.py"

    from autoskillit.recipe._api import _LoadCacheEntry

    field_names = {f.name for f in dataclasses.fields(_LoadCacheEntry)}
    assert "rule_registry_hash" in field_names, (
        "_LoadCacheEntry must have a rule_registry_hash field"
    )

    api_text = api_src.read_text()
    tree = ast.parse(api_text)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "load_and_validate":
            fn_src = ast.get_source_segment(api_text, node) or ""
            assert "rule_registry_hash" in fn_src, (
                "load_and_validate must reference rule_registry_hash in cache hit validation"
            )
            break
    else:
        raise AssertionError("load_and_validate not found in _api.py")


def test_rule_registry_hash_nonempty_after_recipe_import() -> None:
    """RULE_REGISTRY_HASH must be non-empty after `import autoskillit.recipe`."""
    from autoskillit.recipe.registry import (
        RULE_REGISTRY_HASH,  # pyright: ignore[reportAttributeAccessIssue]
    )

    assert RULE_REGISTRY_HASH, (
        "RULE_REGISTRY_HASH is empty — _finalize_registry() may have been removed or "
        "called before rules registered"
    )
