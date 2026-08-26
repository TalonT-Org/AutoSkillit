"""Shard-ownership guard for the workspace/skills decomposition (#4833).

Every public symbol defined in either of the two facades
(``autoskillit.workspace.skills`` and ``autoskillit.workspace.skill_capabilities``)
must be owned by exactly one shard module, and the facade must re-export it
identity-equal. This enforces the single-source-of-truth invariant so future
contributors cannot silently duplicate a name across two shards.
"""

from __future__ import annotations

from importlib import import_module

import pytest

pytestmark = [pytest.mark.layer("workspace"), pytest.mark.small]


_SKILLS_SHARD_OWNERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "skills_records",
        (
            "EffectiveSkillCatalog",
            "EffectiveSkillInvocation",
            "SkillCatalogEntry",
            "SkillExclusion",
            "SkillInfo",
            "SkillInvalidity",
            "compute_skill_closure",
            "invalidity_hints",
            "logger",
            "render_skill_invalidities",
        ),
    ),
    (
        "skills_overrides",
        (
            "ProjectLocalOverride",
            "_OVERRIDE_SEARCH_DIRS",
            "_project_skill_path",
            "detect_project_local_overrides",
            "override_names",
        ),
    ),
    (
        "skills_exploration",
        (
            "replace_exploration_vector_bodies",
            "_bind_exploration_vector_markers",
            "_load_exploration_sidecar",
            "_parse_exploration_sidecar",
        ),
    ),
    (
        "skills_visibility",
        (
            "_effective_disabled_categories",
            "_skill_is_visible",
            "_visibility_policy",
        ),
    ),
    (
        "skills_frontmatter",
        ("_skill_info_from_frontmatter",),
    ),
)

_SKILL_CAPABILITY_SHARD_OWNERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "skill_capability_cache",
        (
            "_SKILL_CAPABILITY_EVIDENCE_CACHE",
            "_SKILL_CAPABILITY_EVIDENCE_CACHE_MAX_ENTRIES",
            "_SKILL_CAPABILITY_EVIDENCE_CACHE_MAX_BYTES",
            "_SKILL_CAPABILITY_EVIDENCE_CACHE_MAX_INPUT_BYTES",
            "_SKILL_CAPABILITY_EVIDENCE_RECORD_WEIGHT_BYTES",
            "_SkillCapabilityEvidenceBuildState",
            "_SkillCapabilityEvidenceCache",
            "_SkillCapabilityEvidenceCacheEntry",
            "_SkillCapabilityEvidenceCacheInfo",
            "_retained_string_weight_bytes",
            "_skill_capability_evidence_entry_weight_bytes",
            "_skill_capability_evidence_input_weight_bytes",
        ),
    ),
    (
        "skill_capability_scanner",
        (
            "CapabilityActor",
            "CapabilityDirection",
            "CapabilitySourceClassification",
            "SkillCapabilityEvidence",
            "_scan_skill_capability_evidence_uncached",
        ),
    ),
    (
        "skill_capability_authenticity",
        (
            "SkillCapabilityAuthenticityDiagnostic",
            "SkillCapabilityValidation",
            "detect_skill_capabilities",
            "validate_skill_capability_authenticity",
            "validate_skill_capability_declarations",
        ),
    ),
    (
        "skill_semantic_plan",
        ("RETIRED_SEMANTIC_CAPABILITIES", "parse_skill_semantic_plan"),
    ),
)


def _facade_public_surface() -> tuple[str, ...]:
    skills = import_module("autoskillit.workspace.skills")
    capabilities = import_module("autoskillit.workspace.skill_capabilities")
    names = set(getattr(skills, "__all__", ())) | set(getattr(capabilities, "__all__", ()))
    return tuple(sorted(names))


def test_shard_ownership_is_well_formed() -> None:
    all_owned = [
        name
        for _, names in _SKILLS_SHARD_OWNERS + _SKILL_CAPABILITY_SHARD_OWNERS
        for name in names
    ]
    assert all(all_owned), "every name tuple must be non-empty"
    assert len(all_owned) == len(set(all_owned)), (
        "every owned name must appear in exactly one shard"
    )
    assert set(all_owned) == set(_facade_public_surface()), (
        "shard ownership must cover every facade-public symbol"
    )


@pytest.mark.parametrize(
    ("shard_stem", "owned_names"),
    _SKILLS_SHARD_OWNERS + _SKILL_CAPABILITY_SHARD_OWNERS,
    ids=[stem for stem, _ in _SKILLS_SHARD_OWNERS + _SKILL_CAPABILITY_SHARD_OWNERS],
)
def test_each_shard_declares_only_owned_names(
    shard_stem: str,
    owned_names: tuple[str, ...],
) -> None:
    """Each shard's __all__ (or named exports) must be a subset of its owned names."""
    # The shard may or may not have an __all__; we verify it is a subset of owned.
    module = import_module(f"autoskillit.workspace.{shard_stem}")
    declared = set(getattr(module, "__all__", ()))
    assert declared <= set(owned_names), (
        f"shard {shard_stem} declares names outside its ownership: {declared - set(owned_names)}"
    )


@pytest.mark.parametrize("name", sorted(set(_facade_public_surface())))
def test_every_owned_name_is_reexported_by_a_facade(name: str) -> None:
    """Every name in the public surface must be importable from a facade."""
    skills = import_module("autoskillit.workspace.skills")
    capabilities = import_module("autoskillit.workspace.skill_capabilities")
    in_skills = hasattr(skills, name)
    in_capabilities = hasattr(capabilities, name)
    assert in_skills ^ in_capabilities, (
        f"{name!r} must be re-exported by exactly one facade "
        f"(skills={in_skills}, capabilities={in_capabilities})"
    )


@pytest.mark.parametrize(
    ("shard_stem", "owned_names"),
    _SKILLS_SHARD_OWNERS + _SKILL_CAPABILITY_SHARD_OWNERS,
    ids=[stem for stem, _ in _SKILLS_SHARD_OWNERS + _SKILL_CAPABILITY_SHARD_OWNERS],
)
def test_facade_reexport_is_passthrough(
    shard_stem: str,
    owned_names: tuple[str, ...],
) -> None:
    """The facade must re-export each shard symbol with identity equality."""
    shard = import_module(f"autoskillit.workspace.{shard_stem}")
    skills = import_module("autoskillit.workspace.skills")
    capabilities = import_module("autoskillit.workspace.skill_capabilities")
    for name in owned_names:
        in_skills = hasattr(skills, name)
        in_capabilities = hasattr(capabilities, name)
        assert in_skills ^ in_capabilities, (
            f"{name!r} (from {shard_stem}) must be re-exported by exactly one facade"
        )
        facade = skills if in_skills else capabilities
        assert getattr(facade, name) is getattr(shard, name), (
            f"facade re-export of {name!r} is not identity-equal to shard symbol"
        )
