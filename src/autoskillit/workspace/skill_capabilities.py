"""Skill capability evidence classification facade.

Stable import surface for the capability-evidence classifier. Composes the
process-local weighted LRU cache, the regex scanner, the
declaration-vs-evidence authenticity check, and the semantic-plan parser
behind one entry point; readers should import from this module rather than
from the individual shards.

The 13 underscore-prefixed names re-exported from
``skill_capability_cache`` below (the cache singletons and weight helpers)
are **not** part of the public surface — they appear in this module only
so existing tests can monkeypatch.setattr them via the facade. The
public surface is the ``__all__`` list; everything else is internal
re-export plumbing. Test-only — production code reaches the cache shard
through ``classify_skill_capability_evidence``, which binds the
facade-global cache singleton at call time.
"""

from __future__ import annotations

from autoskillit.workspace.skill_capability_authenticity import (
    SkillCapabilityAuthenticityDiagnostic,
    SkillCapabilityValidation,
    detect_skill_capabilities,
    validate_skill_capability_authenticity,
    validate_skill_capability_declarations,
)
from autoskillit.workspace.skill_capability_cache import (  # noqa: F401  # re-exported so tests can monkeypatch.setattr on the facade
    _SKILL_CAPABILITY_EVIDENCE_CACHE,
    _SKILL_CAPABILITY_EVIDENCE_CACHE_MAX_BYTES,
    _SKILL_CAPABILITY_EVIDENCE_CACHE_MAX_ENTRIES,
    _SKILL_CAPABILITY_EVIDENCE_CACHE_MAX_INPUT_BYTES,
    _SKILL_CAPABILITY_EVIDENCE_RECORD_WEIGHT_BYTES,
    _retained_string_weight_bytes,
    _skill_capability_evidence_entry_weight_bytes,
    _skill_capability_evidence_input_weight_bytes,
    _SkillCapabilityEvidenceBuildState,
    _SkillCapabilityEvidenceCache,
    _SkillCapabilityEvidenceCacheEntry,
    _SkillCapabilityEvidenceCacheInfo,
)
from autoskillit.workspace.skill_capability_scanner import (
    CapabilityActor,
    CapabilityDirection,
    CapabilitySourceClassification,
    SkillCapabilityEvidence,
    _normalize_skill_capability_name,
    _scan_skill_capability_evidence_uncached,
)
from autoskillit.workspace.skill_semantic_plan import (
    RETIRED_SEMANTIC_CAPABILITIES,
    parse_skill_semantic_plan,
)


def classify_skill_capability_evidence(
    content: str,
    skill_name: str | None = None,
) -> tuple[SkillCapabilityEvidence, ...]:
    """Classify all recognizable capability occurrences in ``content``.

    Documentary occurrences are retained as ``artifact`` evidence so callers
    can explain why a declaration was rejected without treating it as genuine.
    """
    evidence_cache = _SKILL_CAPABILITY_EVIDENCE_CACHE
    scanner = _scan_skill_capability_evidence_uncached
    normalize = _normalize_skill_capability_name

    effective_skill_name = normalize(content, skill_name)

    if len(content) + len(effective_skill_name) > evidence_cache.max_input_bytes:
        return scanner(content, effective_skill_name)

    input_weight_bytes = _skill_capability_evidence_input_weight_bytes(
        content,
        effective_skill_name,
    )
    if input_weight_bytes > evidence_cache.max_input_bytes:
        return scanner(content, effective_skill_name)

    hash(content)
    hash(effective_skill_name)
    key = (content, effective_skill_name)
    resident, state, is_builder = evidence_cache._lookup_or_register(key)
    if resident is not None:
        return resident
    if state is None:
        raise RuntimeError("Capability evidence cache returned no build state")
    if not is_builder:
        return evidence_cache._wait_for_build(key, state)

    try:
        result = scanner(content, effective_skill_name)
        completed_weight_bytes = _skill_capability_evidence_entry_weight_bytes(
            input_weight_bytes,
            result,
        )
    except BaseException as error:
        evidence_cache._publish_failure(key, state, error)
        raise
    return evidence_cache._complete_build(
        key,
        state,
        result,
        completed_weight_bytes,
    )


__all__ = [
    "CapabilityActor",
    "CapabilityDirection",
    "CapabilitySourceClassification",
    "RETIRED_SEMANTIC_CAPABILITIES",
    "SkillCapabilityAuthenticityDiagnostic",
    "SkillCapabilityEvidence",
    "SkillCapabilityValidation",
    "_scan_skill_capability_evidence_uncached",
    "classify_skill_capability_evidence",
    "detect_skill_capabilities",
    "parse_skill_semantic_plan",
    "validate_skill_capability_authenticity",
    "validate_skill_capability_declarations",
]
