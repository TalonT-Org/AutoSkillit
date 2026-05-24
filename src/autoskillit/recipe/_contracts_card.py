"""Recipe contract card generation, loading, and validation."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from autoskillit.core import (
    SKILL_TOOLS,
    Severity,
    atomic_write,
    dump_yaml_str,
    get_logger,
    load_yaml,
)
from autoskillit.recipe._contracts_manifest import (
    classify_step_arg_style,
    compute_skill_hash,
    count_positional_args,
    extract_context_refs,
    extract_input_refs,
    get_skill_contract,
    load_bundled_manifest,
    resolve_skill_name,
)
from autoskillit.recipe._contracts_types import (
    BlockFingerprint,
    RecipeCard,
)

logger = get_logger(__name__)


def _compute_block_fingerprint(block: Any) -> BlockFingerprint:
    """Compute a structural fingerprint for a RecipeBlock.

    The fingerprint captures:
    - ``member_count``: total number of member steps
    - ``tool_counts_sorted``: per-tool counts sorted by tool name (stable comparison)
    - ``gh_api_occurrences``: total 'gh api' shell substring occurrences
    - ``capture_names_hash``: sha256 of the sorted set of capture key names across members
    - ``entry_step`` / ``exit_step``: the block's entry and exit step names
    """
    all_capture_names: list[str] = []
    for step in block.members:
        all_capture_names.extend(sorted((step.capture or {}).keys()))
    sorted_capture_names = sorted(all_capture_names)
    capture_names_hash = (
        f"sha256:{hashlib.sha256(' '.join(sorted_capture_names).encode()).hexdigest()}"
    )
    tool_counts_sorted = tuple(sorted(block.tool_counts.items()))
    return BlockFingerprint(
        name=block.name,
        member_count=len(block.members),
        tool_counts_sorted=tool_counts_sorted,
        gh_api_occurrences=block.gh_api_occurrences,
        capture_names_hash=capture_names_hash,
        entry_step=block.entry,
        exit_step=block.exit,
    )


def _generate_recipe_card_for_recipe(recipe: Any) -> RecipeCard:
    """Generate a RecipeCard from a Recipe object (no disk write).

    Used by the block fingerprint drift detection path in ``check_contract_staleness``
    and by tests that want a ``RecipeCard`` with populated ``block_fingerprints``.
    Uses a deferred import of ``_build_step_graph`` and ``extract_blocks`` to avoid
    a circular import (``_analysis.py`` imports from ``contracts.py``).
    """
    from autoskillit.recipe._analysis import _build_step_graph, extract_blocks

    step_graph = _build_step_graph(recipe)
    blocks = extract_blocks(recipe, step_graph)
    fingerprints = tuple(_compute_block_fingerprint(b) for b in blocks)
    manifest = load_bundled_manifest()
    return RecipeCard(
        recipe_source_hash=None,
        bundled_manifest_version=manifest.get("version", ""),
        skill_hashes={},
        skills={},
        dataflow=[],
        block_fingerprints=fingerprints,
    )


def generate_recipe_card(
    pipeline_path: Path | str | Any,
    recipes_dir: Path | str | None = None,
    *,
    skills_dir: Path | str | None = None,
) -> dict | RecipeCard:
    """Generate a recipe card file for a recipe.

    Walks each step, resolves skill names, looks up contracts in the manifest,
    computes SKILL.md hashes, and builds dataflow entries. Writes the recipe card
    to ``recipes_dir / "contracts" / "{pipeline_stem}.yaml"``.

    When ``skills_dir`` is None, skill hashes are not computed and ``skill_hashes``
    in the generated card will be empty.

    When ``pipeline_path`` is a ``Recipe`` object, returns a ``RecipeCard`` with
    populated ``block_fingerprints`` (no disk write).  The path-based form returns
    the contract data dict directly (no disk re-read required by callers).
    """
    if hasattr(pipeline_path, "steps"):
        return _generate_recipe_card_for_recipe(pipeline_path)

    if recipes_dir is None:
        raise ValueError("recipes_dir required when pipeline_path is a file path")
    pipeline_path = Path(pipeline_path)
    recipes_dir = Path(recipes_dir)

    from autoskillit.recipe.io import _parse_recipe
    from autoskillit.recipe.staleness_cache import compute_recipe_hash

    recipe_hash = compute_recipe_hash(pipeline_path)
    data = load_yaml(pipeline_path)
    recipe = _parse_recipe(data)
    manifest = load_bundled_manifest()

    skill_hashes: dict[str, str] = {}
    skills: dict[str, dict] = {}
    dataflow: list[dict] = []

    ingredient_names = set(recipe.ingredients.keys())
    available: set[str] = set(ingredient_names)

    for step_name, step in recipe.steps.items():
        entry: dict[str, Any] = {
            "step": step_name,
            "available": sorted(available),
            "required": [],
            "produced": [],
        }

        if step.tool in SKILL_TOOLS:
            skill_cmd = step.with_args.get("skill_command", "")
            skill_name = resolve_skill_name(skill_cmd)
            if skill_name:
                contract = get_skill_contract(skill_name, manifest)
                if contract:
                    skill_entry: dict[str, Any] = {
                        "inputs": [
                            {
                                "name": i.name,
                                "type": i.type,
                                "required": i.required,
                                "recommended": i.recommended,
                            }
                            for i in contract.inputs
                        ],
                        "outputs": [{"name": o.name, "type": o.type} for o in contract.outputs],
                        "expected_output_patterns": contract.expected_output_patterns,
                        "pattern_examples": contract.pattern_examples,
                    }
                    if contract.write_behavior is not None:
                        skill_entry["write_behavior"] = contract.write_behavior
                    if contract.write_expected_when:
                        skill_entry["write_expected_when"] = contract.write_expected_when
                    if contract.read_only:
                        skill_entry["read_only"] = True
                    skills[skill_name] = skill_entry
                    all_input_names = {i.name for i in contract.inputs}
                    arg_style = classify_step_arg_style(skill_cmd, all_input_names)
                    if arg_style == "positional_text":
                        entry["required"] = []
                        entry["positional_args"] = count_positional_args(skill_cmd)
                    elif arg_style == "positional_template":
                        entry["required"] = []
                        entry["positional_mapping"] = True
                    else:
                        ctx_refs = extract_context_refs(step)
                        inp_refs = extract_input_refs(step)
                        referenced = ctx_refs | inp_refs
                        entry["required"] = [
                            i.name
                            for i in contract.inputs
                            if i.required and i.name not in referenced
                        ]
                    if skill_name not in skill_hashes and skills_dir is not None:
                        skill_hashes[skill_name] = compute_skill_hash(
                            skill_name, skills_dir=Path(skills_dir)
                        )

        produced = list(step.capture.keys())
        entry["produced"] = produced
        available.update(produced)
        dataflow.append(entry)

    contract_data = {
        "recipe_source_hash": recipe_hash,
        "bundled_manifest_version": manifest["version"],
        "skill_hashes": skill_hashes,
        "skills": skills,
        "dataflow": dataflow,
    }

    card_path = recipes_dir / "contracts" / f"{pipeline_path.stem}.yaml"
    card_path.parent.mkdir(parents=True, exist_ok=True)
    new_content = dump_yaml_str(contract_data, default_flow_style=False, sort_keys=False)
    if card_path.exists():
        existing = card_path.read_text(encoding="utf-8")
        if existing == new_content:
            return contract_data
    atomic_write(card_path, new_content)
    return contract_data


def load_recipe_card(recipe_name: str, recipes_dir: Path | str) -> dict | None:
    """Load a previously generated recipe card file.

    Returns the parsed YAML dict, or None if the recipe card doesn't exist.
    """
    contract_path = Path(recipes_dir) / "contracts" / f"{recipe_name}.yaml"
    if not contract_path.is_file():
        return None
    return load_yaml(contract_path)


def validate_recipe_cards(recipe: Any, contract: dict[str, Any]) -> list[dict[str, str]]:
    """Validate recipe dataflow using a pre-computed recipe card.

    For each dataflow entry, checks that all required inputs are in the
    available set at that point in the recipe.

    Returns a list of finding dicts with keys: rule, severity, step, message.
    """
    findings: list[dict[str, str]] = []
    for entry in contract.get("dataflow", []):
        available = set(entry.get("available", []))
        for req in entry.get("required", []):
            if req in available:
                findings.append(
                    {
                        "rule": "contract-unreferenced-required",
                        "severity": Severity.ERROR.value,
                        "step": entry.get("step", ""),
                        "message": (
                            f"Step '{entry['step']}' requires '{req}' which is available "
                            f"in context as '${{{{ context.{req} }}}}', but the step does "
                            f"not reference it in the skill_command."
                        ),
                    }
                )
            else:
                findings.append(
                    {
                        "rule": "contract-unsatisfied-input",
                        "severity": Severity.ERROR.value,
                        "step": entry.get("step", ""),
                        "message": (
                            f"Step '{entry['step']}' requires '{req}' but it is not "
                            f"available at this point in the recipe."
                        ),
                    }
                )
    return findings
