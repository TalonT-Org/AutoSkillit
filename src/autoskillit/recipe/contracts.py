"""Recipe contract types, manifest loading, card generation, and staleness detection."""

from __future__ import annotations

import dataclasses
import hashlib
import re
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from autoskillit.core import SkillResolver

from autoskillit.core import (
    SKILL_TOOLS,
    Severity,
    atomic_write,
    dump_yaml_str,
    get_logger,
    load_yaml,
    pkg_root,
)
from autoskillit.recipe.io import _parse_recipe
from autoskillit.recipe.schema import Recipe, RecipeBlock
from autoskillit.recipe.staleness_cache import (
    StalenessEntry,
    compute_recipe_hash,
    read_staleness_cache,
    write_staleness_cache,
)

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Contract data types
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class SkillInput:
    name: str
    type: str
    required: bool
    recommended: bool = False
    nullable: bool = True


@dataclasses.dataclass
class SkillOutput:
    name: str
    type: str


@dataclasses.dataclass(frozen=True)
class ResultFieldSpec:
    name: str
    type: str
    required: bool = True


@dataclasses.dataclass
class SkillContract:
    inputs: list[SkillInput]
    outputs: list[SkillOutput]
    expected_output_patterns: list[str] = dataclasses.field(default_factory=list)
    pattern_examples: list[str] = dataclasses.field(default_factory=list)
    write_behavior: str | None = None
    write_expected_when: list[str] = dataclasses.field(default_factory=list)
    read_only: bool = False
    result_fields: list[ResultFieldSpec] = dataclasses.field(default_factory=list)


@dataclasses.dataclass
class StaleItem:
    skill: str
    reason: str  # "version_mismatch" | "hash_mismatch"
    stored_value: str
    current_value: str


@dataclasses.dataclass
class DataFlowEntry:
    step: str
    available: list[str]
    required: list[str]
    produced: list[str]


@dataclasses.dataclass(frozen=True)
class BlockFingerprint:
    """Structural fingerprint for a named recipe block.

    Used by ``check_contract_staleness`` to detect silent composition drift:
    any change to a block's member count, tool usage, gh api call count, or
    capture names produces a fingerprint mismatch (reason='block_composition_drift').
    """

    name: str
    member_count: int
    tool_counts_sorted: tuple[tuple[str, int], ...]  # sorted by tool name for stable comparison
    gh_api_occurrences: int
    capture_names_hash: str  # sha256hex of sorted capture key names across all members
    entry_step: str
    exit_step: str


@dataclasses.dataclass
class RecipeCard:
    generated_at: str
    bundled_manifest_version: str
    skill_hashes: dict[str, str]
    skills: dict[str, SkillContract]
    dataflow: list[DataFlowEntry]
    block_fingerprints: tuple[BlockFingerprint, ...] = dataclasses.field(
        default_factory=tuple  # type: ignore[arg-type]
    )


# ---------------------------------------------------------------------------
# Regex patterns
# ---------------------------------------------------------------------------

_SKILL_NAME_RE = re.compile(r"/autoskillit:([\w-]+)")
_CONTEXT_REF_RE = re.compile(r"\$\{\{\s*context\.(\w+)\s*\}\}")
INPUT_REF_RE = re.compile(r"\$\{\{\s*inputs\.(\w+)\s*\}\}")
_TEMPLATE_REF_RE = re.compile(r"\$\{\{[^}]+\}\}")
RESULT_CAPTURE_RE = re.compile(r"\$\{\{\s*result\.([\w-]+)\s*\}\}")


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
    # Reject names truncated by a bash-style {placeholder} token immediately
    # following the match (e.g. "/autoskillit:exp-lens-{slug}" extracts
    # "exp-lens-" but is dynamic — the true name is resolved at runtime).
    if match.end() < len(skill_command) and skill_command[match.end()] == "{":
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
            recommended=inp.get("recommended", False),
        )
        for inp in skill_data.get("inputs", [])
    ]
    outputs = [
        SkillOutput(name=out["name"], type=out["type"]) for out in skill_data.get("outputs", [])
    ]
    patterns = skill_data.get("expected_output_patterns", [])
    examples = skill_data.get("pattern_examples", [])
    write_behavior = skill_data.get("write_behavior")
    write_expected_when = skill_data.get("write_expected_when", [])
    read_only = bool(skill_data.get("read_only", False))
    try:
        result_fields = [
            ResultFieldSpec(
                name=rf["name"],
                type=rf["type"],
                required=rf.get("required", True),
            )
            for rf in skill_data.get("result_fields", [])
        ]
    except KeyError as exc:
        raise KeyError(
            f"Malformed result_fields entry for skill '{skill_name}': missing key {exc}"
        ) from exc
    return SkillContract(
        inputs=inputs,
        outputs=outputs,
        expected_output_patterns=patterns,
        pattern_examples=examples,
        write_behavior=write_behavior,
        write_expected_when=write_expected_when,
        read_only=read_only,
        result_fields=result_fields,
    )


def get_callable_contract(
    dotted_path: str, manifest: dict[str, Any] | None = None
) -> SkillContract | None:
    """Look up a run_python callable in the manifest and return a SkillContract.

    Callable contracts live under the ``callable_contracts`` top-level key in
    skill_contracts.yaml, keyed by the fully-qualified dotted Python path
    (e.g. ``autoskillit.smoke_utils.check_review_loop``).
    """
    if manifest is None:
        manifest = load_bundled_manifest()
    callables = manifest.get("callable_contracts", {})
    entry = callables.get(dotted_path)
    if entry is None:
        return None
    inputs = [
        SkillInput(
            name=inp["name"],
            type=inp["type"],
            required=inp.get("required", True),
            nullable=inp.get("nullable", True),
        )
        for inp in entry.get("inputs", [])
    ]
    outputs = [SkillOutput(name=out["name"], type=out["type"]) for out in entry.get("outputs", [])]
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
        refs.update(INPUT_REF_RE.findall(str(val)))
    return refs


def extract_skill_cmd_refs(skill_command: str) -> set[str]:
    """Extract context and input ref names from a skill_command string only.

    Unlike extract_context_refs/extract_input_refs which scan all with_args,
    this scans only the skill_command string. Used to detect positional-style
    invocations where template ref names don't match named contract inputs.
    """
    ctx = set(_CONTEXT_REF_RE.findall(skill_command))
    inp = set(INPUT_REF_RE.findall(skill_command))
    return ctx | inp


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
# Block fingerprint helpers
# ---------------------------------------------------------------------------


def _compute_block_fingerprint(block: RecipeBlock) -> BlockFingerprint:
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


def _generate_recipe_card_for_recipe(recipe: Recipe) -> RecipeCard:
    """Generate a RecipeCard from a Recipe object (no disk write).

    Used by the block fingerprint drift detection path in ``check_contract_staleness``
    and by tests that want a ``RecipeCard`` with populated ``block_fingerprints``.
    Uses a deferred import of ``_build_step_graph`` and ``extract_blocks`` to avoid
    a circular import (``_analysis.py`` imports from ``contracts.py``).
    """
    # Deferred import to avoid circular dependency:
    # contracts.py → _analysis.py (already safe via _analysis.py → contracts.py)
    from autoskillit.recipe._analysis import _build_step_graph, extract_blocks  # noqa: PLC0415

    step_graph = _build_step_graph(recipe)
    blocks = extract_blocks(recipe, step_graph)
    fingerprints = tuple(_compute_block_fingerprint(b) for b in blocks)
    manifest = load_bundled_manifest()
    return RecipeCard(
        generated_at=datetime.now(UTC).isoformat(),
        bundled_manifest_version=manifest.get("version", ""),
        skill_hashes={},
        skills={},
        dataflow=[],
        block_fingerprints=fingerprints,
    )


# ---------------------------------------------------------------------------
# Pipeline contract generation, loading, and validation
# ---------------------------------------------------------------------------


def generate_recipe_card(
    pipeline_path: Path | str | Recipe,
    recipes_dir: Path | str | None = None,
    *,
    skills_dir: Path | None = None,
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
    if isinstance(pipeline_path, Recipe):
        return _generate_recipe_card_for_recipe(pipeline_path)

    if recipes_dir is None:
        raise ValueError("recipes_dir required when pipeline_path is a file path")
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
                    pos_arg_count = count_positional_args(skill_cmd)
                    if pos_arg_count > 0:
                        # Positional args used — can't verify named inputs by ref.
                        # Record the count so downstream rules know args were supplied.
                        entry["required"] = []
                        entry["positional_args"] = pos_arg_count
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
    atomic_write(
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
    contract: dict[str, Any] | Recipe,
    *,
    recipe_path: Path | None = None,
    cache_path: Path | None = None,
    skills_dir: Path | None = None,
    resolver: SkillResolver | None = None,
    stored_card: RecipeCard | None = None,
) -> list[StaleItem]:
    """Check a pipeline contract for staleness against the current manifest.

    When ``stored_card`` is provided and ``contract`` is a ``Recipe``, compares
    block fingerprints from the stored card against the current recipe's blocks.
    Returns ``StaleItem`` entries with ``reason='block_composition_drift'`` for
    any block whose fingerprint has changed.  This path does not perform manifest
    or skill-hash checks — it is a pure structural comparison.

    When ``recipe_path`` and ``cache_path`` are both provided, a disk-backed
    cache keyed by recipe content hash + manifest version is consulted first.
    A cache hit with ``is_stale=False`` returns [] immediately without reading
    any SKILL.md files. Stale cache hits fall through to re-compute StaleItem
    details. The result is written back to the cache on every cache miss.

    When ``skills_dir`` is None, the bundled skills directory is used for hash
    comparison.

    When ``contract`` is a ``Recipe`` but ``stored_card`` is ``None``, no
    comparison baseline is available and [] is returned immediately.  This is
    expected during initial card generation before a stored card exists.

    Returns a list of StaleItem entries indicating what changed.
    """
    if stored_card is not None:
        recipe_obj = contract if isinstance(contract, Recipe) else None
        if recipe_obj is not None:
            current_card = _generate_recipe_card_for_recipe(recipe_obj)
            current_fps = {fp.name: fp for fp in current_card.block_fingerprints}
            stale_items: list[StaleItem] = []
            for stored_fp in stored_card.block_fingerprints:
                current_fp = current_fps.get(stored_fp.name)
                if current_fp is None:
                    stale_items.append(
                        StaleItem(
                            skill=stored_fp.name,
                            reason="block_composition_drift",
                            stored_value=repr(stored_fp),
                            current_value="(block removed)",
                        )
                    )
                elif current_fp != stored_fp:
                    stale_items.append(
                        StaleItem(
                            skill=stored_fp.name,
                            reason="block_composition_drift",
                            stored_value=repr(stored_fp),
                            current_value=repr(current_fp),
                        )
                    )
            return stale_items

    if isinstance(contract, Recipe):
        return []

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

    if skills_dir is not None:
        _resolver = None
        effective_skills_dir: Path | None = skills_dir
    else:
        if resolver is None:
            from autoskillit.workspace import DefaultSkillResolver  # noqa: PLC0415

            resolver = DefaultSkillResolver()
        _resolver = resolver
        effective_skills_dir = None
    for skill_name, stored_hash in contract.get("skill_hashes", {}).items():
        if effective_skills_dir is not None:
            current_hash = compute_skill_hash(skill_name, skills_dir=effective_skills_dir)
        else:
            if _resolver is None:
                raise RuntimeError(
                    "check_staleness called without effective_skills_dir or resolver"
                )
            info = _resolver.resolve(skill_name)
            current_hash = (
                compute_skill_hash(skill_name, skills_dir=info.path.parent.parent)
                if info is not None
                else ""
            )
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
