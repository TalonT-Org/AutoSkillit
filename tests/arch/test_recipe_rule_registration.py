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
