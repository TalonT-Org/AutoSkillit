"""Recipe contract types, manifest loading, card generation, and staleness detection."""

from __future__ import annotations

import dataclasses
import hashlib
import re
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

from autoskillit.core import (
    SKILL_TOOLS,
    Severity,
    _atomic_write,
    dump_yaml_str,
    get_logger,
    load_yaml,
    pkg_root,
)
from autoskillit.recipe.io import _parse_recipe
from autoskillit.recipe.staleness_cache import (
    StalenessEntry,
    compute_recipe_hash,
    read_staleness_cache,
    write_staleness_cache,
)
from autoskillit.workspace import bundled_skills_dir

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Contract data types
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class SkillInput:
    name: str
    type: str
    required: bool


@dataclasses.dataclass
class SkillOutput:
    name: str
    type: str


@dataclasses.dataclass
class SkillContract:
    inputs: list[SkillInput]
    outputs: list[SkillOutput]


@dataclasses.dataclass
class StaleItem:
    skill: str
    reason: str  # "version_mismatch" | "hash_mismatch"
    stored_value: str
    current_value: str


@dataclasses.dataclass
class DataflowEntry:
    step: str
    available: list[str]
    required: list[str]
    produced: list[str]


@dataclasses.dataclass
class RecipeCard:
    generated_at: str
    bundled_manifest_version: str
    skill_hashes: dict[str, str]
    skills: dict[str, SkillContract]
    dataflow: list[DataflowEntry]


# ---------------------------------------------------------------------------
# Regex patterns
# ---------------------------------------------------------------------------

_SKILL_NAME_RE = re.compile(r"/autoskillit:([\w-]+)")
_CONTEXT_REF_RE = re.compile(r"\$\{\{\s*context\.(\w+)\s*\}\}")
_INPUT_REF_RE = re.compile(r"\$\{\{\s*inputs\.(\w+)\s*\}\}")
_TEMPLATE_REF_RE = re.compile(r"\$\{\{[^}]+\}\}")
_RESULT_CAPTURE_RE = re.compile(r"\$\{\{\s*result\.([\w-]+)\s*\}\}")


# ---------------------------------------------------------------------------
# Core contract functions
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def load_bundled_manifest() -> dict[str, Any]:
    """Load the bundled skill_contracts.yaml from the package directory."""
    manifest_path = pkg_root() / "recipe" / "skill_contracts.yaml"
    return load_yaml(manifest_path)


def resolve_skill_name(skill_command: str) -> str | None:
    """Extract the skill name from a command string.

    Returns the skill name (e.g. "retry-worktree") or None if the command
    does not reference an autoskillit skill or contains dynamic expressions.
    """
    match = _SKILL_NAME_RE.search(skill_command)
    if not match:
        return None
    name = match.group(1)
    # Reject dynamic names containing template expressions
    if "${{" in name:
        return None
    return name


def get_skill_contract(skill_name: str, manifest: dict[str, Any]) -> SkillContract | None:
    """Look up a skill in the manifest and return a SkillContract."""
    skills = manifest.get("skills", {})
    skill_data = skills.get(skill_name)
    if skill_data is None:
        return None
    inputs = [
        SkillInput(
            name=inp["name"],
            type=inp["type"],
            required=inp.get("required", False),
        )
        for inp in skill_data.get("inputs", [])
    ]
    outputs = [
        SkillOutput(name=out["name"], type=out["type"]) for out in skill_data.get("outputs", [])
    ]
    return SkillContract(inputs=inputs, outputs=outputs)


def compute_skill_hash(skill_name: str, *, skills_dir: Path) -> str:
    """Compute SHA256 hash of a skill's SKILL.md file."""
    skill_md = skills_dir / skill_name / "SKILL.md"
    if not skill_md.is_file():
        return ""
    content = skill_md.read_bytes()
    return f"sha256:{hashlib.sha256(content).hexdigest()}"


def extract_context_refs(step: Any) -> set[str]:
    """Extract ${{ context.X }} references from a step's with_args."""
    refs: set[str] = set()
    for val in step.with_args.values():
        refs.update(_CONTEXT_REF_RE.findall(str(val)))
    return refs


def extract_input_refs(step: Any) -> set[str]:
    """Extract ${{ inputs.X }} references from a step's with_args."""
    refs: set[str] = set()
    for val in step.with_args.values():
        refs.update(_INPUT_REF_RE.findall(str(val)))
    return refs


def count_positional_args(skill_command: str) -> int:
    """Count positional text tokens after the skill name.

    Tokens that are template references (${{ ... }}) are excluded since
    they are handled by extract_context_refs / extract_input_refs.

    Returns 0 if there are no extra tokens after the skill name.
    """
    match = _SKILL_NAME_RE.search(skill_command)
    if not match:
        return 0
    after_skill = skill_command[match.end() :].strip()
    if not after_skill:
        return 0
    # Remove template references before counting
    without_templates = _TEMPLATE_REF_RE.sub("", after_skill).strip()
    if not without_templates:
        return 0
    return len(without_templates.split())


# ---------------------------------------------------------------------------
# Pipeline contract generation, loading, and validation
# ---------------------------------------------------------------------------


def generate_recipe_card(
    pipeline_path: Path | str,
    recipes_dir: Path | str,
    *,
    skills_dir: Path | None = None,
) -> dict:
    """Generate a recipe card file for a recipe.

    Walks each step, resolves skill names, looks up contracts in the manifest,
    computes SKILL.md hashes, and builds dataflow entries. Writes the recipe card
    to ``recipes_dir / "contracts" / "{pipeline_stem}.yaml"``.

    When ``skills_dir`` is None, skill hashes are not computed and ``skill_hashes``
    in the generated card will be empty.

    Returns the contract data dict directly (no disk re-read required by callers).
    """
    pipeline_path = Path(pipeline_path)
    recipes_dir = Path(recipes_dir)

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
                    skills[skill_name] = {
                        "inputs": [
                            {"name": i.name, "type": i.type, "required": i.required}
                            for i in contract.inputs
                        ],
                        "outputs": [{"name": o.name, "type": o.type} for o in contract.outputs],
                    }
                    if count_positional_args(skill_cmd) > 0:
                        # Positional args used — can't verify named inputs by ref
                        entry["required"] = []
                    else:
                        # Named template refs only — flag required inputs not referenced
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
                            skill_name, skills_dir=skills_dir
                        )

        produced = list(step.capture.keys())
        entry["produced"] = produced
        available.update(produced)
        dataflow.append(entry)

    contract_data = {
        "generated_at": datetime.now(UTC).isoformat(),
        "bundled_manifest_version": manifest["version"],
        "skill_hashes": skill_hashes,
        "skills": skills,
        "dataflow": dataflow,
    }

    card_path = recipes_dir / "contracts" / f"{pipeline_path.stem}.yaml"
    card_path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(
        card_path, dump_yaml_str(contract_data, default_flow_style=False, sort_keys=False)
    )
    return contract_data


def load_recipe_card(recipe_name: str, recipes_dir: Path) -> dict | None:
    """Load a previously generated recipe card file.

    Returns the parsed YAML dict, or None if the recipe card doesn't exist.
    """
    contract_path = recipes_dir / "contracts" / f"{recipe_name}.yaml"
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
            # req is a required input not referenced in the step's skill_command
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


# ---------------------------------------------------------------------------
# Staleness detection
# ---------------------------------------------------------------------------


def check_contract_staleness(
    contract: dict[str, Any],
    *,
    recipe_path: Path | None = None,
    cache_path: Path | None = None,
    skills_dir: Path | None = None,
) -> list[StaleItem]:
    """Check a pipeline contract for staleness against the current manifest.

    When ``recipe_path`` and ``cache_path`` are both provided, a disk-backed
    cache keyed by recipe content hash + manifest version is consulted first.
    A cache hit with ``is_stale=False`` returns [] immediately without reading
    any SKILL.md files. Stale cache hits fall through to re-compute StaleItem
    details. The result is written back to the cache on every cache miss.

    When ``skills_dir`` is None, the bundled skills directory is used for hash
    comparison.

    Returns a list of StaleItem entries indicating what changed.
    """
    manifest = load_bundled_manifest()
    current_version = manifest["version"]
    cached: StalenessEntry | None = None

    if recipe_path is not None and cache_path is not None:
        cached = read_staleness_cache(cache_path, recipe_path.stem)
        if cached is not None:
            current_hash = compute_recipe_hash(recipe_path)
            if cached.recipe_hash == current_hash and cached.manifest_version == current_version:
                if not cached.is_stale:
                    return []
                # stale=True cache hit: fall through to re-compute for StaleItem details

    stale: list[StaleItem] = []

    stored_version = contract.get("bundled_manifest_version", "")
    if stored_version != current_version:
        stale.append(
            StaleItem(
                skill="(manifest)",
                reason="version_mismatch",
                stored_value=stored_version,
                current_value=current_version,
            )
        )

    effective_skills_dir = skills_dir if skills_dir is not None else bundled_skills_dir()
    for skill_name, stored_hash in contract.get("skill_hashes", {}).items():
        current_hash = compute_skill_hash(skill_name, skills_dir=effective_skills_dir)
        if current_hash and stored_hash != current_hash:
            stale.append(
                StaleItem(
                    skill=skill_name,
                    reason="hash_mismatch",
                    stored_value=stored_hash,
                    current_value=current_hash,
                )
            )

    if recipe_path is not None and cache_path is not None:
        file_hash = compute_recipe_hash(recipe_path)
        # Preserve triage_result when content is unchanged (same hash+version).
        # When content changes, the prior triage is invalid and must be cleared.
        prior_triage: str | None = None
        if (
            cached is not None
            and cached.recipe_hash == file_hash
            and cached.manifest_version == current_version
        ):
            prior_triage = cached.triage_result
        write_staleness_cache(
            cache_path,
            recipe_path.stem,
            StalenessEntry(
                recipe_hash=file_hash,
                manifest_version=current_version,
                is_stale=bool(stale),
                triage_result=prior_triage,
                checked_at=datetime.now(UTC).isoformat(),
            ),
        )

    return stale


def stale_to_suggestions(stale: list[StaleItem]) -> list[dict[str, str]]:
    """Convert stale contract items to MCP suggestion dicts."""
    suggestions: list[dict[str, str]] = []
    for item in stale:
        suggestions.append(
            {
                "rule": "stale-contract",
                "severity": "warning",
                "step": item.skill,
                "skill": item.skill,
                "reason": item.reason,
                "stored_value": item.stored_value,
                "current_value": item.current_value,
                "message": (
                    f"Contract is stale: {item.reason} for "
                    f"'{item.skill}' (stored={item.stored_value}, "
                    f"current={item.current_value}). Consider "
                    f"regenerating the contract."
                ),
            }
        )
    return suggestions
