"""Shard-ownership guard for the session-skill and projected-artifact decomposition.

Every public symbol on the ``autoskillit.workspace.session_skills`` and
``autoskillit.workspace._projected_artifact.materialization`` facades must be
owned by exactly one shard module, and the facade must re-export each symbol
identity-equal to the shard's authoritative definition. This enforces the
single-source-of-truth invariant so future contributors cannot silently
duplicate a name across two shards.

The decomposition splits the two flat modules into:

- ``autoskillit.workspace.session_skills._catalog``
- ``autoskillit.workspace.session_skills._provider``
- ``autoskillit.workspace.session_skills._lifecycle``
- ``autoskillit.workspace.session_skills._materialization``
- ``autoskillit.workspace.session_skills._manager``
- ``autoskillit.workspace._projected_artifact._documents``
- ``autoskillit.workspace._projected_artifact._publication``
- ``autoskillit.workspace._projected_artifact._validation``

The session-skill facade (``session_skills/__init__.py``) and the
projected-artifact facade (``_projected_artifact/materialization.py``) become
identity-preserving compatibility surfaces over those shards.

``autoskillit.workspace.session_skills._projection`` is a distinct *gateway*
shard rather than a session-skill shard proper: it owns a small local surface
(``SkillProjectionPreparation``, ``build_skill_projection_binding``,
``finalize_skill_projection_binding``, ``prepare_catalog_skill_projection``,
``prepare_skill_projection``) and re-exports the rest of its ``__all__``,
identity-equal, from ``_projected_artifact`` and ``autoskillit.core``. It is
tracked by dedicated gateway tests below rather than by the per-shard
ownership tables, since the generic ``__all__ <= owned_names`` invariant
does not hold for a module that legitimately re-exports names it does not
own.

Import convention for shards that need to reach a symbol defined in another
shard (so ``monkeypatch.setattr`` on the producer's facade takes effect):

1. **Canonical** — import the producer's facade at module scope under a
   ``_X_facade`` alias (e.g. ``import autoskillit.workspace.session_skills._projection
   as _skill_projection_facade`` in ``_provider.py``). The alias
   preserves identity-equal re-export with the facade's ``__all__`` so patches
   propagate without rebinding.

2. **Exception — function-local deferred import** — only when (1) is
   structurally impossible (cycle that cannot be broken without rearranging
   call sites). Use ``# noqa: PLC0415`` with an inline rationale.

3. **Intra-package sibling narrowing** — ``_provider`` and ``_materialization``
   may import the sibling ``_projection`` gateway shard directly; no other
   session-skill shard may. Shards may NOT import from their own facade
   (``workspace.session_skills`` for session shards,
   ``_projected_artifact.materialization`` for projection shards).

Pick (1) by default; reach for (2) only when (1) is structurally impossible.
Never mix the two in the same shard without an inline justification.

Symbols whose authoritative definition lives in the facade module itself
(the ``compute_skill_closure`` re-export from ``skills``) are tracked
separately as facade-retained ownership rows and are exempt from the
per-shard declaration/re-export tests.
"""

from __future__ import annotations

from importlib import import_module
from pathlib import Path

import pytest

from tests.arch._helpers import SRC_ROOT

pytestmark = [pytest.mark.layer("workspace"), pytest.mark.small]


# Symbols whose authoritative definition lives in the facade module itself
# rather than in any sharded submodule. These rows are only consulted by
# ``test_shard_ownership_is_well_formed``; the per-shard subset/passthrough
# tests below iterate over _SESSION_SKILL_SHARD_OWNERS +
# _PROJECTED_ARTIFACT_SHARD_OWNERS exclusively, never over these facade-retained rows.
_FACADE_RETAINED_OWNERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "session_skills",
        ("compute_skill_closure",),
    ),
)

_SESSION_SKILL_SHARD_OWNERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "_catalog",
        (
            "CompiledSessionSkillCatalog",
            "SkillUnavailableMetadata",
            "_SKILL_UNAVAILABILITY_SCHEMA_VERSION",
            "_canonical_skill_unavailability_payload",
            "_compile_reachable_profile_skill_catalog",
            "_merge_skill_unavailability_payloads",
            "_profile_skill_catalog",
            "_profile_skill_infos",
            "_required_native_child_roles",
            "_session_agent_definitions",
            "compile_session_skill_catalog",
            "write_skill_unavailability_metadata",
        ),
    ),
    (
        "_lifecycle",
        (
            "_SESSION_LEASES_SUBDIR",
            "_SessionLease",
            "_raise_failures",
            "_remove_and_verify",
            "resolve_persistent_session_root",
            "resolve_persistent_session_roots",
        ),
    ),
    (
        "_manager",
        (
            "DefaultSessionSkillManager",
            "_InitializedSession",
            "_materialize_bound_records",
        ),
    ),
    (
        "_materialization",
        (
            "_ExplorerBindingEnv",
            "_ExplorerBindingEnvFactory",
            "_SessionSetupKwargs",
            "_alias_legacy_discovery_root",
            "_create_inert_rollout_paths",
            "_merge_profile_projection",
            "_materialize_profile_skill_infos",
            "_materialize_session",
            "_restore_session",
            "materialize_profile_skills",
        ),
    ),
    (
        "_provider",
        (
            "SkillsDirectoryProvider",
            "_CANDIDATE_ROOTS",
            "_parse_write_paths",
            "default_skill_resolver",
            "resolve_closure_write_dirs",
            "resolve_ephemeral_root",
        ),
    ),
)

_PROJECTED_ARTIFACT_SHARD_OWNERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "_documents",
        (
            "AgentSkillDocument",
            "SkillContractRecord",
            "SkillProjectionContext",
            "_SKILL_NAMESPACE_REF_RE",
            "_active_exploration_vectors",
            "_agent_skill_namespace",
            "_default_base_branch",
            "_direct_install_projection_context",
            "_exploration_router_plan",
            "_source_identity",
            "project_agent_skill_document",
        ),
    ),
    (
        "_publication",
        (
            "SANITIZED_PLUGIN_MANIFEST_SCHEMA_VERSION",
            "_copy_non_skill_plugin_assets",
            "_manifest_skill_entry",
            "_projection_skills_manifest",
            "_replace_directory",
            "_render_agent_definitions",
            "_skill_sequence",
            "materialize_agent_skill_tree",
            "materialize_sanitized_plugin_root",
            "write_generated_hooks_json",
        ),
    ),
    (
        "_validation",
        ("validate_sanitized_plugin_artifact",),
    ),
)

# _projection.py is a gateway shard: these five names are the only ones it
# defines locally. The rest of its __all__ is re-exported, identity-equal,
# from `_projected_artifact` (see _PROJECTION_PROJECTED_ARTIFACT_REEXPORTS
# below) or from `autoskillit.core` dataclasses (see
# _PROJECTION_CORE_REEXPORTS below); the local set is what this table tracks.
_PROJECTION_LOCAL_NAMES: frozenset[str] = frozenset(
    {
        "SkillProjectionPreparation",
        "build_skill_projection_binding",
        "finalize_skill_projection_binding",
        "prepare_catalog_skill_projection",
        "prepare_skill_projection",
    }
)

# Names _projection.py re-exports, identity-equal, from `_projected_artifact`.
_PROJECTION_PROJECTED_ARTIFACT_REEXPORTS: frozenset[str] = frozenset(
    {
        "AgentSkillDocument",
        "ProjectedPluginArtifactAuthority",
        "ProjectedPluginRetirementOwner",
        "SkillProjectionContext",
        "materialize_agent_skill_tree",
        "materialize_sanitized_plugin_root",
        "project_agent_skill_document",
        "project_default_plugin_authority",
        "project_direct_install_authority",
        "validate_sanitized_plugin_artifact",
        "write_generated_hooks_json",
    }
)

# Names _projection.py re-exports, identity-equal, from `autoskillit.core`.
_PROJECTION_CORE_REEXPORTS: frozenset[str] = frozenset(
    {
        "SkillProjectionBinding",
        "SkillProjectionRefusal",
    }
)

# The fourteen names autoskillit.workspace (the outer package facade) imports
# from workspace.session_skills._projection.
_WORKSPACE_PROJECTION_REEXPORTS: frozenset[str] = frozenset(
    {
        "AgentSkillDocument",
        "SkillProjectionBinding",
        "SkillProjectionContext",
        "SkillProjectionPreparation",
        "SkillProjectionRefusal",
        "build_skill_projection_binding",
        "finalize_skill_projection_binding",
        "materialize_agent_skill_tree",
        "materialize_sanitized_plugin_root",
        "prepare_catalog_skill_projection",
        "prepare_skill_projection",
        "project_agent_skill_document",
        "validate_sanitized_plugin_artifact",
        "write_generated_hooks_json",
    }
)


def _facade_public_surface() -> tuple[str, ...]:
    session_skills = import_module("autoskillit.workspace.session_skills")
    materialization = import_module("autoskillit.workspace._projected_artifact.materialization")
    names = set(getattr(session_skills, "__all__", ())) | set(
        getattr(materialization, "__all__", ())
    )
    return tuple(sorted(names))


def _shard_stems() -> tuple[set[str], set[str]]:
    workspace_init = import_module("autoskillit.workspace")
    pkg_init_file = workspace_init.__file__
    assert pkg_init_file is not None
    pkg_root = Path(pkg_init_file).parent
    session_skills_pkg = pkg_root / "session_skills"
    session_skill_stems = {
        p.stem
        for p in session_skills_pkg.glob("_*.py")
        if p.stem not in {"__init__", "_projection"}
    }

    projected_pkg_root = pkg_root / "_projected_artifact"
    projection_stems = {
        p.stem
        for p in projected_pkg_root.glob("*.py")
        if p.stem in {"_documents", "_publication", "_validation"}
    }
    return session_skill_stems, projection_stems


def _import_family_shard(family: str, shard_stem: str):
    if family == "session_skills":
        return import_module(f"autoskillit.workspace.session_skills.{shard_stem}")
    return import_module(f"autoskillit.workspace._projected_artifact.{shard_stem}")


_ALL_SHARD_OWNERS: tuple[tuple[str, str, tuple[str, ...]], ...] = tuple(
    ("session_skills", stem, names) for stem, names in _SESSION_SKILL_SHARD_OWNERS
) + tuple(("projected_artifact", stem, names) for stem, names in _PROJECTED_ARTIFACT_SHARD_OWNERS)


def test_every_shard_module_is_in_ownership_table() -> None:
    session_skill_stems, projection_stems = _shard_stems()
    table_session_skills = {stem for stem, _ in _SESSION_SKILL_SHARD_OWNERS}
    table_projections = {stem for stem, _ in _PROJECTED_ARTIFACT_SHARD_OWNERS}
    assert session_skill_stems == table_session_skills, (
        f"session-skill shard stems out of sync: disk has "
        f"{sorted(session_skill_stems - table_session_skills)}, "
        f"table has {sorted(table_session_skills - session_skill_stems)}"
    )
    assert projection_stems == table_projections, (
        f"projected-artifact shard stems out of sync: disk has "
        f"{sorted(projection_stems - table_projections)}, "
        f"table has {sorted(table_projections - projection_stems)}"
    )


def test_session_skills_package_contains_exact_owned_shard_set() -> None:
    """The private session_skills/ package must contain exactly the owned shards.

    Complements _shard_stems() sync above by pinning the literal file set,
    including the gateway shard _projection.py, which _shard_stems()
    deliberately excludes from the generic ownership-table sync check.
    """
    workspace_init = import_module("autoskillit.workspace")
    pkg_init_file = workspace_init.__file__
    assert pkg_init_file is not None
    session_skills_pkg = Path(pkg_init_file).parent / "session_skills"
    expected = (
        {"__init__.py"}
        | {f"{stem}.py" for stem, _ in _SESSION_SKILL_SHARD_OWNERS}
        | {"_projection.py"}
    )
    actual = {p.name for p in session_skills_pkg.glob("*.py")}
    assert actual == expected, (
        f"workspace/session_skills/ contains {sorted(actual)}, expected {sorted(expected)}"
    )


def test_shard_ownership_is_well_formed() -> None:
    all_owned = [
        name
        for _, names in _SESSION_SKILL_SHARD_OWNERS
        + _PROJECTED_ARTIFACT_SHARD_OWNERS
        + _FACADE_RETAINED_OWNERS
        for name in names
    ]
    assert len(all_owned) == len(set(all_owned)), (
        "every owned name must appear in exactly one shard"
    )
    facade_surface = set(_facade_public_surface())
    owned_set = set(all_owned)
    missing = facade_surface - owned_set
    assert not missing, (
        f"every facade-public symbol must be owned by exactly one shard; "
        f"missing from ownership: {sorted(missing)}"
    )


@pytest.mark.parametrize(
    ("family", "shard_stem", "owned_names"),
    _ALL_SHARD_OWNERS,
    ids=[f"{family}:{stem}" for family, stem, _ in _ALL_SHARD_OWNERS],
)
def test_each_shard_declares_only_owned_names(
    family: str,
    shard_stem: str,
    owned_names: tuple[str, ...],
) -> None:
    """Each shard's __all__ (or named exports) must be a subset of its owned names."""
    module = _import_family_shard(family, shard_stem)
    declared = set(getattr(module, "__all__", ()))
    assert declared <= set(owned_names), (
        f"shard {shard_stem} declares names outside its ownership: {declared - set(owned_names)}"
    )


@pytest.mark.parametrize("name", sorted(set(_facade_public_surface())))
def test_every_owned_name_is_reexported_by_a_facade(name: str) -> None:
    """Every name in the public surface must be importable from a facade."""
    session_skills = import_module("autoskillit.workspace.session_skills")
    materialization = import_module("autoskillit.workspace._projected_artifact.materialization")
    in_session_skills = hasattr(session_skills, name)
    in_materialization = hasattr(materialization, name)
    assert in_session_skills ^ in_materialization, (
        f"{name!r} must be re-exported by exactly one facade "
        f"(session_skills={in_session_skills}, materialization={in_materialization})"
    )


@pytest.mark.parametrize(
    ("family", "shard_stem", "owned_names"),
    _ALL_SHARD_OWNERS,
    ids=[f"{family}:{stem}" for family, stem, _ in _ALL_SHARD_OWNERS],
)
def test_facade_reexport_is_passthrough(
    family: str,
    shard_stem: str,
    owned_names: tuple[str, ...],
) -> None:
    """Every owned symbol in the facade surface must be re-exported identity-equal.

    Symbols that exist on the shard but are NOT in either facade's ``__all__``
    are private to the shard (e.g. ``_SessionLease``) and are not subject to
    facade re-export — they are reached directly via the shard module.
    """
    shard = _import_family_shard(family, shard_stem)
    session_skills = import_module("autoskillit.workspace.session_skills")
    materialization = import_module("autoskillit.workspace._projected_artifact.materialization")
    facade_surface = set(getattr(session_skills, "__all__", ())) | set(
        getattr(materialization, "__all__", ())
    )
    for name in owned_names:
        if name not in facade_surface:
            continue
        in_session_skills = hasattr(session_skills, name)
        in_materialization = hasattr(materialization, name)
        assert in_session_skills ^ in_materialization, (
            f"{name!r} (from {shard_stem}) must be re-exported by exactly one facade"
        )
        facade = session_skills if in_session_skills else materialization
        assert getattr(facade, name) is getattr(shard, name), (
            f"facade re-export of {name!r} is not identity-equal to shard symbol"
        )


@pytest.mark.parametrize(
    ("symbol", "facade_module", "canonical_module"),
    [
        (
            "write_skill_unavailability_metadata",
            "autoskillit.workspace.session_skills",
            "autoskillit.workspace.session_skills._catalog",
        ),
        (
            "write_generated_hooks_json",
            "autoskillit.workspace._projected_artifact.materialization",
            "autoskillit.workspace._projected_artifact._publication",
        ),
    ],
)
def test_durable_writer_lookup_strings_remain_on_facades(
    symbol: str,
    facade_module: str,
    canonical_module: str,
) -> None:
    """Registered durable-writer lookups must stay resolvable and identity-equal.

    The registry addresses each writer as ``{facade_module}:{symbol}``, so the
    facade attribute must resolve to the canonical shard definition itself —
    not a copy — and must be defined by the canonical shard.
    """
    facade_symbol = getattr(import_module(facade_module), symbol)
    canonical_symbol = getattr(import_module(canonical_module), symbol)

    assert facade_symbol is canonical_symbol, (
        f"registered writer {facade_module}:{symbol} must remain identity-equal "
        f"to {canonical_module}.{symbol}"
    )
    assert facade_symbol.__module__ == canonical_module, (
        f"registered writer {symbol!r} must be defined by {canonical_module}, "
        f"but is defined by {facade_symbol.__module__}"
    )


def test_compute_skill_closure_remains_external_reexport() -> None:
    """compute_skill_closure is sourced from workspace.skills and re-exported by session_skills."""
    import autoskillit.workspace.session_skills as session_skills
    import autoskillit.workspace.skills as skills

    assert session_skills.compute_skill_closure is skills.compute_skill_closure, (
        "session_skills.compute_skill_closure must remain an identity-preserving "
        "re-export of workspace.skills.compute_skill_closure"
    )


def test_parse_write_paths_remains_provider_owned_direct_module_alias() -> None:
    """_parse_write_paths stays directly available from session_skills and provider shard."""
    session_skills = import_module("autoskillit.workspace.session_skills")
    session_skill_provider = import_module("autoskillit.workspace.session_skills._provider")

    assert session_skills._parse_write_paths is session_skill_provider._parse_write_paths, (
        "session_skills._parse_write_paths must be identity-equal to provider shard's "
        "definition; the facade must not introduce a wrapper"
    )


def test_session_skill_catalog_owns_write_versioned_json_writer() -> None:
    """Catalog shard owns the durable-writer call site for skill-unavailability.json."""
    session_skills = import_module("autoskillit.workspace.session_skills")
    catalog = import_module("autoskillit.workspace.session_skills._catalog")
    write_skill_unavailability_metadata = catalog.write_skill_unavailability_metadata
    facade_writer = session_skills.write_skill_unavailability_metadata

    src = Path(write_skill_unavailability_metadata.__code__.co_filename).resolve()
    expected_src = SRC_ROOT / "workspace" / "session_skills" / "_catalog.py"
    assert src == expected_src.resolve(), (
        f"write_skill_unavailability_metadata must be defined in "
        f"session_skills/_catalog.py; actual source: {src}"
    )
    assert facade_writer is write_skill_unavailability_metadata


def test_session_skill_lifecycle_owns_lease_delegation() -> None:
    """Lifecycle shard owns lease acquisition and delegates flock to ArtifactLease.

    The original ``session_skills.py`` did not call ``fcntl.flock`` directly;
    it routed through ``ArtifactLease.acquire_exclusive``. The lifecycle shard
    preserves that delegation by owning ``_SessionLease`` and reaching flock
    only through ``ArtifactLease``.

    Direct-``fcntl.flock`` governance is NOT asserted here — that is owned by
    ``tests/fleet/test_state_lock_contract.py``, whose allowlist deliberately
    admits ``workspace/session_skills/_lifecycle.py``. This test pins
    ownership and re-export identity only.
    """
    lifecycle = import_module("autoskillit.workspace.session_skills._lifecycle")

    assert hasattr(lifecycle, "ArtifactLease"), (
        "lifecycle shard must import ArtifactLease (the canonical fcntl.flock owner)"
    )
    assert hasattr(lifecycle, "_SessionLease"), (
        "lifecycle shard must own _SessionLease, the workspace-owned external lease"
    )
    facade = import_module("autoskillit.workspace.session_skills")
    if hasattr(facade, "_SessionLease"):
        assert facade._SessionLease is lifecycle._SessionLease, (
            "a facade re-export of _SessionLease must be identity-equal to the "
            "lifecycle shard's definition"
        )


def test_projection_locally_defined_names_are_uniquely_owned() -> None:
    """_projection's locally defined names must be defined in _projection itself.

    Only these five names are ownership-tracked for the gateway shard; its
    remaining __all__ entries are pass-through reexports checked separately
    (see test_projection_reexports_remain_identity_equal_to_projected_artifact)
    and are intentionally NOT required to appear in this local-ownership row —
    a gateway shard's __all__ is not a subset of what it owns.
    """
    projection = import_module("autoskillit.workspace.session_skills._projection")
    declared = set(getattr(projection, "__all__", ()))
    assert _PROJECTION_LOCAL_NAMES <= declared, (
        f"_projection must declare its locally-owned names in __all__; "
        f"missing: {sorted(_PROJECTION_LOCAL_NAMES - declared)}"
    )
    for name in sorted(_PROJECTION_LOCAL_NAMES):
        value = getattr(projection, name)
        module_name = getattr(value, "__module__", None)
        assert module_name == "autoskillit.workspace.session_skills._projection", (
            f"{name!r} must be defined in _projection itself, not imported; "
            f"actual __module__: {module_name}"
        )


def test_projection_reexports_remain_identity_equal_to_projected_artifact() -> None:
    """_projection's _projected_artifact reexports stay identity-equal to their source.

    _projection is a gateway: it re-exports these names from the cross-subsystem
    ``_projected_artifact`` package rather than owning them. A gateway must
    never introduce a copy — patches to the projected-artifact original must
    still be visible through _projection.
    """
    projection = import_module("autoskillit.workspace.session_skills._projection")
    projected_artifact = import_module("autoskillit.workspace._projected_artifact")
    for name in sorted(_PROJECTION_PROJECTED_ARTIFACT_REEXPORTS):
        assert hasattr(projected_artifact, name), (
            f"{name!r} must remain part of _projected_artifact's public surface"
        )
        assert getattr(projection, name) is getattr(projected_artifact, name), (
            f"_projection.{name} must be identity-equal to "
            f"_projected_artifact.{name} (gateway reexport, not a copy)"
        )


def test_projection_core_reexports_remain_identity_equal_to_core() -> None:
    """_projection's autoskillit.core reexports stay identity-equal to their source.

    _projection re-exports these dataclasses from the cross-subsystem
    ``autoskillit.core`` package rather than owning them. A gateway must
    never introduce a copy — patches to the core original must still be
    visible through _projection.
    """
    projection = import_module("autoskillit.workspace.session_skills._projection")
    core = import_module("autoskillit.core")
    for name in sorted(_PROJECTION_CORE_REEXPORTS):
        assert hasattr(core, name), (
            f"{name!r} must remain part of autoskillit.core's public surface"
        )
        assert getattr(projection, name) is getattr(core, name), (
            f"_projection.{name} must be identity-equal to "
            f"autoskillit.core.{name} (gateway reexport, not a copy)"
        )


def test_workspace_projection_reexports_remain_identity_equal_to_projection_module() -> None:
    """autoskillit.workspace's projection-related exports stay identity-equal to _projection.

    workspace/__init__.py imports these fourteen names from
    workspace.session_skills._projection — the reexport must not introduce a
    wrapper or copy anywhere along that chain.
    """
    workspace = import_module("autoskillit.workspace")
    projection = import_module("autoskillit.workspace.session_skills._projection")
    for name in sorted(_WORKSPACE_PROJECTION_REEXPORTS):
        assert getattr(workspace, name) is getattr(projection, name), (
            f"autoskillit.workspace.{name} must be identity-equal to "
            f"autoskillit.workspace.session_skills._projection.{name}"
        )


def test_publication_owns_sanitized_plugin_manifest_schema_constant() -> None:
    """SANITIZED_PLUGIN_MANIFEST_SCHEMA_VERSION lives in publication; validation imports it."""
    publication_path = SRC_ROOT / "workspace" / "_projected_artifact" / "_publication.py"
    validation_path = SRC_ROOT / "workspace" / "_projected_artifact" / "_validation.py"
    publication_text = publication_path.read_text()
    validation_text = validation_path.read_text()
    assert "SANITIZED_PLUGIN_MANIFEST_SCHEMA_VERSION" in publication_text, (
        "publication shard must own the manifest schema constant"
    )
    # Validation imports the schema constant from publication rather than copying it.
    assert (
        "SANITIZED_PLUGIN_MANIFEST_SCHEMA_VERSION" in validation_text
        and "_publication" in validation_text
        and "from autoskillit.workspace._projected_artifact._publication" in validation_text
    ), (
        "validation shard must import SANITIZED_PLUGIN_MANIFEST_SCHEMA_VERSION "
        "from _publication rather than declaring a local copy"
    )


def test_render_agent_definitions_owner_is_publication_and_identity_shared_with_authority() -> (
    None
):
    """_render_agent_definitions is defined in publication and used identity-equal by authority."""
    from autoskillit.workspace._projected_artifact import _publication as publication
    from autoskillit.workspace._projected_artifact import authority

    assert hasattr(publication, "_render_agent_definitions"), (
        "publication shard must own _render_agent_definitions"
    )
    assert authority._render_agent_definitions is publication._render_agent_definitions, (
        "_render_agent_definitions must remain a single object identity shared "
        "by authority and the publication shard"
    )
    # Verify the function is defined in _publication.py by checking the source file.
    publication_path = SRC_ROOT / "workspace" / "_projected_artifact" / "_publication.py"
    publication_text = publication_path.read_text()
    assert "def _render_agent_definitions" in publication_text, (
        "_render_agent_definitions must be defined inside _publication.py"
    )
