"""Architecture guard for the context-admission type-contract split (#4738)."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

_TYPES_DIR = Path(__file__).resolve().parents[2] / "src" / "autoskillit" / "core" / "types"
_FACADE_STEM = "_type_context_admission"
_SHARD_IMPORTS: dict[str, frozenset[str]] = {
    "_type_context_admission_base": frozenset(),
    "_type_context_admission_identities": frozenset({"_type_context_admission_base"}),
    "_type_context_admission_records": frozenset(
        {
            "_type_context_admission_base",
            "_type_context_admission_identities",
        }
    ),
    "_type_context_admission_events": frozenset(
        {
            "_type_context_admission_base",
            "_type_context_admission_identities",
            "_type_context_admission_records",
        }
    ),
    "_type_context_admission_effects": frozenset(
        {
            "_type_context_admission_base",
            "_type_context_admission_identities",
            "_type_context_admission_records",
        }
    ),
    "_type_context_admission_states": frozenset(
        {
            "_type_context_admission_base",
            "_type_context_admission_identities",
            "_type_context_admission_records",
            "_type_context_admission_events",
            "_type_context_admission_effects",
        }
    ),
    "_type_context_admission_coverage": frozenset({"_type_context_admission_base"}),
}
_WATCHED_STEMS = frozenset(_SHARD_IMPORTS) | {_FACADE_STEM}


def _context_admission_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports: set[str] = set()

    for node in ast.walk(tree):
        modules: list[str] = []
        if isinstance(node, ast.ImportFrom):
            if node.module:
                modules.append(node.module)
            if node.module in (None, "autoskillit.core.types"):
                modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        else:
            continue
        for module in modules:
            stem = module.rsplit(".", maxsplit=1)[-1]
            if stem in _WATCHED_STEMS:
                imports.add(stem)
    return imports


@pytest.mark.parametrize(
    ("shard_stem", "allowed_imports"),
    tuple(_SHARD_IMPORTS.items()),
    ids=tuple(_SHARD_IMPORTS),
)
def test_context_admission_shards_follow_one_way_import_graph(
    shard_stem: str,
    allowed_imports: frozenset[str],
) -> None:
    """Internal shards may depend only on the declared lower-level shards."""
    path = _TYPES_DIR / f"{shard_stem}.py"
    assert path.exists(), f"Missing context-admission shard: {path.name}"

    imports = _context_admission_imports(path)
    assert _FACADE_STEM not in imports, f"{path.name} must not import the stable facade"
    assert imports <= allowed_imports, (
        f"{path.name} imports higher-level or forbidden context-admission shards: "
        f"{sorted(imports - allowed_imports)}"
    )
