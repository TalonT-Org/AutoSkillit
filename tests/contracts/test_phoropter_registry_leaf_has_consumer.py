"""Contract: phoropter-registry.yaml leaves have a consumer or inert-tracked annotation.

A leaf is "live" iff either (a) some production module outside
``src/autoskillit/assets/`` reads the leaf via attribute or chained
``.get()`` access, or (b) the YAML comment block immediately preceding
the leaf entry carries an ``inert-tracked:#NNNN`` annotation.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Any

import pytest

from autoskillit.core import load_yaml, pkg_root

pytestmark = [pytest.mark.layer("contracts"), pytest.mark.small]

REGISTRY_PATH = pkg_root() / "assets" / "phoropter-registry.yaml"
_SRC_ROOT = Path(__file__).resolve().parents[2] / "src" / "autoskillit"

_INERT_TRACKED_RE = re.compile(r"inert-tracked:#[1-9]\d*")

# Scan production ``.py`` files only (excluding ``assets/`` which holds
# the registry itself and ``skills_extended/`` whose SKILL.md frontmatter
# is documentation, not consumption).
_EXCLUDE_PY_DIRS: frozenset[str] = frozenset({"assets", "skills_extended"})


def _load_production_sources() -> dict[Path, str]:
    """Scan production ``.py`` files for leaf-consumer access.

    Read errors propagate; the contract does not silently skip sources.
    """
    sources: dict[Path, str] = {}
    for py_path in _SRC_ROOT.rglob("*.py"):
        if any(part in _EXCLUDE_PY_DIRS for part in py_path.relative_to(_SRC_ROOT).parts):
            continue
        sources[py_path] = py_path.read_text(encoding="utf-8")
    return sources


_PRODUCTION_SOURCES: dict[Path, str] = _load_production_sources()


def _walk_leaves(entry: dict[str, Any]) -> list[tuple[str, Any]]:
    """Recursively walk a family entry, yielding ``(dot_path, value)`` for every nested leaf.

    ``None`` values are preserved as leaves.
    """
    leaves: list[tuple[str, Any]] = []

    def _recurse(node: Any, prefix: str) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                path = f"{prefix}.{key}" if prefix else key
                if isinstance(value, dict):
                    _recurse(value, path)
                else:
                    leaves.append((path, value))
        else:
            leaves.append((prefix, node))

    _recurse(entry, "")
    return leaves


def _attribute_chain(node: ast.Attribute) -> list[str]:
    """Return the trailing attribute names from ``node`` outward.

    ``obj.a.b.c`` → ``["a", "b", "c"]``.
    """
    parts: list[str] = []
    current: ast.expr = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    parts.reverse()
    return parts


def _get_chain(node: ast.Call) -> list[str] | None:
    """Return the string keys from a ``.get("x").get("y")`` chain rooted at ``node``.

    Returns ``None`` if the chain is not a valid string-keyed ``.get()``
    sequence (e.g., keys are non-string literals or methods are not
    named ``get``).
    """
    keys: list[str] = []
    current: ast.expr = node
    while isinstance(current, ast.Call):
        func = current.func
        if not isinstance(func, ast.Attribute) or func.attr != "get":
            return None
        if not current.args:
            return None
        first_arg = current.args[0]
        if not isinstance(first_arg, ast.Constant) or not isinstance(first_arg.value, str):
            return None
        keys.append(first_arg.value)
        current = func.value
    keys.reverse()
    return keys


def _ast_has_consumer(tree: ast.Module, segments: tuple[str, ...]) -> bool:
    """Return ``True`` iff ``tree`` accesses a path that ends with ``segments``.

    Detected patterns:

    - ``obj.a.b.c`` — ``ast.Attribute`` whose trailing chain equals ``segments``.
    - ``obj.get("a").get("b")`` — ``ast.Call`` chain whose string keys equal ``segments``.
    """
    target = list(segments)
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            chain = _attribute_chain(node)
            if len(chain) >= len(target) and chain[-len(target) :] == target:
                return True
        elif isinstance(node, ast.Call):
            keys = _get_chain(node)
            if keys is not None and len(keys) >= len(target) and keys[-len(target) :] == target:
                return True
    return False


def _has_consumer(leaf_path: str) -> bool:
    """True iff some production source reads the leaf via attribute or chained ``.get()`` access.

    Single-segment paths (no family-name prefix) cannot be distinguished
    from incidental attribute access via suffix matching and are treated
    as having no consumer. The registry schema enforces ≥2 segments per
    leaf, so this branch is a defense-in-depth fallback.
    """
    segments = tuple(leaf_path.split("."))
    if not segments or not all(segments) or len(segments) < 2:
        return False
    for source in _PRODUCTION_SOURCES.values():
        tree = ast.parse(source)
        if _ast_has_consumer(tree, segments):
            return True
    return False


def _is_inert_tracked(registry_text: str, family_name: str) -> bool:
    """True iff the YAML comment block immediately preceding the family's
    declaration carries an ``inert-tracked:#NNNN`` annotation.

    The annotation marks the whole family entry as inert rather than
    per-leaf; collect contiguous comment lines above the family declaration.
    """
    lines = registry_text.splitlines()
    family_idx = next(
        (
            i
            for i, line in enumerate(lines)
            if re.match(rf"^\s{{2}}{re.escape(family_name)}\s*:", line)
        ),
        None,
    )
    if family_idx is None:
        return False

    comment_lines: list[str] = []
    j = family_idx - 1
    while j >= 0 and lines[j].strip().startswith("#"):
        comment_lines.append(lines[j])
        j -= 1
    joined = "\n".join(reversed(comment_lines))
    return bool(_INERT_TRACKED_RE.search(joined))


def _check_registry_leaves(path: Path) -> None:
    """Internal helper used by the main test and the canary test."""
    registry_text = path.read_text(encoding="utf-8")
    registry = load_yaml(path)
    violations: list[str] = []
    for family_name, family_entry in registry.get("families", {}).items():
        if _is_inert_tracked(registry_text, family_name):
            continue
        for leaf_path, _ in _walk_leaves(family_entry):
            if _has_consumer(leaf_path):
                continue
            violations.append(f"{family_name}.{leaf_path}")
    assert not violations, (
        "Registry leaves without consumers or inert-track markers:\n" + "\n".join(violations)
    )


def test_every_registry_leaf_has_consumer_or_inert_track() -> None:
    """Every leaf in phoropter-registry.yaml must have either a production
    consumer or an ``inert-tracked:#NNNN`` annotation.
    """
    _check_registry_leaves(REGISTRY_PATH)


def test_inert_tracked_regex_matches_yaml_comment_shapes() -> None:
    """The inert-tracked regex matches the YAML-comment shapes used in the registry.

    Positive case — comment block above the leaf key matches:
    """
    positive = (
        "# Deliberately unread pending #4895.\n"
        "# inert-tracked:#4895\n"
        "  vis-lens:\n"
        "    step_naming:\n"
        "      prefix: vis\n"
    )
    assert _is_inert_tracked(positive, "vis-lens")

    # Negative case — no comment block.
    negative = "  vis-lens:\n    step_naming:\n      prefix: vis\n"
    assert not _is_inert_tracked(negative, "vis-lens")

    # Negative case — unrelated comment.
    unrelated = "# unrelated comment\n  vis-lens:\n    step_naming:\n      prefix: vis\n"
    assert not _is_inert_tracked(unrelated, "vis-lens")


def test_rejected_accretion_canary(tmp_path: Path) -> None:
    """Demonstrates the contract catches phantom leaves.

    Two scenarios are exercised against a ``tmp_path`` registry: a
    2-segment phantom with an obscure name (positive case for AST-based
    detection) and a 1-segment phantom with a common attribute name
    (positive case for the single-segment guard).
    """
    base = "schema_version: 2\nfamilies:\n  arch-lens:\n    step_naming:\n      prefix: null\n"
    cases = [
        ("phantom_leaf", '    phantom_leaf: "retired metadata"\n'),
        ("description", '    description: "retired"\n'),
    ]
    for needle, leaf_line in cases:
        bad_path = tmp_path / "phoropter-registry.yaml"
        bad_path.write_text(base + leaf_line, encoding="utf-8")
        with pytest.raises(AssertionError, match=needle):
            _check_registry_leaves(bad_path)
