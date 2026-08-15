"""Recipe contract manifest loading and ref extraction utils."""

from __future__ import annotations

import dataclasses
import hashlib
import json
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, Literal, assert_never, cast

from autoskillit.core import (
    VALID_INPUT_SPEC_TYPES,
    BoundScalar,
    InputSpec,
    InputSpecType,
    get_logger,
    load_yaml,
    pkg_root,
    resolve_skill_name,
)
from autoskillit.recipe._api_cache import YamlFileCache
from autoskillit.recipe._contracts_types import (
    _CONTEXT_REF_RE,
    _TEMPLATE_REF_RE,
    INPUT_REF_RE,
    AuditAuthorityPublicationSpec,
    AuditOutputContract,
    AuditOutputMode,
    OutcomeInvariantEntry,
    ResultFieldSpec,
    SkillContract,
    SkillInput,
    SkillOutput,
    SuccessQualifierEntry,
    ToolOutputContractSpec,
    ToolOutputFieldSpec,
)

logger = get_logger(__name__)

_MANIFEST_CACHE = YamlFileCache()
_SKILL_CONTRACT_IDENTITY_DOMAIN = b"autoskillit:skill-contract:v1\0"


def load_bundled_manifest() -> dict[str, Any]:
    """Load the bundled skill_contracts.yaml from the package directory."""
    manifest_path = pkg_root() / "recipe" / "skill_contracts.yaml"
    return _MANIFEST_CACHE.get_or_load(manifest_path, load_yaml)


def _parse_skill_input(skill_name: str, raw: Mapping[str, Any]) -> SkillInput:
    input_def = SkillInput(
        name=raw["name"],
        type=raw["type"],
        # Skill inputs default to optional — skills are permissive by design.
        required=raw.get("required", False),
        recommended=raw.get("recommended", False),
    )
    if "absence_value" not in raw:
        return input_def
    absence_value = raw["absence_value"]
    if type(absence_value) not in (str, int, bool):
        raise ValueError(
            f"absence_value for skill '{skill_name}' input "
            f"'{input_def.name}' must be a strict string, integer, or boolean"
        )
    if input_def.required:
        raise ValueError(
            f"required input '{input_def.name}' for skill '{skill_name}' "
            "cannot declare absence_value"
        )
    if not input_def.accepts(absence_value):
        raise ValueError(
            f"absence_value for skill '{skill_name}' input "
            f"'{input_def.name}' does not satisfy type '{input_def.type}'"
        )
    return dataclasses.replace(
        input_def,
        absence_value=cast(BoundScalar, absence_value),
    )


def get_skill_contract(skill_name: str, manifest: dict[str, Any]) -> SkillContract | None:
    """Look up a skill in the manifest and return a SkillContract."""
    skills = manifest.get("skills", {})
    skill_data = skills.get(skill_name)
    if skill_data is None:
        return None
    inputs = tuple(_parse_skill_input(skill_name, inp) for inp in skill_data.get("inputs", []))
    outputs = [
        SkillOutput(
            name=out["name"],
            type=out["type"],
            allowed_values=out.get("allowed_values", []),
        )
        for out in skill_data.get("outputs", [])
    ]
    patterns = skill_data.get("expected_output_patterns", [])
    examples = skill_data.get("pattern_examples", [])
    write_behavior = skill_data.get("write_behavior")
    write_expected_when = skill_data.get("write_expected_when", [])
    read_only = bool(skill_data.get("read_only", False))
    scope_discipline = skill_data.get("scope_discipline", False)
    if not isinstance(scope_discipline, bool):
        raise ValueError(f"scope_discipline for skill '{skill_name}' must be a boolean")
    completion_required = bool(skill_data.get("completion_required", False))
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

    output_names = {o.name for o in outputs}
    int_output_names = {o.name for o in outputs if o.type == "integer"}

    outcome_invariants: list[OutcomeInvariantEntry] = []
    for inv in skill_data.get("outcome_invariants", []):
        w, r = inv.get("when", ""), inv.get("require", "")
        if not w or not r:
            raise ValueError(
                f"outcome_invariants entry for skill '{skill_name}' missing 'when' or 'require'"
            )
        for expr_label, expr in [("when", w), ("require", r)]:
            field_name = expr.split()[0] if expr.split() else ""
            if field_name not in output_names:
                raise ValueError(
                    f"outcome_invariants.{expr_label} references undeclared output "
                    f"'{field_name}' in skill '{skill_name}'"
                )
            if field_name not in int_output_names:
                raise ValueError(
                    f"outcome_invariants.{expr_label} references non-integer output "
                    f"'{field_name}' in skill '{skill_name}'"
                )
        outcome_invariants.append(OutcomeInvariantEntry(when=w, require=r))

    success_qualifiers: list[SuccessQualifierEntry] = []
    for sq in skill_data.get("success_qualifiers", []):
        w, q = sq.get("when", ""), sq.get("qualifier", "")
        if not w or not q:
            raise ValueError(
                f"success_qualifiers entry for skill '{skill_name}' missing 'when' or 'qualifier'"
            )
        success_qualifiers.append(SuccessQualifierEntry(when=w, qualifier=q))

    audit_output_contracts: dict[AuditOutputMode, AuditOutputContract] = {}
    raw_mode_contracts = skill_data.get("audit_output_contracts", {})
    if not isinstance(raw_mode_contracts, Mapping):
        raise ValueError(f"audit_output_contracts for skill '{skill_name}' must be a mapping")
    for raw_mode, raw_contract in raw_mode_contracts.items():
        try:
            mode = AuditOutputMode(raw_mode)
        except ValueError as exc:
            raise ValueError(
                f"unsupported audit output mode {raw_mode!r} for skill '{skill_name}'"
            ) from exc
        if not isinstance(raw_contract, Mapping):
            raise ValueError(
                f"audit_output_contracts.{mode.value} for skill '{skill_name}' must be a mapping"
            )
        mode_outputs = tuple(
            SkillOutput(
                name=out["name"],
                type=out["type"],
                allowed_values=out.get("allowed_values", []),
            )
            for out in raw_contract.get("outputs", [])
        )
        if not mode_outputs:
            raise ValueError(
                f"audit_output_contracts.{mode.value} for skill '{skill_name}' "
                "must declare outputs"
            )
        audit_output_contracts[mode] = AuditOutputContract(
            outputs=mode_outputs,
            expected_output_patterns=tuple(raw_contract.get("expected_output_patterns", [])),
            pattern_examples=tuple(raw_contract.get("pattern_examples", [])),
        )

    authority_data = skill_data.get("audit_authority_publication")
    authority_publication = None
    if authority_data is not None:
        if not isinstance(authority_data, Mapping):
            raise ValueError(
                f"audit_authority_publication for skill '{skill_name}' must be a mapping"
            )
        required_modes = {AuditOutputMode.ATTESTED, AuditOutputMode.STANDALONE}
        if set(audit_output_contracts) != required_modes:
            declared = sorted(mode.value for mode in audit_output_contracts)
            required_mode_values = [
                mode.value for mode in sorted(required_modes, key=lambda item: item.value)
            ]
            raise ValueError(
                f"audit publication skill '{skill_name}' must declare exact output "
                f"modes {required_mode_values}; got {declared}"
            )
        output_field = authority_data.get("output_field", "")
        prior_input_field = authority_data.get("prior_input_field", "")
        input_names = {item.name for item in inputs}
        attested_contract = audit_output_contracts.get(AuditOutputMode.ATTESTED)
        attested_output_names = (
            {output.name for output in attested_contract.outputs}
            if attested_contract is not None
            else set()
        )
        if output_field not in output_names | attested_output_names:
            raise ValueError(
                f"audit_authority_publication references undeclared output "
                f"'{output_field}' in skill '{skill_name}'"
            )
        if prior_input_field not in input_names:
            raise ValueError(
                f"audit_authority_publication references undeclared input "
                f"'{prior_input_field}' in skill '{skill_name}'"
            )
        authority_publication = AuditAuthorityPublicationSpec(
            output_field=output_field,
            prior_input_field=prior_input_field,
        )

    return SkillContract(
        inputs=inputs,
        outputs=outputs,
        expected_output_patterns=patterns,
        pattern_examples=examples,
        write_behavior=write_behavior,
        write_expected_when=write_expected_when,
        read_only=read_only,
        scope_discipline=scope_discipline,
        completion_required=completion_required,
        result_fields=result_fields,
        outcome_invariants=outcome_invariants,
        success_qualifiers=success_qualifiers,
        input_preflight=skill_data.get("input_preflight"),
        audit_authority_publication=authority_publication,
        audit_output_contracts=audit_output_contracts,
    )


def select_audit_output_contract(
    contract: SkillContract,
    mode: AuditOutputMode,
) -> SkillContract:
    """Freeze one audit child-output contract before prompt and parser construction."""
    selected = contract.audit_output_contracts.get(mode)
    if selected is None:
        raise ValueError(f"audit output contract does not declare mode {mode.value!r}")
    return dataclasses.replace(
        contract,
        outputs=list(selected.outputs),
        expected_output_patterns=list(selected.expected_output_patterns),
        pattern_examples=list(selected.pattern_examples),
        audit_authority_publication=(
            contract.audit_authority_publication if mode is AuditOutputMode.ATTESTED else None
        ),
        audit_output_contracts={},
        audit_output_mode=mode,
    )


def compute_skill_contract_identity(
    skill_name: str,
    *,
    manifest: dict[str, Any] | None = None,
    contract_resolver: Callable[[str, dict[str, Any]], SkillContract | None] | None = None,
    manifest_loader: Callable[[], dict[str, Any]] | None = None,
) -> str:
    """Hash the canonical runtime-relevant shape of one skill contract."""
    active_manifest = (
        manifest if manifest is not None else (manifest_loader or load_bundled_manifest)()
    )
    contract = (contract_resolver or get_skill_contract)(skill_name, active_manifest)
    if contract is None:
        raise ValueError(f"skill contract is unavailable for {skill_name!r}")
    publication = contract.audit_authority_publication
    mode_contracts = {
        mode.value: {
            "expected_output_patterns": list(definition.expected_output_patterns),
            "outputs": [
                {
                    "allowed_values": list(output.allowed_values),
                    "name": output.name,
                    "type": output.type,
                }
                for output in definition.outputs
            ],
        }
        for mode, definition in sorted(
            contract.audit_output_contracts.items(),
            key=lambda item: item[0].value,
        )
    }
    payload = json.dumps(
        {
            "audit_authority_publication": (
                {
                    "output_field": publication.output_field,
                    "prior_input_field": publication.prior_input_field,
                }
                if publication is not None
                else None
            ),
            "audit_output_contracts": mode_contracts,
            "completion_required": contract.completion_required,
            "input_preflight": contract.input_preflight,
            "inputs": [
                {
                    "name": item.name,
                    "nullable": item.nullable,
                    "required": item.required,
                    "type": item.type,
                    "absence_value": item.absence_value,
                }
                for item in contract.inputs
            ],
            "skill_name": skill_name,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(_SKILL_CONTRACT_IDENTITY_DOMAIN + payload).hexdigest()


def resolve_input_specs(skill_command: str) -> tuple[InputSpec, ...]:
    """Resolve InputSpec entries for path-typed inputs from a skill's contract.

    Recognized types are those in ``VALID_INPUT_SPEC_TYPES`` — scalar
    ``file_path`` / ``directory_path`` and the ``file_path_list`` variant
    whose value is one positional token carrying comma- or newline-separated
    member paths. Non-path types (``string``, ``integer``, …) are skipped.
    """
    name = resolve_skill_name(skill_command)
    if not name:
        return ()
    contract = get_skill_contract(name, load_bundled_manifest())
    if contract is None:
        return ()
    path_position = 0
    specs: list[InputSpec] = []
    for inp in contract.inputs:
        if inp.type not in VALID_INPUT_SPEC_TYPES:
            continue
        narrowed_type = cast(InputSpecType, inp.type)
        match narrowed_type:
            case "file_path" | "directory_path" | "file_path_list":
                specs.append(
                    InputSpec(
                        name=inp.name,
                        type=narrowed_type,
                        required=inp.required,
                        position=path_position,
                    )
                )
                path_position += 1
            case _ as unreachable:
                assert_never(unreachable)
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
    inputs = tuple(
        SkillInput(
            name=inp["name"],
            type=inp["type"],
            # Callable inputs default to required — callables are strict by design.
            required=inp.get("required", True),
            nullable=inp.get("nullable", True),
        )
        for inp in entry.get("inputs", [])
    )
    outputs = [
        SkillOutput(
            name=out["name"],
            type=out["type"],
            allowed_values=out.get("allowed_values", []),
        )
        for out in entry.get("outputs", [])
    ]
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
