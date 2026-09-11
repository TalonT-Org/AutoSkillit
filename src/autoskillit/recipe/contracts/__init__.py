"""IL-2 recipe skill contract primitives — types, manifest, card, staleness (#4671 Phase D).

Sub-package of :mod:`autoskillit.recipe`. Real implementations live in this
sub-package; backward-compat shims at the old ``recipe/contracts.py`` and
``recipe/_contracts_*.py`` / ``recipe/staleness_cache.py`` paths preserve
existing import sites.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ._contracts_card import (  # noqa: F401
        SKILL_TOOLS,
        Severity,
        _compute_block_fingerprint,
        atomic_write,
        bind_recipe,
        dump_yaml_str,
        get_logger,
        load_yaml,
        logger,
    )
    from ._contracts_manifest import (  # noqa: F401
        _MANIFEST_CACHE,
        _SKILL_CONTRACT_IDENTITY_DOMAIN,
        EXTERNAL_EFFECT_CHOICES,
        VALID_EXTERNAL_EFFECTS,
        VALID_INPUT_SPEC_TYPES,
        BoundScalar,
        InputSpec,
        InputSpecType,
        YamlFileCache,
        _parse_skill_input,
        assert_never,
        cast,
        compute_skill_contract_identity,
        pkg_root,
    )
    from ._contracts_staleness import (  # noqa: F401
        StalenessEntry,
        compute_recipe_hash,
        read_staleness_cache,
        write_staleness_cache,
    )
    from ._contracts_types import PreflightKind  # noqa: F401
    from .contracts import (
        _CONTEXT_REF_RE,
        _TEMPLATE_REF_RE,
        INPUT_REF_RE,
        RESULT_CAPTURE_RE,
        AuditAuthorityPublicationSpec,
        AuditOutputContract,
        AuditOutputMode,
        BlockFingerprint,
        DataFlowEntry,
        OutcomeInvariantEntry,
        RecipeCard,
        ResultFieldSpec,
        SkillContract,
        SkillInput,
        SkillOutput,
        StaleItem,
        SuccessQualifierEntry,
        ToolOutputContractSpec,
        ToolOutputFieldSpec,
        _generate_recipe_card_for_recipe,
        check_contract_staleness,
        classify_step_arg_style,
        compute_skill_hash,
        count_positional_args,
        extract_context_refs,
        extract_input_refs,
        extract_skill_cmd_refs,
        generate_recipe_card,
        get_callable_contract,
        get_skill_contract,
        get_tool_output_contract,
        load_bundled_manifest,
        load_recipe_card,
        resolve_input_specs,
        resolve_skill_name,
        select_audit_output_contract,
        stale_to_suggestions,
        validate_recipe_cards,
    )  # noqa: F401


def __getattr__(name: str):
    """Lazily resolve symbols and submodules to avoid eager subpackage loads."""
    if name in _LAZY_MODULES:
        from importlib import import_module

        full = f"{__name__}.{name}"
        mod = import_module(full)
        globals()[name] = mod
        return mod
    mod_name = _LAZY_SYMBOL_TO_MODULE.get(name)
    if mod_name is not None:
        from importlib import import_module

        mod = import_module(f"{__name__}.{mod_name}")
        value = getattr(mod, name)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


_LAZY_MODULES: frozenset[str] = frozenset(
    {
        "contracts",
        "_contracts_card",
        "_contracts_manifest",
        "_contracts_staleness",
        "_contracts_types",
        "staleness_cache",
    }
)

_LAZY_SYMBOL_TO_MODULE: dict[str, str] = {
    "AuditAuthorityPublicationSpec": "_contracts_types",
    "AuditOutputContract": "_contracts_types",
    "AuditOutputMode": "_contracts_types",
    "BlockFingerprint": "_contracts_types",
    "DataFlowEntry": "_contracts_types",
    "INPUT_REF_RE": "_contracts_types",
    "OutcomeInvariantEntry": "_contracts_types",
    "RESULT_CAPTURE_RE": "_contracts_types",
    "RecipeCard": "_contracts_types",
    "ResultFieldSpec": "_contracts_types",
    "SkillContract": "_contracts_types",
    "SkillInput": "_contracts_types",
    "SkillOutput": "_contracts_types",
    "StaleItem": "_contracts_types",
    "SuccessQualifierEntry": "_contracts_types",
    "ToolOutputContractSpec": "_contracts_types",
    "ToolOutputFieldSpec": "_contracts_types",
    "_CONTEXT_REF_RE": "_contracts_types",
    "_TEMPLATE_REF_RE": "_contracts_types",
    "_generate_recipe_card_for_recipe": "_contracts_card",
    "check_contract_staleness": "_contracts_staleness",
    "classify_step_arg_style": "_contracts_manifest",
    "compute_skill_hash": "_contracts_staleness",
    "count_positional_args": "_contracts_manifest",
    "extract_context_refs": "_contracts_manifest",
    "extract_input_refs": "_contracts_manifest",
    "extract_skill_cmd_refs": "_contracts_manifest",
    "generate_recipe_card": "_contracts_card",
    "get_callable_contract": "_contracts_manifest",
    "get_skill_contract": "_contracts_manifest",
    "get_tool_output_contract": "_contracts_manifest",
    "load_bundled_manifest": "_contracts_staleness",
    "load_recipe_card": "_contracts_card",
    "resolve_input_specs": "_contracts_manifest",
    "resolve_skill_name": "_contracts_manifest",
    "select_audit_output_contract": "_contracts_manifest",
    "stale_to_suggestions": "_contracts_staleness",
    "validate_recipe_cards": "_contracts_card",
    "SKILL_TOOLS": "_contracts_card",
    "Severity": "_contracts_card",
    "_compute_block_fingerprint": "_contracts_card",
    "atomic_write": "staleness_cache",
    "bind_recipe": "_contracts_card",
    "dump_yaml_str": "_contracts_card",
    "get_logger": "staleness_cache",
    "load_yaml": "_contracts_manifest",
    "logger": "staleness_cache",
    "BoundScalar": "_contracts_types",
    "EXTERNAL_EFFECT_CHOICES": "_contracts_types",
    "InputSpec": "_contracts_manifest",
    "InputSpecType": "_contracts_manifest",
    "VALID_EXTERNAL_EFFECTS": "_contracts_types",
    "VALID_INPUT_SPEC_TYPES": "_contracts_manifest",
    "YamlFileCache": "_contracts_manifest",
    "_MANIFEST_CACHE": "_contracts_manifest",
    "_SKILL_CONTRACT_IDENTITY_DOMAIN": "_contracts_manifest",
    "_parse_skill_input": "_contracts_manifest",
    "assert_never": "_contracts_manifest",
    "cast": "_contracts_manifest",
    "compute_skill_contract_identity": "_contracts_manifest",
    "pkg_root": "_contracts_manifest",
    "StalenessEntry": "staleness_cache",
    "compute_recipe_hash": "staleness_cache",
    "read_staleness_cache": "staleness_cache",
    "write_staleness_cache": "staleness_cache",
    "PreflightKind": "_contracts_types",
}

__all__ = [
    "AuditAuthorityPublicationSpec",
    "AuditOutputContract",
    "AuditOutputMode",
    "BlockFingerprint",
    "BoundScalar",
    "DataFlowEntry",
    "EXTERNAL_EFFECT_CHOICES",
    "INPUT_REF_RE",
    "InputSpec",
    "InputSpecType",
    "OutcomeInvariantEntry",
    "PreflightKind",
    "RESULT_CAPTURE_RE",
    "RecipeCard",
    "ResultFieldSpec",
    "SKILL_TOOLS",
    "Severity",
    "SkillContract",
    "SkillInput",
    "SkillOutput",
    "StaleItem",
    "StalenessEntry",
    "SuccessQualifierEntry",
    "ToolOutputContractSpec",
    "ToolOutputFieldSpec",
    "VALID_EXTERNAL_EFFECTS",
    "VALID_INPUT_SPEC_TYPES",
    "YamlFileCache",
    "_CONTEXT_REF_RE",
    "_MANIFEST_CACHE",
    "_SKILL_CONTRACT_IDENTITY_DOMAIN",
    "_TEMPLATE_REF_RE",
    "_compute_block_fingerprint",
    "_generate_recipe_card_for_recipe",
    "_parse_skill_input",
    "assert_never",
    "atomic_write",
    "bind_recipe",
    "cast",
    "check_contract_staleness",
    "classify_step_arg_style",
    "compute_recipe_hash",
    "compute_skill_contract_identity",
    "compute_skill_hash",
    "count_positional_args",
    "dump_yaml_str",
    "extract_context_refs",
    "extract_input_refs",
    "extract_skill_cmd_refs",
    "generate_recipe_card",
    "get_callable_contract",
    "get_logger",
    "get_skill_contract",
    "get_tool_output_contract",
    "load_bundled_manifest",
    "load_recipe_card",
    "load_yaml",
    "logger",
    "pkg_root",
    "read_staleness_cache",
    "resolve_input_specs",
    "resolve_skill_name",
    "select_audit_output_contract",
    "stale_to_suggestions",
    "validate_recipe_cards",
    "write_staleness_cache",
]
