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
_DIAGRAM_FORMAT_VERSION = "v3"


# ---------------------------------------------------------------------------
# Layout data structures
# ---------------------------------------------------------------------------


@dataclass
class _LayoutStep:
    """A positioned step in the visual flow layout."""

    name: str
    tool: str
    is_terminal: bool = False
    is_infrastructure: bool = False
    message: str = ""
    on_success: str | None = None
    on_failure: str | None = None
    on_context_limit: str | None = None
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
    # Indices in the visible (non-infra, non-terminal) step list for the
    # FOR EACH iteration block, or None if no loop is detected.
    for_each_range: tuple[int, int] | None = None


# ---------------------------------------------------------------------------
# Layout computation
# ---------------------------------------------------------------------------


def _is_infrastructure_step(step: Any) -> bool:
    """Return True if *step* is a plumbing step that should be hidden from diagrams.

    Infrastructure steps are ``run_cmd`` steps whose sole purpose is capturing
    or setting a context value (git rev-parse, printf, echo one-liners).
    They add no user-visible behaviour to the pipeline flow.
    """
    if step.tool != "run_cmd":
        return False
    note_lower = (step.note or "").lower()
    cmd = ""
    if step.with_args and isinstance(step.with_args, dict):
        cmd = step.with_args.get("cmd", "") or ""
    return (
        "capture" in note_lower
        or "set" in note_lower
        or "printf" in cmd
        or "git rev-parse" in cmd
        or (cmd.strip().startswith("echo") and "\n" not in cmd)
    )


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

        # Determine tool label — do NOT include model (per spec)
        if step.tool is not None:
            tool_val = step.tool
        elif step.python is not None:
            tool_val = step.python
        elif step.action is not None:
            tool_val = step.action
        else:
            tool_val = "—"

        is_terminal = step.action == "stop"
        infra = _is_infrastructure_step(step)

        ls = _LayoutStep(
            name=step_name,
            tool=tool_val,
            is_terminal=is_terminal,
            is_infrastructure=infra,
            message=step.message or "",
            on_success=step.on_success,
            on_failure=step.on_failure,
            on_context_limit=step.on_context_limit,
            retries=step.retries if not is_terminal else 0,
            on_exhausted=step.on_exhausted if not is_terminal else "escalate",
            skip_when_false=step.skip_when_false,
        )

        if _is_back_edge(step.on_success, idx):
            ls.is_back_edge_success = True
            back_edges.append((step_name, step.on_success))  # type: ignore[arg-type]
        if _is_back_edge(step.on_failure, idx):
            ls.is_back_edge_failure = True
            back_edges.append((step_name, step.on_failure))  # type: ignore[arg-type]

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

    # Post-process: redirect routing targets that pass through infrastructure steps
    # to the first visible (non-infra) successor so they don't appear in the diagram.
    infra_names = {s.name for s in layout_steps if s.is_infrastructure}

    def _skip_infra(target: str | None) -> str | None:
        """Follow on_success chain past infrastructure steps to first visible target."""
        if target is None or target not in infra_names:
            return target
        visited: set[str] = set()
        current: str = target
        while current in infra_names and current not in visited:
            visited.add(current)
            orig = recipe.steps.get(current)
            if orig is None or orig.on_success is None:
                break
            current = orig.on_success
        return current

    for ls in layout_steps:
        ls.on_success = _skip_infra(ls.on_success)
        ls.on_failure = _skip_infra(ls.on_failure)
        ls.on_context_limit = _skip_infra(ls.on_context_limit)
        ls.on_result_conditions = [
            (when_str, _skip_infra(target) or target, is_back)
            for when_str, target, is_back in ls.on_result_conditions
        ]

    # Detect FOR EACH loop group among visible (non-infra, non-terminal) steps.
    # Find the back-edge with the largest span (most steps in the loop).
    visible = [s for s in layout_steps if not s.is_infrastructure and not s.is_terminal]
    vis_index = {s.name: i for i, s in enumerate(visible)}

    for_each_range: tuple[int, int] | None = None
    best_span = 1  # require loop of at least 3 visible steps (span > 1)

    for vi, vs in enumerate(visible):
        back_targets: list[str] = []
        if vs.is_back_edge_success and vs.on_success:
            back_targets.append(vs.on_success)
        if vs.is_back_edge_failure and vs.on_failure:
            back_targets.append(vs.on_failure)
        for _, target, is_back in vs.on_result_conditions:
            if is_back:
                back_targets.append(target)

        for target in back_targets:
            vj = vis_index.get(target)
            if vj is not None and vj < vi:
                span = vi - vj
                if span > best_span:
                    best_span = span
                    for_each_range = (vj, vi)

    return _LayoutResult(
        steps=layout_steps,
        back_edges=back_edges,
        for_each_range=for_each_range,
    )


# ---------------------------------------------------------------------------
# Visual ASCII flow renderer
# ---------------------------------------------------------------------------


def _append_step(step: _LayoutStep, lines: list[str], prefix: str) -> None:
    """Append rendering lines for a single step onto *lines*."""
    if step.skip_when_false:
        # Optional step: bracket notation with right-side annotation
        retry_str = ""
        if step.retries == 0:
            retry_str = " (retry ×∞)"
        elif step.retries > 0:
            retry_str = f" (retry ×{step.retries})"
        lines.append(f"{prefix}├── [{step.name}]{retry_str}  ← only if {step.skip_when_false}")
        if step.on_result_conditions:
            for when_str, target, is_back in step.on_result_conditions:
                suf = " ↑" if is_back else ""
                lines.append(f"{prefix}│       {when_str} → {target}{suf}")
        if step.on_context_limit:
            lines.append(f"{prefix}│       ⌛ context limit → {step.on_context_limit}")
        if step.on_failure:
            suf = " ↑" if step.is_back_edge_failure else ""
            lines.append(f"{prefix}│       ✗ failure → {step.on_failure}{suf}")
    else:
        # Normal step: show tool name and retry annotation inline
        if not step.is_terminal:
            retry_str = " (retry ×∞)" if step.retries == 0 else f" (retry ×{step.retries})"
        else:
            retry_str = ""

        tool_label = f"  [{step.tool}]" if step.tool and step.tool != "—" else ""
        lines.append(f"{prefix}{step.name}{tool_label}{retry_str}")

        if step.on_result_conditions:
            for when_str, target, is_back in step.on_result_conditions:
                suf = " ↑" if is_back else ""
                lines.append(f"{prefix}│  {when_str} → {target}{suf}")
        else:
            if step.on_success:
                suf = " ↑" if step.is_back_edge_success else ""
                lines.append(f"{prefix}│  ↓ success → {step.on_success}{suf}")

        if step.on_failure:
            suf = " ↑" if step.is_back_edge_failure else ""
            lines.append(f"{prefix}│  ✗ failure → {step.on_failure}{suf}")

        if step.on_context_limit:
            lines.append(f"{prefix}│  ⌛ context limit → {step.on_context_limit}")


def _render_visual_flow(layout: _LayoutResult) -> str:
    """Render the layout as a spec-compliant visual ASCII flow diagram.

    Format rules (per SKILL.md visual grammar):
    - Infrastructure steps are hidden entirely
    - Optional steps use bracket+arrow notation: ``├── [name]  ← only if cond``
    - Retry shown parenthetically on step name: ``(retry ×N)`` or ``(retry ×∞)``
    - ``on_context_limit`` shown as: ``⌛ context limit → target``
    - Iteration loops wrapped in FOR EACH block using box-drawing
    - Back-edges use ``↑`` suffix on target name
    - Terminal steps at bottom after separator line
    """
    lines: list[str] = []

    visible = [s for s in layout.steps if not s.is_infrastructure and not s.is_terminal]
    terminal = [s for s in layout.steps if s.is_terminal]

    fe_start: int | None = None
    fe_end: int | None = None
    if layout.for_each_range is not None:
        fe_start, fe_end = layout.for_each_range

    i = 0
    while i < len(visible):
        if fe_start is not None and i == fe_start:
            # Wrap loop group in FOR EACH block
            lines.append("┌────┤ FOR EACH:")
            assert fe_end is not None
            for j in range(fe_start, fe_end + 1):
                inner = visible[j]
                _append_step(inner, lines, "│  ")
                if j < fe_end:
                    lines.append("│  │")
            lines.append("└────┘")
            i = fe_end + 1
            if i < len(visible):
                lines.append("│")
            continue

        _append_step(visible[i], lines, "")

        if i < len(visible) - 1:
            lines.append("│")

        i += 1

    if terminal:
        lines.append("│")
        lines.append("─────────────────────────────────────")
        for term in terminal:
            if term.message:
                lines.append(f'⏹ {term.name}  "{term.message}"')
            else:
                lines.append(f"⏹ {term.name}")

    return "\n".join(lines)


def _format_ingredient_default(ing: Any) -> str:
    """Return the display value for an ingredient's default in the Inputs table."""
    if ing.default is None:
        return "—"
    if ing.default == "":
        return "auto-detect"
    if ing.default.lower() == "false":
        return "off"
    if ing.default.lower() == "true":
        return "on"
    return ing.default


def generate_recipe_diagram(pipeline_path: Path, recipes_dir: Path) -> str:
    """Generate a visual flow diagram for the recipe at *pipeline_path*.

    Reads the recipe YAML, builds a spec-compliant visual ASCII flow diagram
    and 3-column Inputs table, embeds SHA-256 hash and format version for
    staleness detection, and writes the result atomically to
    ``recipes_dir/diagrams/{stem}.md``.

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

    # Compute layout and render flow diagram
    layout = _compute_layout(recipe)
    flow_diagram = _render_visual_flow(layout)

    # Build 3-column Inputs table (Name | Description | Default)
    input_rows: list[str] = [
        "| Name | Description | Default |",
        "|------|-------------|---------|",
    ]
    agent_managed: list[str] = []
    for ing_name, ing in recipe.ingredients.items():
        if ing.default is None and not ing.required:
            # Agent-managed state captured by pipeline steps — omit from table
            agent_managed.append(ing_name)
            continue
        input_rows.append(
            f"| {ing_name} | {ing.description} | {_format_ingredient_default(ing)} |"
        )
    inputs_table = "\n".join(input_rows)
    if agent_managed:
        inputs_table += f"\n\nAgent-managed: {', '.join(agent_managed)}"

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
        f"### Inputs\n"
        f"{inputs_table}"
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
