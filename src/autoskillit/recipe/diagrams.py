"""Pre-generate recipe flow diagrams as static Markdown artifacts.

Diagrams are stored in ``recipes/diagrams/{name}.md``, parallel to contract
cards in ``recipes/contracts/{name}.yaml``.  Staleness is detected by an HTML
comment ``<!-- autoskillit-recipe-hash: sha256:... -->`` embedded at the top
of each diagram file.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from autoskillit.core import _atomic_write
from autoskillit.recipe.staleness_cache import compute_recipe_hash

# Width of the separator rule in the route table.
_RULE = "─" * 71


def generate_recipe_diagram(pipeline_path: Path, recipes_dir: Path) -> str:
    """Generate a flow diagram for the recipe at *pipeline_path*.

    Reads the recipe YAML, builds a Markdown route table and ingredients
    table, embeds a SHA-256 hash comment for staleness detection, and
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

    # Determine step order for back-edge detection
    step_names = list(recipe.steps.keys())
    step_index: dict[str, int] = {name: i for i, name in enumerate(step_names)}

    def _is_back_edge(target: str | None, current_idx: int) -> bool:
        if target is None:
            return False
        idx = step_index.get(target)
        return idx is not None and idx < current_idx

    def _route(target: str | None, current_idx: int) -> str:
        if target is None:
            return ""
        suffix = "↑" if _is_back_edge(target, current_idx) else ""
        return f"→ {target}{suffix}"

    # Separate terminal and non-terminal steps
    terminal_steps: list[tuple[str, str]] = []
    non_terminal: list[tuple[str, Any]] = []
    for step_name, step in recipe.steps.items():
        if step.action == "stop":
            terminal_steps.append((step_name, step.message or ""))
        else:
            non_terminal.append((step_name, step))

    # Build route table rows
    table_lines: list[str] = []
    header = f"{'Step':<22} {'Tool':<22} {'✓ success':<22} {'✗ failure'}"
    table_lines.append(header)
    table_lines.append(_RULE)

    for step_name, step in non_terminal:
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

        if step.on_result is not None:
            # on_result steps: leave ✓ success blank
            success_col = ""
        else:
            success_col = _route(step.on_success, idx)

        failure_col = _route(step.on_failure, idx)

        table_lines.append(f"{step_name:<22} {tool_val:<22} {success_col:<22} {failure_col}")

        # Retry continuation line
        if step.retries > 0:
            table_lines.append(
                f"  {'↺ ×' + str(step.retries) + ' (failure)':<20}  → {step.on_exhausted}"
            )

        # on_result continuation lines
        if step.on_result is not None:
            sr = step.on_result
            if sr.conditions:
                # Predicate format
                for cond in sr.conditions:
                    when_str = cond.when if cond.when else "(default)"
                    suffix = "↑" if _is_back_edge(cond.route, idx) else ""
                    table_lines.append(f"  {when_str:<20}  → {cond.route}{suffix}")
            elif sr.routes:
                # Legacy format
                for key, target in sr.routes.items():
                    suffix = "↑" if _is_back_edge(target, idx) else ""
                    table_lines.append(f"  {key:<20}  → {target}{suffix}")

    table_lines.append(_RULE)

    # Terminal step lines below rule
    for term_name, term_msg in terminal_steps:
        if term_msg:
            table_lines.append(f'{term_name}  "{term_msg}"')
        else:
            table_lines.append(term_name)

    route_table = "\n".join(table_lines)

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
        f"## {recipe.name}\n"
        f"{recipe.description}\n"
        f"\n"
        f"**Flow:** {recipe.summary}\n"
        f"\n"
        f"### Graph\n"
        f"{route_table}\n"
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


def check_diagram_staleness(
    recipe_name: str,
    recipes_dir: Path,
    recipe_path: Path,
) -> bool:
    """Return True if the diagram for *recipe_name* is missing or out of date.

    Staleness is determined by comparing the SHA-256 hash embedded in the
    diagram file against the current hash of the recipe YAML.

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

    match = _HASH_RE.search(content)
    if not match:
        return True

    stored_hash = match.group(1)
    current_hash = compute_recipe_hash(recipe_path)
    return stored_hash != current_hash


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
