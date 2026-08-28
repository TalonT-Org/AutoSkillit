"""Contract: phoropter-registry.yaml leaves have a production consumer or inert-tracked annotation.

Tracks issue #4894.

Generalizes the ``inert-tracked:#NNNN`` discipline documented in
tests/AGENTS.md § run_skill Parameter-Role Ledgers (precedent:
tests/contracts/test_config_field_has_consumer.py, which applies it to
config dataclass fields; and
tests/contracts/test_recipe_step_field_ledger.py, which applies it to
``RecipeStep`` fields) to the phoropter registry YAML.

A leaf is "live" iff either (a) some production module outside
``src/autoskillit/assets/`` reads the leaf via dotted attribute access
(``entry.step_naming.prefix``) or chained ``dict.get()`` calls
(``entry.get("step_naming", {}).get("prefix")``), or (b) the YAML comment
block immediately preceding the leaf entry carries an
``inert-tracked:#NNNN`` annotation citing an open issue.

The consumer scan excludes ``src/autoskillit/skills_extended/`` because
SKILL.md frontmatter is documentation, not consumption — including it would
falsely mark ``activate_deps`` as consumed by 47 SKILL.md frontmatter
blocks after a future re-accretion, defeating the contract.

Distinct from tests/skills/test_phoropter_structural.py::test_collection_does_not_read_registry,
which guards that the structural test does not read the registry at
collection time (a different invariant — the structural test is a *consumer*
of filesystem SKILL.md content, not of the registry).

Scope: enforces every leaf (recursive) under ``families.<family-name>.*`` in
``src/autoskillit/assets/phoropter-registry.yaml``. The top-level
``schema_version`` and ``families`` keys are not leaves.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

from autoskillit.core import load_yaml, pkg_root

pytestmark = [pytest.mark.layer("contracts"), pytest.mark.small]

_REGISTRY_PATH = pkg_root() / "assets" / "phoropter-registry.yaml"
_SRC_ROOT = Path(__file__).resolve().parents[2] / "src" / "autoskillit"

_INERT_TRACKED_RE = re.compile(r"inert-tracked:#[1-9]\d*")

# Scan roots mirror ``test_config_field_has_consumer.py:111-117``: production
# ``.py`` (excluding ``assets/`` which holds the registry itself and
# ``skills_extended/`` whose SKILL.md frontmatter is documentation, not
# consumption) plus the production YAML files that could reference leaves.
_SCAN_PY_ROOTS: tuple[Path, ...] = (_SRC_ROOT,)

_SCAN_YAML_PATHS: tuple[Path, ...] = (
    _SRC_ROOT / "recipes" / "phoropter.yaml",
    _SRC_ROOT / "recipe" / "skill_contracts.yaml",
    _SRC_ROOT / "config" / "defaults.yaml",
)

_EXCLUDE_PY_DIRS: frozenset[str] = frozenset({"assets", "skills_extended"})


def _load_production_sources() -> dict[Path, str]:
    """Scan all production source files for leaf-consumer references.

    ``.py`` files under ``src/autoskillit/`` (excluding ``assets/`` and
    ``skills_extended/``). ``.yaml`` files at the hardcoded production
    paths in ``_SCAN_YAML_PATHS``. Returned as a path -> text map.
    """
    sources: dict[Path, str] = {}
    for py_root in _SCAN_PY_ROOTS:
        for py_path in py_root.rglob("*.py"):
            if any(part in _EXCLUDE_PY_DIRS for part in py_path.relative_to(_SRC_ROOT).parts):
                continue
            try:
                sources[py_path] = py_path.read_text(encoding="utf-8")
            except OSError:
                continue
    for yaml_path in _SCAN_YAML_PATHS:
        if yaml_path.exists():
            try:
                sources[yaml_path] = yaml_path.read_text(encoding="utf-8")
            except OSError:
                continue
    return sources


_PRODUCTION_SOURCES: dict[Path, str] = _load_production_sources()


def _walk_leaves(entry: dict[str, Any]) -> list[tuple[str, Any]]:
    """Recursively walk a family entry, yielding ``(dot_path, value)`` for every nested leaf.

    ``None`` values are preserved as leaves — the only live leaf,
    ``step_naming.prefix``, is ``null`` for arch-lens and exp-lens.
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


def _has_consumer(leaf_path: str) -> bool:
    """True iff some production source reads the leaf via dotted attribute
    access or chained ``.get()`` calls.

    Two access patterns are recognized (per the plan's T9 spec):

    - **Dotted attribute access**: ``.step_naming.prefix`` (precedent from
      ``test_config_field_has_consumer.py:171`` — regex ``\\.{name}\\b``).
    - **Chained ``.get()`` calls**: ``.get(\"step_naming\").get(\"prefix\")``
      (the actual production pattern in
      ``src/autoskillit/recipe/rules/rules_phoropter_adjacency.py:31``).

    For each leaf path (e.g., ``step_naming.prefix``), split on ``.`` to
    get segments. A source file is a consumer if EITHER:

    - It contains ``.{first_segment}.{second_segment}\\b`` (dotted access), OR
    - It contains a chained ``.get()`` pattern that walks both segments.
      The regex anchors ``.get(\"first\").get(\"second\")`` (DOTALL so the
      two calls may span lines).

    For a single-segment leaf (no ``.``), match ``.{leaf_name}\\b``.
    """
    segments = leaf_path.split(".")
    if len(segments) == 1:
        # Top-level single-segment leaf — dotted attribute access only.
        pattern = re.compile(rf"\.{re.escape(segments[0])}\b")
        return any(pattern.search(text) for text in _PRODUCTION_SOURCES.values())

    first, second = segments[0], segments[1]
    dotted = re.compile(rf"\.{re.escape(first)}\.{re.escape(second)}\b")
    # Allow default values between the key and the closing paren — e.g.
    # ``.get("step_naming", {}).get("prefix")`` — which is the actual
    # production pattern in ``rules_phoropter_adjacency.py``.
    chained = re.compile(
        rf"\.get\(\s*[\"']{re.escape(first)}[\"'][^)]*\)[^.]*\.get\(\s*[\"']"
        rf"{re.escape(second)}[\"'][^)]*\)",
        re.DOTALL,
    )
    return any(
        dotted.search(text) or chained.search(text) for text in _PRODUCTION_SOURCES.values()
    )


def _is_inert_tracked(
    registry_text: str,
    family_name: str,
    leaf_path: str,
) -> bool:
    """True iff the YAML comment block immediately preceding the family's
    declaration carries an ``inert-tracked:#NNNN`` annotation.

    The annotation marks the whole family entry as inert (rather than per-leaf),
    so we walk back from the family line collecting contiguous comment lines.
    ``leaf_path`` is accepted for API symmetry with ``_check_registry_leaves``
    but is not consulted here.
    """
    del leaf_path  # intentionally unused — annotation is family-scoped
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
    for family_name, family_entry in registry["families"].items():
        for leaf_path, _ in _walk_leaves(family_entry):
            if _has_consumer(leaf_path):
                continue
            if _is_inert_tracked(registry_text, family_name, leaf_path):
                continue
            violations.append(f"{family_name}.{leaf_path}")
    assert not violations, (
        "Registry leaves without consumers or inert-track markers:\n" + "\n".join(violations)
    )


def test_every_registry_leaf_has_consumer_or_inert_track() -> None:
    """Every leaf in phoropter-registry.yaml must have either a production
    consumer or an ``inert-tracked:#NNNN`` annotation.

    Mirrors the violations-accumulator pattern at lines 232-246 of
    ``test_config_field_has_consumer.py``.
    """
    _check_registry_leaves(_REGISTRY_PATH)


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
    assert _is_inert_tracked(positive, "vis-lens", "step_naming.prefix")

    # Negative case — no comment block.
    negative = "  vis-lens:\n    step_naming:\n      prefix: vis\n"
    assert not _is_inert_tracked(negative, "vis-lens", "step_naming.prefix")

    # Negative case — unrelated comment.
    unrelated = "# unrelated comment\n  vis-lens:\n    step_naming:\n      prefix: vis\n"
    assert not _is_inert_tracked(unrelated, "vis-lens", "step_naming.prefix")


def test_rejected_accretion_canary(tmp_path: Path) -> None:
    """Demonstrates the contract catches phantom leaves.

    A registry that re-adds a leaf without consumer or inert-track must fail
    the contract. The contract logic is invoked against a ``tmp_path``
    registry rather than the production registry, so the test does not
    depend on live state.
    """
    bad_registry_text = (
        "schema_version: 2\n"
        "families:\n"
        "  arch-lens:\n"
        "    step_naming:\n"
        "      prefix: null\n"
        '    phantom_leaf: "retired metadata"\n'
    )
    bad_path = tmp_path / "phoropter-registry.yaml"
    bad_path.write_text(bad_registry_text, encoding="utf-8")

    with pytest.raises(AssertionError, match="phantom_leaf"):
        _check_registry_leaves(bad_path)
