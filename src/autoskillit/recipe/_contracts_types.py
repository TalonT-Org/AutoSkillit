"""Backward-compat shim for ``recipe/_contracts_types.py``.

Real implementation: ``autoskillit.recipe.contracts._contracts_types`` (#4671 D).
Preserves old import path ``autoskillit.recipe._contracts_types``.
"""

from __future__ import annotations

from autoskillit.recipe.contracts._contracts_types import (
    _CONTEXT_REF_RE,
    _TEMPLATE_REF_RE,
    EXTERNAL_EFFECT_CHOICES,
    INPUT_REF_RE,
    RESULT_CAPTURE_RE,
    VALID_EXTERNAL_EFFECTS,
    AuditAuthorityPublicationSpec,
    AuditOutputContract,
    AuditOutputMode,
    BlockFingerprint,
    BoundScalar,
    DataFlowEntry,
    OutcomeInvariantEntry,
    PreflightKind,
    RecipeCard,
    ResultFieldSpec,
    SkillContract,
    SkillInput,
    SkillOutput,
    StaleItem,
    StrEnum,
    SuccessQualifierEntry,
    ToolOutputContractSpec,
    ToolOutputFieldSpec,
    annotations,
)

__all__ = [
    "AuditAuthorityPublicationSpec",
    "AuditOutputContract",
    "AuditOutputMode",
    "BlockFingerprint",
    "BoundScalar",
    "DataFlowEntry",
    "EXTERNAL_EFFECT_CHOICES",
    "INPUT_REF_RE",
    "OutcomeInvariantEntry",
    "PreflightKind",
    "RESULT_CAPTURE_RE",
    "RecipeCard",
    "ResultFieldSpec",
    "SkillContract",
    "SkillInput",
    "SkillOutput",
    "StaleItem",
    "StrEnum",
    "SuccessQualifierEntry",
    "ToolOutputContractSpec",
    "ToolOutputFieldSpec",
    "VALID_EXTERNAL_EFFECTS",
    "_CONTEXT_REF_RE",
    "_TEMPLATE_REF_RE",
    "annotations",
]
