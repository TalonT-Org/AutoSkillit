"""Skill-contract remediation registry.

Issue #4735: this shard was extracted from ``_type_constants.py``. The
forcing-function guards (uniqueness of kinds, bijection with
``SkillInvalidityKind``) run at module load time and must remain intact —
they are what make adding a new ``SkillInvalidityKind`` member a CI-blocking
event unless a remediation is registered.

``SKILL_SEMANTIC_SCHEMA_VERSION`` is imported because two f-string hints in
``_SKILL_CONTRACT_REMEDIATION_DEFS`` reference it; omitting the import raises
``NameError`` at module load time.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import NamedTuple

from ._type_enums import RemediationAction, SkillInvalidityKind
from ._type_skill_semantics import SKILL_SEMANTIC_SCHEMA_VERSION

__all__ = ["SkillContractRemediationDef", "SKILL_CONTRACT_REMEDIATIONS"]


class SkillContractRemediationDef(NamedTuple):
    """One SkillInvalidityKind's forcing-function remediation declaration.

    Modeled on ``RetiredArtifactShape``: a new validation cannot ship without
    registering how pre-existing artifacts that now fail it are handled.
    ``DETERMINISTIC`` kinds must be handled by ``SkillMigrationAdapter``;
    ``ADVISORY`` kinds only ever surface ``hint`` to an operator.
    """

    kind: SkillInvalidityKind
    introduced_in: str
    action: RemediationAction
    hint: str


# Append-only, exactly like RETIRED_INSTALL_ARTIFACT_SHAPES: every member of
# SkillInvalidityKind must have an entry here, enforced by a guard test in
# tests/contracts/. The resolver (workspace/skills.py) renders `hint` into
# SkillExclusion records, composition-root warnings, and doctor findings;
# migration/engine.py's SkillMigrationAdapter renders every DETERMINISTIC
# entry into an actual frontmatter rewrite.
_SKILL_CONTRACT_REMEDIATION_DEFS = (
    SkillContractRemediationDef(
        kind=SkillInvalidityKind.FRONTMATTER_PARSE,
        introduced_in="0.10.929",
        action=RemediationAction.ADVISORY,
        hint="fix the YAML frontmatter parse error named in the detail message",
    ),
    SkillContractRemediationDef(
        kind=SkillInvalidityKind.FIELD_SHAPE,
        introduced_in="0.10.929",
        action=RemediationAction.ADVISORY,
        hint=("change the offending frontmatter field to a YAML list, e.g. 'categories: [tag]'"),
    ),
    SkillContractRemediationDef(
        kind=SkillInvalidityKind.EXPLORATION_CONTRACT_INVALID,
        introduced_in="0.10.931",
        action=RemediationAction.ADVISORY,
        hint=(
            "move exploration vectors to a valid exploration.yaml sidecar and ensure "
            "its declarations match the SKILL.md exploration-vector markers"
        ),
    ),
    SkillContractRemediationDef(
        kind=SkillInvalidityKind.RESERVED_FIELD,
        introduced_in="0.10.929",
        action=RemediationAction.ADVISORY,
        hint=(
            "remove 'canonical_content'/'canonical_digest' from frontmatter — "
            "these are source-derived and must not be supplied"
        ),
    ),
    SkillContractRemediationDef(
        kind=SkillInvalidityKind.UNKNOWN_CAPABILITY,
        introduced_in="0.10.929",
        action=RemediationAction.ADVISORY,
        hint=(
            "remove the unrecognized capability name from 'uses_capabilities:', or "
            "move the skill to an execution role permitted to declare it"
        ),
    ),
    SkillContractRemediationDef(
        kind=SkillInvalidityKind.UNDECLARED_CAPABILITY,
        introduced_in="0.10.929",
        action=RemediationAction.DETERMINISTIC,
        hint=("add the missing capability name(s) to 'uses_capabilities:' in the frontmatter"),
    ),
    SkillContractRemediationDef(
        kind=SkillInvalidityKind.SEMANTIC_UNDECLARED_TOKENS,
        introduced_in="0.10.929",
        action=RemediationAction.DETERMINISTIC,
        hint=(
            "add a 'semantic_version'/'semantic_requirements' declaration covering "
            "the detected portable-execution tokens"
        ),
    ),
    SkillContractRemediationDef(
        kind=SkillInvalidityKind.SEMANTIC_MISSING_VERSION,
        introduced_in="0.10.929",
        action=RemediationAction.DETERMINISTIC,
        hint=(
            f"add 'semantic_version: {SKILL_SEMANTIC_SCHEMA_VERSION}' alongside the "
            "existing semantic_requirements"
        ),
    ),
    SkillContractRemediationDef(
        kind=SkillInvalidityKind.SEMANTIC_VERSION_MISMATCH,
        introduced_in="0.10.929",
        action=RemediationAction.ADVISORY,
        hint=(
            "update semantic_requirements to the current schema and bump "
            f"semantic_version to {SKILL_SEMANTIC_SCHEMA_VERSION}"
        ),
    ),
    SkillContractRemediationDef(
        kind=SkillInvalidityKind.SEMANTIC_CHILD_CARDINALITY_INVALID,
        introduced_in="0.10.964",
        action=RemediationAction.DETERMINISTIC,
        hint=(
            "give each semantic_requirements.child_spawns entry exactly one authority: "
            "count: <positive integer> or for_each: <runtime collection>"
        ),
    ),
    SkillContractRemediationDef(
        kind=SkillInvalidityKind.SEMANTIC_PLAN_INVALID,
        introduced_in="0.10.929",
        action=RemediationAction.ADVISORY,
        hint="fix the malformed semantic_requirements mapping named in the detail message",
    ),
)
SKILL_CONTRACT_REMEDIATIONS: Mapping[SkillInvalidityKind, SkillContractRemediationDef] = (
    MappingProxyType(
        {definition.kind: definition for definition in _SKILL_CONTRACT_REMEDIATION_DEFS}
    )
)

if len(SKILL_CONTRACT_REMEDIATIONS) != len(_SKILL_CONTRACT_REMEDIATION_DEFS):
    raise AssertionError("Skill contract remediation definitions must have unique kinds")

_UNREGISTERED_INVALIDITY_KINDS = sorted(
    set(SkillInvalidityKind) - set(SKILL_CONTRACT_REMEDIATIONS)
)
if _UNREGISTERED_INVALIDITY_KINDS:
    raise AssertionError(
        "Every SkillInvalidityKind must have a SKILL_CONTRACT_REMEDIATIONS entry. "
        f"Missing: {_UNREGISTERED_INVALIDITY_KINDS}"
    )
