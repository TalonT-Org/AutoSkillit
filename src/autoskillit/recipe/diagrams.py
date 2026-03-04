"""Pre-generate recipe flow diagrams as static Markdown artifacts.

Diagrams are stored in ``recipes/diagrams/{name}.md``, parallel to contract
cards in ``recipes/contracts/{name}.yaml``.  Staleness is detected by an HTML
comment ``<!-- autoskillit-recipe-hash: sha256:... -->`` embedded at the top
of each diagram file, plus a format version marker to detect rendering logic
changes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from autoskillit.core import _atomic_write
from autoskillit.recipe.staleness_cache import compute_recipe_hash

# Diagram format version — bump when rendering logic changes so that
# existing diagrams are flagged stale even if the recipe YAML hasn't changed.
_DIAGRAM_FORMAT_VERSION = "v2"


# ---------------------------------------------------------------------------
# Layout data structures
# ---------------------------------------------------------------------------


@dataclass
class _LayoutStep:
    """A positioned step in the visual flow layout."""

    name: str
    tool: str
    is_terminal: bool = False
    message: str = ""
    on_success: str | None = None
    on_failure: str | None = None
    retries: int = 0
    on_exhausted: str = "escalate"
    is_back_edge_success: bool = False
    is_back_edge_failure: bool = False
    on_result_conditions: list[tuple[str, str, bool]] = field(default_factory=list)
    skip_when_false: str | None = None


@dataclass
class _LayoutResult:
    """Complete layout for visual rendering."""

    steps: list[_LayoutStep]
    back_edges: list[tuple[str, str]]  # (from_step, to_step)


# ---------------------------------------------------------------------------
# Layout computation
# ---------------------------------------------------------------------------


def _compute_layout(recipe: Any) -> _LayoutResult:
    """Compute visual layout from a Recipe dataclass."""
    step_names = list(recipe.steps.keys())
    step_index: dict[str, int] = {name: i for i, name in enumerate(step_names)}

    def _is_back_edge(target: str | None, current_idx: int) -> bool:
        if target is None:
            return False
        idx = step_index.get(target)
        return idx is not None and idx < current_idx

    layout_steps: list[_LayoutStep] = []
    back_edges: list[tuple[str, str]] = []

    for step_name, step in recipe.steps.items():
        idx = step_index[step_name]

        # Determine tool column value
        if step.tool is not None:
            tool_val = step.tool
        elif step.python is not None:
            tool_val = step.python
        elif step.action is not None:
            tool_val = step.action
        else:
            tool_val = "—"
        if step.model:
            tool_val = f"{tool_val} [{step.model}]"

        is_terminal = step.action == "stop"

        ls = _LayoutStep(
            name=step_name,
            tool=tool_val,
            is_terminal=is_terminal,
            message=step.message or "",
            on_success=step.on_success,
            on_failure=step.on_failure,
            retries=step.retries if not is_terminal else 0,
            on_exhausted=step.on_exhausted if not is_terminal else "escalate",
            skip_when_false=step.skip_when_false,
        )

        # Check for back-edges on success/failure
        if _is_back_edge(step.on_success, idx):
            ls.is_back_edge_success = True
            back_edges.append((step_name, step.on_success))  # type: ignore[arg-type]
        if _is_back_edge(step.on_failure, idx):
            ls.is_back_edge_failure = True
            back_edges.append((step_name, step.on_failure))  # type: ignore[arg-type]

        # Handle on_result conditions
        if step.on_result is not None:
            sr = step.on_result
            if sr.conditions:
                for cond in sr.conditions:
                    when_str = cond.when if cond.when else "(default)"
                    is_back = _is_back_edge(cond.route, idx)
                    ls.on_result_conditions.append((when_str, cond.route, is_back))
                    if is_back:
                        back_edges.append((step_name, cond.route))
            elif sr.routes:
                for key, target in sr.routes.items():
                    is_back = _is_back_edge(target, idx)
                    ls.on_result_conditions.append((key, target, is_back))
                    if is_back:
                        back_edges.append((step_name, target))

        layout_steps.append(ls)

    return _LayoutResult(steps=layout_steps, back_edges=back_edges)


# ---------------------------------------------------------------------------
# Visual ASCII flow renderer
# ---------------------------------------------------------------------------


def _render_visual_flow(layout: _LayoutResult) -> str:
    """Render the layout as a visual ASCII flow diagram using box-drawing characters.

    Format:
    - Steps connected by │ on the main vertical spine
    - Success/failure routes shown inline
    - Back-edges marked with ↑
    - Optional steps annotated with their condition
    - Retry info shown as sub-lines
    - Terminal steps shown at the bottom
    """
    lines: list[str] = []

    non_terminal = [s for s in layout.steps if not s.is_terminal]
    terminal = [s for s in layout.steps if s.is_terminal]

    for i, step in enumerate(non_terminal):
        # Optional step annotation
        if step.skip_when_false:
            lines.append(f"│  ⟨skip if {step.skip_when_false} is false⟩")

        # Step box
        lines.append(f"┌─ {step.name}  [{step.tool}]")

        # Routes
        if step.on_result_conditions:
            # on_result routing — multiple conditions
            for when_str, target, is_back in step.on_result_conditions:
                suffix = " ↑" if is_back else ""
                lines.append(f"│  ├─ {when_str}  → {target}{suffix}")
            if step.on_failure:
                suffix = " ↑" if step.is_back_edge_failure else ""
                lines.append(f"│  ✗ failure  → {step.on_failure}{suffix}")
        else:
            # Normal success/failure routing
            if step.on_success:
                suffix = " ↑" if step.is_back_edge_success else ""
                lines.append(f"│  ✓ success  → {step.on_success}{suffix}")
            if step.on_failure:
                suffix = " ↑" if step.is_back_edge_failure else ""
                lines.append(f"│  ✗ failure  → {step.on_failure}{suffix}")

        # Retry info
        if step.retries > 0:
            lines.append(f"│  ↺ ×{step.retries}  → {step.on_exhausted}")

        # Connector to next step (unless last non-terminal)
        if i < len(non_terminal) - 1:
            lines.append("│")

    # Terminal steps section
    if terminal:
        lines.append("│")
        lines.append("───────────────────────────────────────")
        for term in terminal:
            if term.message:
                lines.append(f'⏹ {term.name}  "{term.message}"')
            else:
                lines.append(f"⏹ {term.name}")

    return "\n".join(lines)


def generate_recipe_diagram(pipeline_path: Path, recipes_dir: Path) -> str:
    """Generate a visual flow diagram for the recipe at *pipeline_path*.

    Reads the recipe YAML, builds a visual ASCII flow diagram and ingredients
    table, embeds SHA-256 hash and format version for staleness detection, and
    writes the result atomically to ``recipes_dir/diagrams/{stem}.md``.

    Args:
        pipeline_path: Absolute path to the recipe ``.yaml`` file.
        recipes_dir: Root recipes directory (diagrams written to its
            ``diagrams/`` sub-directory).

    Returns:
        The diagram Markdown string that was written to disk.
    """
    from autoskillit.recipe.io import load_recipe

    recipe = load_recipe(pipeline_path)
    recipe_hash = compute_recipe_hash(pipeline_path)

    # Compute layout and render
    layout = _compute_layout(recipe)
    flow_diagram = _render_visual_flow(layout)

    # Build ingredients table
    ingredient_rows: list[str] = []
    ingredient_rows.append("| Name | Description | Required | Default |")
    ingredient_rows.append("|------|-------------|----------|---------|")
    for ing_name, ing in recipe.ingredients.items():
        required = "yes" if ing.required else "no"
        default = ing.default if ing.default is not None else ""
        ingredient_rows.append(f"| {ing_name} | {ing.description} | {required} | {default} |")
    ingredients_table = "\n".join(ingredient_rows)

    # Build kitchen rules section
    rules_section = ""
    if recipe.kitchen_rules:
        rules_lines = ["### Kitchen Rules"]
        for rule in recipe.kitchen_rules:
            rules_lines.append(f"- {rule}")
        rules_section = "\n" + "\n".join(rules_lines)

    # Assemble full diagram
    diagram = (
        f"<!-- autoskillit-recipe-hash: {recipe_hash} -->\n"
        f"<!-- autoskillit-diagram-format: {_DIAGRAM_FORMAT_VERSION} -->\n"
        f"## {recipe.name}\n"
        f"{recipe.description}\n"
        f"\n"
        f"**Flow:** {recipe.summary}\n"
        f"\n"
        f"### Graph\n"
        f"{flow_diagram}\n"
        f"\n"
        f"### Ingredients\n"
        f"{ingredients_table}"
        f"{rules_section}\n"
    )

    # Write atomically to diagrams/{stem}.md
    out_path = recipes_dir / "diagrams" / f"{pipeline_path.stem}.md"
    _atomic_write(out_path, diagram)
    return diagram


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
    return stored_format != _DIAGRAM_FORMAT_VERSION


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
                f"'autoskillit recipes render {recipe_name}' or "
                f"'autoskillit migrate' to regenerate."
            ),
        }
    ]
