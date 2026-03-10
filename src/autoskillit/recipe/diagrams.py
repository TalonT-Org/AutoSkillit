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
from autoskillit.recipe._analysis import _is_infrastructure_step, build_recipe_graph
from autoskillit.recipe.schema import Recipe
from autoskillit.recipe.staleness_cache import compute_recipe_hash

# Diagram format version — bump when rendering logic changes so that
# existing diagrams are flagged stale even if the recipe YAML hasn't changed.
_DIAGRAM_FORMAT_VERSION = "v6"


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
    note: str = ""  # carries recipe step note for semantic FOR EACH detection
    is_confirm: bool = False  # True when action == "confirm"


@dataclass
class _StepClassification:
    """Classification of recipe steps by their role in the FOR EACH iteration cycle."""

    main_chain: frozenset[str]  # Tight-cycle steps → inline chain tokens
    side_legs: frozenset[str]  # Failure/context-limit only → second row below chain
    routing_blocks: frozenset[str]  # Pure-routing multi-way steps → └── footer block
    hidden: frozenset[str]  # Infrastructure + terminal → omitted from diagram


@dataclass
class _LayoutResult:
    """Complete layout for visual rendering."""

    steps: list[_LayoutStep]
    back_edges: list[tuple[str, str]]  # (from_step, to_step)
    # Indices in the visible (non-infra, non-terminal) step list for the
    # FOR EACH iteration block, or None if no loop is detected.
    for_each_range: tuple[int, int] | None = None
    for_each_label: str = "FOR EACH:"  # descriptive label for the FOR EACH box header
    classification: _StepClassification | None = None  # step role classification


# ---------------------------------------------------------------------------
# Layout computation
# ---------------------------------------------------------------------------


_PLAN_ITERATION_KEYWORDS: frozenset[str] = frozenset(
    {
        "plan_parts",
        "plan_part",
        "for each plan",
        "for each group",
        "groups mode",
    }
)


def _is_plan_iteration_note(note: str) -> bool:
    """Return True if a step note signals plan-parts or groups iteration intent."""
    note_lower = note.lower()
    return any(kw in note_lower for kw in _PLAN_ITERATION_KEYWORDS)


def _derive_for_each_label(span_steps: list[_LayoutStep]) -> str:
    """Derive the descriptive FOR EACH label from step notes within the loop span."""
    notes_combined = " ".join(s.note.lower() for s in span_steps)
    if "groups mode" in notes_combined or "for each group" in notes_combined:
        return "FOR EACH GROUP / PLAN PART:"
    if "plan_parts" in notes_combined or "plan_part" in notes_combined:
        return "FOR EACH PLAN PART:"
    return "FOR EACH:"


def _classify_steps(recipe: Recipe) -> _StepClassification:
    """Classify recipe steps by role using igraph's subcomponent analysis.

    Builds an igraph.Graph from the recipe, projects a success-path-only subgraph,
    then uses igraph.subcomponent(start, OUT) to find all success-reachable steps,
    plus force-includes the back-edge source. No subcomponent(IN) intersection is
    applied — see inline comment at the assignment site for the rationale. Both
    start and end vertices of the back-edge are always included.

    The semantic gate (plan-iteration note) is unchanged: only back-edges whose
    source step carries a plan-iteration keyword in its note qualify.

    Args:
        recipe: The loaded Recipe dataclass.

    Returns:
        ``_StepClassification`` with classified step sets.
    """
    g = build_recipe_graph(recipe)

    step_names = list(recipe.steps.keys())
    name_to_id: dict[str, int] = {name: i for i, name in enumerate(step_names)}

    # Visible step ordering (non-infra, non-terminal) — for back-edge span comparison
    vis_index: dict[str, int] = {
        name: i
        for i, (name, step) in enumerate(recipe.steps.items())
        if not _is_infrastructure_step(step) and step.action != "stop"
    }

    # Success-only subgraph: keep all vertices so IDs remain stable for cross-referencing
    success_edge_ids = [e.index for e in g.es if e["edge_type"] == "success"]
    g_success = g.subgraph_edges(success_edge_ids, delete_vertices=False)

    # Find the best qualifying back-edge and compute tight cycle via igraph subcomponent
    best_span = 1
    best_chain: frozenset[str] = frozenset()

    for name, step in recipe.steps.items():
        vi = vis_index.get(name)
        if vi is None:
            continue

        # Semantic gate: require plan-iteration intent in the back-edge source note
        if not _is_plan_iteration_note(step.note or ""):
            continue

        # Collect back-edge targets (routes to earlier visible steps)
        back_targets: list[str] = []
        if step.on_success:
            t_vi = vis_index.get(step.on_success)
            if t_vi is not None and t_vi < vi:
                back_targets.append(step.on_success)
        if step.on_result:
            sr = step.on_result
            for cond in sr.conditions or []:
                t_vi = vis_index.get(cond.route)
                if t_vi is not None and t_vi < vi:
                    back_targets.append(cond.route)
            for target in (sr.routes or {}).values():
                t_vi = vis_index.get(target)
                if t_vi is not None and t_vi < vi:
                    back_targets.append(target)

        for target in back_targets:
            span = vi - vis_index[target]
            if span <= best_span:
                continue

            # igraph: cycle members = all vertices reachable from start via success edges,
            # plus force-include end (which routes back via on_result in existing recipes).
            # Deliberately no subcomponent(end, IN) intersection: when the back-edge uses
            # on_result, subcomponent(end, IN) in g_success = {end} only (no step has
            # on_success: end), so intersecting collapses member_ids to empty. The formula
            # member_ids = reachable_from_start | {end_id} correctly mirrors _tight_cycle_bfs
            # (BFS from start via success, force-add end). Single-on_success grammar also
            # guarantees no branching in g_success, so dead-end branches cannot exist.
            start_id = name_to_id[target]
            end_id = name_to_id[name]

            member_ids = set(g_success.subcomponent(start_id, mode="OUT"))
            member_ids.add(end_id)  # Force-include: end routes via on_result, not on_success

            chain = frozenset(g.vs[vid]["name"] for vid in member_ids)
            best_span = span
            best_chain = chain

    main_chain = best_chain

    # routing_blocks: pure route-action steps in main_chain with multiple on_result conditions
    routing_blocks_set: set[str] = set()
    for mc_name in main_chain:
        mc_step = recipe.steps.get(mc_name)
        if mc_step is None:
            continue
        if mc_step.action == "route" and mc_step.on_result is not None:
            nr = mc_step.on_result
            num_routes = (
                len(nr.conditions) if nr.conditions else (len(nr.routes) if nr.routes else 0)
            )
            if num_routes > 1:
                routing_blocks_set.add(mc_name)

    routing_blocks = frozenset(routing_blocks_set)

    # side_legs: steps reachable from main_chain via on_failure or on_context_limit only
    side_legs_set: set[str] = set()
    for mc_name in main_chain:
        mc_step = recipe.steps.get(mc_name)
        if mc_step is None:
            continue
        for leg_target in [mc_step.on_failure, mc_step.on_context_limit]:
            if leg_target and leg_target in recipe.steps and leg_target not in main_chain:
                side_legs_set.add(leg_target)

    side_legs = frozenset(side_legs_set)

    # hidden: infrastructure steps and terminal (action == "stop") steps
    hidden_set: set[str] = set()
    for hname, hstep in recipe.steps.items():
        if _is_infrastructure_step(hstep) or hstep.action == "stop":
            hidden_set.add(hname)

    hidden = frozenset(hidden_set)

    return _StepClassification(
        main_chain=main_chain,
        side_legs=side_legs,
        routing_blocks=routing_blocks,
        hidden=hidden,
    )


def _compute_layout(recipe: Any) -> _LayoutResult:
    """Compute visual layout from a Recipe dataclass."""

    # Precompute visible step ordering for back-edge detection.
    # A back-edge exists when a visible step routes to an earlier visible step.
    # Visible = non-infrastructure, non-terminal. Uses the same criteria as
    # _classify_steps.hidden so the two computations agree.
    vis_pos: dict[str, int] = {
        name: i
        for i, (name, step) in enumerate(recipe.steps.items())
        if not _is_infrastructure_step(step) and step.action != "stop"
    }

    layout_steps: list[_LayoutStep] = []
    back_edges: list[tuple[str, str]] = []

    for step_name, step in recipe.steps.items():
        cur_pos = vis_pos.get(step_name, -1)

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
        is_confirm = step.action == "confirm"
        infra = _is_infrastructure_step(step)

        ls = _LayoutStep(
            name=step_name,
            tool=tool_val,
            is_terminal=is_terminal,
            is_infrastructure=infra,
            is_confirm=is_confirm,
            message=step.message or "",
            on_success=step.on_success,
            on_failure=step.on_failure,
            on_context_limit=step.on_context_limit,
            retries=step.retries if not is_terminal else 0,
            on_exhausted=step.on_exhausted if not is_terminal else "escalate",
            skip_when_false=step.skip_when_false,
            note=step.note or "",
        )

        if step.on_success and 0 <= vis_pos.get(step.on_success, -1) < cur_pos:
            ls.is_back_edge_success = True
            back_edges.append((step_name, step.on_success))
        if step.on_failure and 0 <= vis_pos.get(step.on_failure, -1) < cur_pos:
            ls.is_back_edge_failure = True
            back_edges.append((step_name, step.on_failure))

        if step.on_result is not None:
            sr = step.on_result
            if sr.conditions:
                for cond in sr.conditions:
                    when_str = cond.when if cond.when else "(default)"
                    is_back = 0 <= vis_pos.get(cond.route, -1) < cur_pos
                    ls.on_result_conditions.append((when_str, cond.route, is_back))
                    if is_back:
                        back_edges.append((step_name, cond.route))
            elif sr.routes:
                for key, target in sr.routes.items():
                    is_back = 0 <= vis_pos.get(target, -1) < cur_pos
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

    # Phase 3: Semantic FOR EACH detection.
    # Use _classify_steps (which calls _tight_cycle_bfs) for precise cycle membership.
    # This replaces the index-span approach which was over-inclusive: it pulled
    # side-leg steps (retry_worktree, fix) into the horizontal chain.
    classification = _classify_steps(recipe)

    visible = [s for s in layout_steps if s.name not in classification.hidden]
    vis_names_list = [s.name for s in visible]

    chain_names = [n for n in vis_names_list if n in classification.main_chain]
    for_each_range: tuple[int, int] | None = None
    best_label = "FOR EACH:"

    if chain_names:
        fe_start_idx = vis_names_list.index(chain_names[0])
        fe_end_idx = vis_names_list.index(chain_names[-1])
        for_each_range = (fe_start_idx, fe_end_idx)
        span_steps = visible[fe_start_idx : fe_end_idx + 1]
        best_label = _derive_for_each_label(span_steps)

    return _LayoutResult(
        steps=layout_steps,
        back_edges=back_edges,
        for_each_range=for_each_range,
        for_each_label=best_label,
        classification=classification,
    )


# ---------------------------------------------------------------------------
# Visual ASCII flow renderer
# ---------------------------------------------------------------------------


def _append_step(step: _LayoutStep, lines: list[str], prefix: str) -> None:
    """Append rendering lines for a single step onto *lines*."""
    if step.is_confirm:
        # Confirm step: ❓ prefix with yes/no branch labels
        lines.append(f"{prefix}❓ {step.name}")
        if step.on_success:
            lines.append(f"{prefix}│  ✓ yes  → {step.on_success}")
        if step.on_failure:
            lines.append(f"{prefix}│  ✗ no   → {step.on_failure}")
        return
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


def _render_for_each_chain(
    inner_steps: list[_LayoutStep],
    lines: list[str],
    label: str = "FOR EACH:",
    classification: _StepClassification | None = None,
) -> None:
    """Render FOR EACH inner steps as a horizontal chain with side-leg failure branches.

    When *classification* is provided, only tight-cycle chain steps appear as
    inline tokens. Pure-routing steps (e.g. next_or_done) are rendered as a
    ``└──`` footer block after the closing ``└────┘`` line.

    Produces:
        ┌────┤ FOR EACH PLAN PART:
        │    │
        │    step_a (retry ×∞) ─── step_b (retry ×3) ─── step_c (retry ×∞)
        │         │
        │         ✗ failure → escalate
        │
        └────┘
        │         └── routing_step: condition  → target ↑
        │                           condition  → target

    This function is structurally incapable of producing vertical step blocks
    (│  ↓ success → ...) because it never calls _append_step(). The horizontal
    layout is the only code path.
    """
    lines.append(f"┌────┤ {label}")
    lines.append("│    │")

    # Determine which steps appear in the chain line.
    # With classification: main_chain steps excluding pure routing_blocks.
    # Without classification (fallback): all inner_steps.
    if classification is not None:
        chain_step_names = classification.main_chain - classification.routing_blocks
        chain_steps = [s for s in inner_steps if s.name in chain_step_names]
    else:
        chain_steps = inner_steps

    # Build horizontal chain tokens: join step names with ─── connectors.
    chain_tokens: list[str] = []
    for step in chain_steps:
        if step.skip_when_false:
            token = f"[{step.name}]"
        else:
            token = step.name
        if step.retries == 0:
            token += " (retry ×∞)"
        else:
            token += f" (retry ×{step.retries})"
        if step.is_back_edge_success or step.is_back_edge_failure:
            token += " ↑"
        chain_tokens.append(token)

    chain_line = " ─── ".join(chain_tokens)
    lines.append(f"│    {chain_line}")

    # Side-leg failure branches: one block per chain step that has a failure route.
    # Each side-leg hangs below the chain line, indented to the step's position.
    indent_base = 4  # "│    " prefix = 4 chars after the leading │
    cursor = 0
    for idx, step in enumerate(chain_steps):
        failure_routes: list[str] = []
        if step.on_failure:
            suf = " ↑" if step.is_back_edge_failure else ""
            failure_routes.append(f"✗ failure → {step.on_failure}{suf}")
        if step.on_context_limit:
            failure_routes.append(f"⌛ context limit → {step.on_context_limit}")
        for cond_str, target, is_back in step.on_result_conditions:
            suf = " ↑" if is_back else ""
            failure_routes.append(f"{cond_str} → {target}{suf}")
        if failure_routes:
            pad = " " * (indent_base + cursor + 1)
            lines.append(f"│{pad}│")
            for route in failure_routes:
                lines.append(f"│{pad}{route}")
        # Advance cursor by token length + " ─── " separator (5 chars)
        cursor += len(chain_tokens[idx]) + 5

    lines.append("│")
    lines.append("└────┘")

    # Routing block footer: pure-routing multi-way steps rendered as └── blocks
    # after the closing box line.
    if classification is not None:
        routing_block_steps = [s for s in inner_steps if s.name in classification.routing_blocks]
        for rb_step in routing_block_steps:
            if rb_step.on_result_conditions:
                # Continuation lines must align with the condition column on the first line.
                # First-line prefix: "│         └── {name}: " = 1 + 9 + 4 + len(name) + 2 chars.
                # Continuation indent after "│": 9 + 4 + len(name) + 2 = 15 + len(name) spaces.
                continuation_indent = " " * (15 + len(rb_step.name))
                first = True
                for cond_str, target, is_back in rb_step.on_result_conditions:
                    suf = " ↑" if is_back else ""
                    if first:
                        lines.append(f"│         └── {rb_step.name}: {cond_str}  → {target}{suf}")
                        first = False
                    else:
                        lines.append(f"│{continuation_indent}{cond_str}  → {target}{suf}")


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
            assert fe_end is not None
            inner_steps = visible[fe_start : fe_end + 1]
            _render_for_each_chain(
                inner_steps,
                lines,
                label=layout.for_each_label,
                classification=layout.classification,
            )
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
                lines.append(f'{term.name}  "{term.message}"')
            else:
                lines.append(term.name)

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


def generate_recipe_diagram(
    pipeline_path: Path,
    recipes_dir: Path,
    out_dir: Path | None = None,
) -> str:
    """Generate a visual flow diagram for the recipe at *pipeline_path*.

    Reads the recipe YAML, builds a spec-compliant visual ASCII flow diagram
    and 3-column Inputs table, embeds SHA-256 hash and format version for
    staleness detection, and writes the result atomically to the output
    directory.

    Args:
        pipeline_path: Absolute path to the recipe ``.yaml`` file.
        recipes_dir: Root recipes directory (used for loading the recipe).
        out_dir: Directory to write the diagram file into. Defaults to
            ``recipes_dir/diagrams/`` when not provided.

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
    flow_line = (
        f"**Flow:** {recipe.summary}\n\n" if recipe.summary and recipe.summary.strip() else ""
    )
    diagram = (
        f"<!-- autoskillit-recipe-hash: {recipe_hash} -->\n"
        f"<!-- autoskillit-diagram-format: {_DIAGRAM_FORMAT_VERSION} -->\n"
        f"## {recipe.name}\n"
        f"{recipe.description}\n"
        f"\n"
        f"{flow_line}"
        f"### Graph\n"
        f"{flow_diagram}\n"
        f"\n"
        f"### Inputs\n"
        f"{inputs_table}"
        f"{rules_section}\n"
    )

    # Write atomically to out_dir/{stem}.md (defaults to recipes_dir/diagrams/)
    _out_dir = out_dir if out_dir is not None else recipes_dir / "diagrams"
    out_path = _out_dir / f"{pipeline_path.stem}.md"
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
