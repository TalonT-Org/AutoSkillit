"""Recipe contract manifest loading and ref extraction utils."""

from __future__ import annotations

import hashlib
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, cast

if TYPE_CHECKING:
    pass

from autoskillit.core import (
    InputSpec,
    get_logger,
    load_yaml,
    pkg_root,
    resolve_skill_name,
)
from autoskillit.recipe._contracts_types import (
    _CONTEXT_REF_RE,
    _TEMPLATE_REF_RE,
    INPUT_REF_RE,
    ResultFieldSpec,
    SkillContract,
    SkillInput,
    SkillOutput,
    ToolOutputContractSpec,
    ToolOutputFieldSpec,
)

logger = get_logger(__name__)


@lru_cache(maxsize=1)
def load_bundled_manifest() -> dict[str, Any]:
    """Load the bundled skill_contracts.yaml from the package directory."""
    manifest_path = pkg_root() / "recipe" / "skill_contracts.yaml"
    return load_yaml(manifest_path)


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
            # Skill inputs default to optional — skills are permissive by design.
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


def resolve_input_specs(skill_command: str) -> tuple[InputSpec, ...]:
    """Resolve InputSpec entries for file_path/directory_path inputs from a skill's contract."""
    name = resolve_skill_name(skill_command)
    if not name:
        return ()
    contract = get_skill_contract(name, load_bundled_manifest())
    if contract is None:
        return ()
    path_position = 0
    specs: list[InputSpec] = []
    for inp in contract.inputs:
        if inp.type in ("file_path", "directory_path"):
            narrowed_type = cast(Literal["file_path", "directory_path"], inp.type)
            specs.append(
                InputSpec(
                    name=inp.name,
                    type=narrowed_type,
                    required=inp.required,
                    position=path_position,
                )
            )
            path_position += 1
    return tuple(specs)


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
            # Callable inputs default to required — callables are strict by design.
            required=inp.get("required", True),
            nullable=inp.get("nullable", True),
        )
        for inp in entry.get("inputs", [])
    ]
    outputs = [SkillOutput(name=out["name"], type=out["type"]) for out in entry.get("outputs", [])]
    return SkillContract(inputs=inputs, outputs=outputs)


def get_tool_output_contract(
    tool_name: str, manifest: dict[str, Any] | None = None
) -> ToolOutputContractSpec | None:
    """Return the ToolOutputContractSpec for a named MCP tool, or None if not declared."""
    if manifest is None:
        manifest = load_bundled_manifest()
    entry = manifest.get("tool_output_contracts", {}).get(tool_name)
    if entry is None:
        return None
    fields = {}
    for field_name, field_data in entry.get("fields", {}).items():
        if not isinstance(field_data, dict):
            raise ValueError(
                f"tool_output_contracts entry for {tool_name!r}: "
                f"field {field_name!r} must be a mapping, got {type(field_data).__name__!r}"
            )
        fields[field_name] = ToolOutputFieldSpec(
            allowed_values=tuple(field_data.get("allowed_values", [])),
            terminal_values=frozenset(field_data.get("terminal_values", [])),
            recoverable_values=frozenset(field_data.get("recoverable_values", [])),
        )
    result_field = entry.get("result_field")
    if not result_field:
        raise ValueError(
            f"tool_output_contracts entry for {tool_name!r} is missing required 'result_field'"
        )
    return ToolOutputContractSpec(result_field=result_field, fields=fields)


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
    name = resolve_skill_name(skill_command)
    if not name:
        return 0
    idx = skill_command.find(name)
    if idx < 0:
        return 0
    after_skill = skill_command[idx + len(name) :].strip()
    if not after_skill:
        return 0
    without_templates = _TEMPLATE_REF_RE.sub("", after_skill).strip()
    if not without_templates:
        return 0
    return len(without_templates.split())


def classify_step_arg_style(
    skill_command: str,
    contract_input_names: set[str],
) -> Literal["positional_text", "positional_template", "named"]:
    if count_positional_args(skill_command) > 0:
        return "positional_text"
    cmd_refs = extract_skill_cmd_refs(skill_command)
    if cmd_refs and not cmd_refs.issubset(contract_input_names):
        return "positional_template"
    return "named"
