"""Recipe diagram loading and staleness detection.

Diagrams are stored in ``recipes/diagrams/{name}.md``, parallel to contract
cards in ``recipes/contracts/{name}.yaml``.  Staleness is detected by an HTML
comment ``<!-- autoskillit-recipe-hash: sha256:... -->`` embedded at the top
of each diagram file, plus a format version marker.

Diagram *rendering* is handled by the ``/render-recipe`` skill, not by this
module.  This module only loads and checks pre-rendered artifacts.
"""

from __future__ import annotations

from pathlib import Path

import regex as re

from autoskillit.recipe.staleness_cache import compute_recipe_hash

# Diagram format version — bump when the render-recipe skill spec changes
# so that existing diagrams are flagged stale.
DIAGRAM_FORMAT_VERSION = "v7"


def load_recipe_diagram(recipe_name: str, recipes_dir: Path) -> str | None:
    """Read the pre-generated diagram for *recipe_name*, or return None.

    Args:
        recipe_name: Recipe name without extension.
        recipes_dir: Root recipes directory containing a ``diagrams/`` sub-dir.

    Returns:
        Diagram Markdown string, or ``None`` if the file is missing or unreadable.
    """
    path = recipes_dir / "diagrams" / f"{recipe_name}.md"
    if not path.exists():
        return None
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


_HASH_RE = re.compile(r"<!-- autoskillit-recipe-hash: (sha256:[0-9a-f]+) -->")
_FORMAT_RE = re.compile(r"<!-- autoskillit-diagram-format: (\S+) -->")


def check_diagram_staleness(
    recipe_name: str,
    recipes_dir: Path,
    recipe_path: Path,
) -> bool:
    """Return True if the diagram for *recipe_name* is missing or out of date.

    Staleness is determined by comparing:
    1. The SHA-256 hash embedded in the diagram against the current recipe YAML.
    2. The format version embedded in the diagram against the current renderer version.

    Args:
        recipe_name: Recipe name without extension.
        recipes_dir: Root recipes directory containing a ``diagrams/`` sub-dir.
        recipe_path: Path to the recipe ``.yaml`` file (for current hash).

    Returns:
        ``True`` if the diagram is stale or missing, ``False`` if up to date.
    """
    content = load_recipe_diagram(recipe_name, recipes_dir)
    if content is None:
        return True

    hash_match = _HASH_RE.search(content)
    if not hash_match:
        return True

    stored_hash = hash_match.group(1)
    current_hash = compute_recipe_hash(recipe_path)
    if stored_hash != current_hash:
        return True

    # Check format version
    format_match = _FORMAT_RE.search(content)
    if not format_match:
        return True  # No format version = pre-v2 diagram, stale

    stored_format = format_match.group(1)
    return stored_format != DIAGRAM_FORMAT_VERSION


def diagram_stale_to_suggestions(recipe_name: str) -> list[dict[str, str]]:
    """Return an MCP suggestion list for a stale diagram.

    Args:
        recipe_name: Recipe name (used in the suggestion message).

    Returns:
        A single-element list with a ``stale-diagram`` warning suggestion.
    """
    return [
        {
            "rule": "stale-diagram",
            "severity": "warning",
            "message": (
                f"Diagram for '{recipe_name}' is out of date — run "
                f"'/render-recipe {recipe_name}' to regenerate."
            ),
        }
    ]


def annotate_diagram_with_pruning(diagram: str, skip_resolutions: dict[str, bool | None]) -> str:
    """Append a Markdown footer listing steps pruned at serve time.

    Args:
        diagram: The raw diagram Markdown string.
        skip_resolutions: Map of step name to its skip_when_false resolution.
            Steps with value ``False`` are listed as pruned.

    Returns:
        The diagram with a pruning annotation footer appended, or unchanged
        if no steps were pruned or the diagram is empty.
    """
    pruned = sorted(name for name, resolved in skip_resolutions.items() if resolved is False)
    if not pruned:
        return diagram
    step_list = ", ".join(pruned)
    footer = (
        "\n\n---\n"
        "**Steps pruned under current backend/ingredient configuration:**\n"
        f"{step_list}\n\n"
        "> These steps appear in the full recipe graph above but are skipped\n"
        "> at runtime due to `skip_when_false` guards evaluating to false.\n"
    )
    return diagram.rstrip("\n") + footer
