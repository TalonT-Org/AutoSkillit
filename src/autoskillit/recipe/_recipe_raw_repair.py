"""Raw-YAML repair for resolved recipe guards."""

from __future__ import annotations

from typing import Any

from autoskillit.core import compose_yaml, is_yaml_mapping_node
from autoskillit.recipe._recipe_composition import _resolve_skip_redirects
from autoskillit.recipe.schema import RecipeStep


def _resolve_skip_guards_in_content(
    raw: str,
    resolutions: dict[str, bool | None],
    original_steps: dict[str, RecipeStep],
) -> str:
    """Apply skip_when_false resolution decisions to the raw YAML content string.

    For each resolved step:
    - Truthy (step kept): strip skip_when_false and optional: true lines so the step
      appears mandatory.
    - Falsy (step pruned): strip the entire step block.
    """
    if not resolutions:
        return raw
    root = compose_yaml(raw)
    if not is_yaml_mapping_node(root):
        raise ValueError("Guarded recipe must be a YAML mapping")
    steps_node = None
    for key_node, value_node in root.value:
        if getattr(key_node, "value", None) == "steps":
            steps_node = value_node
            break
    if not is_yaml_mapping_node(steps_node):
        raise ValueError("Guarded recipe requires a block-style top-level steps mapping")
    if getattr(steps_node, "flow_style", False):
        raise ValueError("Guarded recipe does not support a flow-style top-level steps mapping")

    counts: dict[int, int] = {}
    expanded: set[int] = set()

    def count_nodes(node: Any) -> None:
        identity = id(node)
        counts[identity] = counts.get(identity, 0) + 1
        if identity in expanded:
            return
        expanded.add(identity)
        value = getattr(node, "value", None)
        if isinstance(value, list):
            for item in value:
                if isinstance(item, tuple):
                    count_nodes(item[0])
                    count_nodes(item[1])
                else:
                    count_nodes(item)

    count_nodes(root)

    def descendants(node: Any, visited: set[int]) -> list[Any]:
        identity = id(node)
        if identity in visited:
            return []
        visited.add(identity)
        found = [node]
        value = getattr(node, "value", None)
        if isinstance(value, list):
            for item in value:
                if isinstance(item, tuple):
                    found.extend(descendants(item[0], visited))
                    found.extend(descendants(item[1], visited))
                else:
                    found.extend(descendants(item, visited))
        return found

    if any(counts.get(id(node), 0) > 1 for node in descendants(steps_node, set())):
        raise ValueError("Guarded recipe does not support aliases within steps")

    def line_start(index: int) -> int:
        return raw.rfind("\n", 0, index) + 1

    def line_end(index: int) -> int:
        newline = raw.find("\n", index)
        return len(raw) if newline < 0 else newline + 1

    redirects = _resolve_skip_redirects(original_steps, resolutions)
    entries = list(steps_node.value)
    mapping_end = (
        len(raw)
        if steps_node.end_mark.index >= len(raw)
        else line_start(steps_node.end_mark.index)
    )
    blocks: dict[str, str] = {}
    order: list[str] = []
    route_fields = {
        "on_success",
        "on_failure",
        "on_context_limit",
        "on_rate_limit",
        "on_exhausted",
        "route",
    }
    for index, (name_node, step_node) in enumerate(entries):
        name = str(name_node.value)
        order.append(name)
        start = line_start(name_node.start_mark.index)
        end = (
            line_start(entries[index + 1][0].start_mark.index)
            if index + 1 < len(entries)
            else mapping_end
        )
        if resolutions.get(name) is False:
            continue
        edits: list[tuple[int, int, str]] = []
        for key_node, value_node in step_node.value:
            key = str(key_node.value)
            if key in {"skip_when_false", "on_skip"} or (
                key == "optional" and resolutions.get(name) is True
            ):
                edits.append(
                    (
                        line_start(key_node.start_mark.index),
                        line_end(value_node.end_mark.index),
                        "",
                    )
                )

        def collect_route_edits(node: Any, parent_key: str | None = None) -> None:
            value = getattr(node, "value", None)
            if not isinstance(value, list):
                return
            for item in value:
                if isinstance(item, tuple):
                    key_node, value_node = item
                    key = str(getattr(key_node, "value", ""))
                    scalar = getattr(value_node, "value", None)
                    is_legacy_route = parent_key == "routes"
                    if isinstance(scalar, str) and (key in route_fields or is_legacy_route):
                        replacement = redirects.get(scalar)
                        if replacement is not None:
                            style = getattr(value_node, "style", None)
                            rendered = replacement
                            if style == "'":
                                rendered = "'" + replacement.replace("'", "''") + "'"
                            elif style == '"':
                                rendered = (
                                    '"'
                                    + replacement.replace("\\", "\\\\").replace('"', '\\"')
                                    + '"'
                                )
                            edits.append(
                                (value_node.start_mark.index, value_node.end_mark.index, rendered)
                            )
                    collect_route_edits(value_node, key)
                else:
                    collect_route_edits(item, parent_key)

        collect_route_edits(step_node)
        ordered_edits = sorted(edits)
        for previous, current in zip(ordered_edits, ordered_edits[1:]):
            if previous[1] > current[0]:
                raise ValueError(f"Overlapping YAML edit spans in step '{name}'")
        block = raw[start:end]
        for edit_start, edit_end, replacement in sorted(edits, reverse=True):
            block = block[: edit_start - start] + replacement + block[edit_end - start :]
        blocks[name] = block

    surviving = [name for name in order if name in blocks]
    if order and order[0] in redirects:
        entry = redirects[order[0]]
        surviving = [entry, *[name for name in surviving if name != entry]]
    content_start = line_start(entries[0][0].start_mark.index)
    content_end = mapping_end
    return raw[:content_start] + "".join(blocks[name] for name in surviving) + raw[content_end:]
