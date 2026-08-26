"""Shard-ownership guard for the workspace/skills decomposition (#4833).

Every public symbol defined in either of the two facades
(``autoskillit.workspace.skills`` and ``autoskillit.workspace.skill_capabilities``)
must be owned by exactly one shard module, and the facade must re-export it
identity-equal. This enforces the single-source-of-truth invariant so future
contributors cannot silently duplicate a name across two shards.

Symbols defined *inside* one of the facade modules themselves (``skills``,
``skill_capabilities``) are tracked separately as facade-retained ownership
rows; they are not sharded and the per-shard declaration/re-export tests are
scoped to the real shards only.

Import convention for shards that need to reach a symbol defined in another
shard (so ``monkeypatch.setattr`` on the producer's facade takes effect):

1. **Canonical** — import the producer's facade at module scope under a
   ``_X_facade`` alias (e.g. ``import autoskillit.workspace.skill_capabilities
   as _capabilities_facade`` in ``skill_capability_authenticity.py`` L13;
   ``import autoskillit.workspace.skills as _skills_facade`` in
   ``skills_frontmatter.py`` L20). The alias preserves identity-equal
   re-export with the facade's ``__all__`` so patches propagate without
   rebinding.

2. **Exception — function-local deferred import** (only
   ``skills_frontmatter.py`` L91, L189-191 with ``# noqa: PLC0415``). Use
   when the call site is inside a hot loop and the facade symbol cannot be
   imported at module scope without a cycle. The inline ``# noqa`` rationale
   is mandatory. Reach for this only when (1) is structurally impossible.

3. **Facade-retained symbols** whose authoritative definition lives inside
   the facade module itself (``classify_skill_capability_evidence`` in the
   capabilities facade; ``DefaultSkillResolver``, the bundled-skill path
   helpers, the cache singletons in the skills facade) are listed in
   ``_FACADE_RETAINED_OWNERS`` and are exempt from the shard passthrough
   tests.

Pick (1) by default; reach for (2) only when (1) is structurally impossible.
Never mix the two in the same shard without an inline justification.
"""

from __future__ import annotations

from importlib import import_module

import pytest

pytestmark = [pytest.mark.layer("workspace"), pytest.mark.small]


# Symbols whose authoritative definition lives in the facade module itself
# rather than in any sharded submodule. These rows are only consulted by
# ``test_shard_ownership_is_well_formed``; the per-shard subset/passthrough
# tests below iterate over _SKILLS_SHARD_OWNERS + _SKILL_CAPABILITY_SHARD_OWNERS
# exclusively, never over these facade-retained rows.
_FACADE_RETAINED_OWNERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "skills",
        (
            "DefaultSkillResolver",
            "_INTERNAL_SKILLS",
            "_LIST_ALL_CACHE",
            "_LIST_ALL_CACHE_KEY",
            "_scan_directory",
            "bundled_skills_dir",
            "bundled_skills_extended_dir",
            "validate_skill_tier_roles",
        ),
    ),
    (
        "skill_capabilities",
        ("classify_skill_capability_evidence",),
    ),
)

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
        (),
    ),
    (
        "skills_frontmatter",
        (),
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
        for _, names in _SKILLS_SHARD_OWNERS
        + _SKILL_CAPABILITY_SHARD_OWNERS
        + _FACADE_RETAINED_OWNERS
        for name in names
    ]
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
