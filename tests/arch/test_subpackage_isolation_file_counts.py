from __future__ import annotations

from pathlib import Path

import pytest

from tests.arch._helpers import SRC_ROOT

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

# Shim files at core/ and recipe/ top level are 2-line forwarding re-exports
# that preserve old import paths after moving real implementations into
# sub-packages. The arch test excludes these from the file count because they
# contribute no real module surface; only the underlying real modules are counted.
_SHIM_FILENAMES: frozenset[str] = frozenset(
    {
        # Phase A: core/install/, core/claude_env/, core/io/ sub-packages
        "_install_detect.py",
        "_cmd_runner.py",
        "_claude_env.py",
        "claude_conventions.py",
        "feature_flags.py",
        # NOTE: ``core/io.py`` is absent. A module cannot sit beside a
        # same-named package — Python resolves ``autoskillit.core.io`` to
        # ``core/io/`` unconditionally, so the shim was unreachable dead code
        # and was deleted. ``core/io/io.py`` is the real module and is counted.
        "paths.py",
        "path_containment.py",
        "_json.py",
        "_terminal_table.py",
        "_version_snapshot.py",
        "_delivery_bounds.py",
        # Phase B: core/git/ sub-package
        "git_remote.py",
        "github_url.py",
        "bash_write_targets.py",
        "branch_guard.py",
        # Phase B: core/audit/ sub-package
        "audit_cycle_verifier.py",
        "audit_semantic_codec.py",
        "closure_hashing.py",
        "closure_verifier.py",
        # Phase B: core/plugins/ sub-package
        "_plugin_cache.py",
        "_plugin_artifact_identity.py",
        "_plugin_ids.py",
        "agent_definition.py",
        # The three plugin-cache lifecycle shards #4741 split out of the
        # monolithic _plugin_cache.py, co-located with the facade that already
        # treats them as siblings and re-exports their symbols.
        "_active_kitchens.py",
        "_plugin_artifact_retirement.py",
        "_retiring_cache.py",
        # Phase B: core/pipeline/ sub-package
        "pipeline_tracker.py",
        "tool_sequence_analysis.py",
        "_execution_marker.py",
        "_step_context.py",
        # Phase C: core/context_admission/ sub-package
        # NOTE: ``context_admission.py`` was moved into the
        # ``core/context_admission/`` sub-package itself, which re-exports
        # every symbol through its ``__init__.py``. The import
        # ``from autoskillit.core.context_admission import X`` resolves
        # through the sub-package, so no top-level shim is needed.
        "context_admission_helpers.py",
        "context_admission_accept_release.py",
        "context_admission_expiry_rollover.py",
        "context_admission_generation.py",
        "context_admission_indeterminate.py",
        "context_admission_prepare_stage_dispatch.py",
        "context_admission_propose_reserve.py",
    }
)
_RECIPE_SHIM_FILENAMES: frozenset[str] = frozenset(
    {
        # Phase D: recipe/analysis/, recipe/helpers/, recipe/ingredients/,
        # recipe/cmd_rpc/, recipe/contracts/, recipe/methodology/ sub-packages.
        # Each entry below is a 2-line forwarding shim preserving the pre-Phase-D
        # import path (``from autoskillit.recipe.<old_name> import X``).
        "_analysis.py",
        "_analysis_bfs.py",
        "_analysis_blocks.py",
        "_analysis_detectors.py",
        "_analysis_graph.py",
        "_git_helpers.py",
        "_io_loading.py",
        "_rule_helpers.py",
        "_skill_helpers.py",
        "_skill_placeholder_parser.py",
        "_registry_utils.py",
        "_recipe_composition.py",
        "_recipe_ingredients.py",
        "_recipe_raw_repair.py",
        "_cmd_rpc.py",
        "_cmd_rpc_guards.py",
        "_cmd_rpc_issues.py",
        "_cmd_rpc_merge.py",
        # NOTE: ``recipe/contracts.py`` is absent for the same reason as
        # ``core/io.py`` above — it sat beside ``recipe/contracts/`` and so was
        # unreachable. ``recipe/contracts/contracts.py`` is the real module.
        "_contracts_card.py",
        "_contracts_manifest.py",
        "_contracts_staleness.py",
        "_contracts_types.py",
        "staleness_cache.py",
        "methodology_disambiguation.py",
        "methodology_tradition_registry.py",
        "methodology_tradition_router.py",
        "methodology_venue_appendix.py",
        "experiment_type_registry.py",
        # Phase E: recipe/api/ and recipe/api_orchestration/ sub-packages.
        # These root-level files preserve the pre-extraction import paths.
        "_api.py",
        "_api_cache.py",
        "_api_listing.py",
        "_api_orchestration.py",
        "_api_orchestration_assemble.py",
        "_api_orchestration_cache.py",
        "_api_orchestration_match.py",
        "_api_orchestration_parse.py",
        "_api_orchestration_text.py",
        "_api_orchestration_types.py",
        "_api_orchestration_validate.py",
    }
)

FILE_COUNT_LIMITS: dict[str, int] = {
    "core": 10,  # 10 files + __init__ + buffer (was 21 before 11 files moved to sub-packages)
    "core/install": 4,  # 2 files + __init__ + buffer
    "core/claude_env": 4,  # 3 files + __init__ + buffer
    "core/io": 9,  # 8 files + __init__ + buffer (yaml_io.py split from io.py for 750-line cap)
    "core/git": 5,  # 4 files + __init__ + buffer
    "core/audit": 5,  # 4 files + __init__ + buffer
    "core/plugins": 10,  # 7 files + __init__ + buffer
    "core/pipeline": 5,  # 4 files + __init__ + buffer
    "core/context_admission": 9,  # 8 files + __init__
    # _type_truth replaces the retired _type_tradition_manifest shard.
    "core/types": 76,
    "core/runtime": 11,
    "config": 20,
    "recipe": 12,  # 12 real files after excluding registered forwarding shims
    "recipe/analysis": 6,  # 5 moved files + __init__
    "recipe/helpers": 7,  # 6 moved files + __init__
    "recipe/ingredients": 5,  # 3 moved files + 1 file extracted to fit 750-line cap + __init__
    "recipe/cmd_rpc": 5,  # 4 moved files + __init__
    "recipe/contracts": 7,  # 6 moved files + __init__
    "recipe/methodology": 6,  # 5 moved files + __init__
    "recipe/rules": 66,
    "server": 20,
    "execution": 23,
    "cli": 9,
    "cli/session": 11,
    "cli/doctor": 13,
    "pipeline": 19,
    "fleet": 20,
    "server/tools": 39,
    "execution/process": 11,
    "execution/backends": 30,
    "execution/github_review": 15,
    "execution/headless": 15,
    "execution/session": 20,
    "workspace": 1,  # was 6; #5018 moved 5 skill-capability modules into skill_capabilities/
    "hooks": 27,  # +1 _capture_spawn.py extracted from _capture_process.py (#4732)
    "hooks/guards": 41,
    "smoke_utils": 11,
}


def test_workspace_skills_package_has_only_the_eight_moved_modules() -> None:
    skills_dir = SRC_ROOT / "workspace" / "skills"
    assert {path.name for path in skills_dir.glob("*.py")} == {
        "__init__.py",
        "_records.py",
        "_overrides.py",
        "_exploration.py",
        "_visibility.py",
        "_frontmatter.py",
        "_format.py",
        "_resources.py",
    }
    assert not any(
        (SRC_ROOT / "workspace" / name).exists()
        for name in (
            "skills.py",
            "skills_records.py",
            "skills_overrides.py",
            "skills_exploration.py",
            "skills_visibility.py",
            "skills_frontmatter.py",
            "skill_format.py",
            "skill_resources.py",
        )
    )


def test_workspace_skill_capabilities_package_has_only_the_five_moved_modules() -> None:
    capabilities_dir = SRC_ROOT / "workspace" / "skill_capabilities"
    assert {path.name for path in capabilities_dir.glob("*.py")} == {
        "__init__.py",
        "_authenticity.py",
        "_cache.py",
        "_scanner.py",
        "_semantic_plan.py",
    }
    assert not any(
        (SRC_ROOT / "workspace" / name).exists()
        for name in (
            "skill_capabilities.py",
            "skill_capability_authenticity.py",
            "skill_capability_cache.py",
            "skill_capability_scanner.py",
            "skill_semantic_plan.py",
        )
    )


def test_server_file_count_under_limit() -> None:
    """server/ must not exceed 20 Python files (REQ-DSGN-002).

    Twenty is a root package a single reviewer can still hold in mind.
    Responsibilities that would push the count past it belong in a
    grouping subpackage instead — see `server/recipe/`, `server/lifecycle/`,
    and `server/response/` (issue #4673). None of the three carries a
    dedicated `FILE_COUNT_LIMITS` entry; they are governed by the default
    12-file default in the subpackage count test below, plus the
    parameterized limits in `tests/arch/test_server_fleet_folder_layout.py`.
    """
    limit = FILE_COUNT_LIMITS["server"]
    py_files = list((SRC_ROOT / "server").glob("*.py"))
    assert len(py_files) <= limit, f"server/ has {len(py_files)} files, max is {limit}"


def _subpackage_file_count_violations() -> list[str]:
    """REQ-CNST-003: Direct Python files have a 12-file default or explicit override.

    The root-package exemptions retain distinct responsibilities whose modules
    remain easier to review separately. Per-package ceilings live in
    ``FILE_COUNT_LIMITS``; each direct ``__init__.py`` is included.

    E1 server/: tool composition, lifecycle, and response entry points.
    E2 recipe/: recipe loading, execution, and validation entry points.
    E3 execution/: process, session, and backend coordination.
    E4 core/: shared import-layer-zero primitives and public facades.
    E5 cli/: user command entry points and presentation.
    E6 hooks/: standalone hook scripts and their support modules.
    E7 pipeline/: admission, audit, and workflow coordination.
    E8 fleet/: dispatch, campaign state, and session coordination.
    """
    violations: list[str] = []
    dirs_to_check: list[Path] = []
    for sub_dir in sorted(SRC_ROOT.iterdir()):
        if not sub_dir.is_dir() or sub_dir.name.startswith("_") or sub_dir.name == "__pycache__":
            continue
        dirs_to_check.append(sub_dir)
        for nested_dir in sorted(sub_dir.iterdir()):
            if (
                not nested_dir.is_dir()
                or nested_dir.name.startswith("_")
                or nested_dir.name == "__pycache__"
            ):
                continue
            dirs_to_check.append(nested_dir)
    for sub_dir in dirs_to_check:
        rel_key = str(sub_dir.relative_to(SRC_ROOT))
        # Recipe forwarding shims are root-only. Canonical nested modules may
        # share their basenames and must count toward the default ceiling.
        if rel_key == "recipe":
            shim_set = _RECIPE_SHIM_FILENAMES
        elif rel_key == "core":
            shim_set = _SHIM_FILENAMES
        else:
            shim_set = frozenset()
        py_files = [p for p in sub_dir.glob("*.py") if p.name not in shim_set]
        limit = FILE_COUNT_LIMITS.get(rel_key, 12)
        if len(py_files) > limit:
            violations.append(f"{rel_key}/: {len(py_files)} Python files (max {limit})")
    return violations


def test_no_subpackage_exceeds_12_files_default_and_per_package_overrides() -> None:
    """REQ-CNST-003: Enforce the 12-file default and per-package ceilings."""
    violations = _subpackage_file_count_violations()
    assert not violations, (
        "Subpackages exceeding their Python file limits (default 12):\n"
        + "\n".join(f"  {v}" for v in violations)
    )


@pytest.mark.parametrize("total_files", [12, 13])
def test_default_file_count_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, total_files: int
) -> None:
    package = tmp_path / "fixture"
    package.mkdir()
    (package / "__init__.py").touch()
    for index in range(total_files - 1):
        (package / f"module_{index}.py").touch()
    monkeypatch.setattr("tests.arch.test_subpackage_isolation_file_counts.SRC_ROOT", tmp_path)

    expected = [] if total_files == 12 else ["fixture/: 13 Python files (max 12)"]
    assert _subpackage_file_count_violations() == expected


def test_nested_core_module_matching_root_shim_counts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package = tmp_path / "core" / "fixture"
    package.mkdir(parents=True)
    (package.parent / "__init__.py").touch()
    (package / "__init__.py").touch()
    (package / "paths.py").touch()
    for index in range(11):
        (package / f"module_{index}.py").touch()
    monkeypatch.setattr("tests.arch.test_subpackage_isolation_file_counts.SRC_ROOT", tmp_path)

    assert _subpackage_file_count_violations() == ["core/fixture/: 13 Python files (max 12)"]


# ── session_skills package shape ─────────────────────────────────────────────
# The session-skill cluster (facade + five shards + the projection gateway
# shard) lives entirely under workspace/session_skills/, a default
# (non-underscored) nested package. This proves no flat file at workspace/
# top level carries any of the pre-package names.

_SESSION_SKILLS_RETIRED_FLAT_NAMES = frozenset(
    {
        "session_skills.py",
        "session_skill_catalog.py",
        "session_skill_lifecycle.py",
        "session_skill_manager.py",
        "session_skill_materialization.py",
        "session_skill_provider.py",
        "skill_projection.py",
    }
)


def test_session_skills_package_shape() -> None:
    """workspace/session_skills/ contains exactly the expected files.

    No flat file at ``workspace/`` top level may carry any of the retired
    names, and the workspace/ ceiling reflects the files moved out of it. The
    expected file set is derived from ``_SESSION_SKILL_SHARD_OWNERS`` (the
    canonical shard registry in
    tests/workspace/test_session_skills_projected_artifact_shard_ownership.py)
    rather than duplicated here as a second literal.
    """
    from tests.workspace.test_session_skills_projected_artifact_shard_ownership import (
        _SESSION_SKILL_SHARD_OWNERS,
    )

    package_dir = SRC_ROOT / "workspace" / "session_skills"
    assert package_dir.is_dir(), f"{package_dir} must exist as a package directory"

    expected_files = {"__init__.py", "_projection.py"} | {
        f"{stem}.py" for stem, _ in _SESSION_SKILL_SHARD_OWNERS
    }
    actual_files = {p.name for p in package_dir.glob("*.py")}
    assert actual_files == expected_files, (
        f"workspace/session_skills/ contains {sorted(actual_files)}, "
        f"expected exactly {sorted(expected_files)}"
    )

    workspace_dir = SRC_ROOT / "workspace"
    flat_survivors = {
        name for name in _SESSION_SKILLS_RETIRED_FLAT_NAMES if (workspace_dir / name).is_file()
    }
    assert not flat_survivors, (
        f"retired flat session-skill files still present at workspace/ top level: "
        f"{sorted(flat_survivors)}"
    )

    workspace_py_files = {p.name for p in workspace_dir.glob("*.py")}
    assert FILE_COUNT_LIMITS["workspace"] == len(workspace_py_files), (
        f"workspace/ file-count ceiling ({FILE_COUNT_LIMITS['workspace']}) must match "
        f"its actual top-level file count ({len(workspace_py_files)}); update "
        f"FILE_COUNT_LIMITS['workspace'] (with a rationale comment) if this fails "
        f"after a legitimate file addition or removal"
    )


# ── Phase B Test 8: closure_hashing behavior snapshot ────────────────────────
# Captured on 2026-09-10 against the post-Phase-B state of
# ``core/audit/closure_hashing.py``. If the hash value changes after a
# future move or refactor of closure_hashing.py, the test fails — meaning
# behavior has drifted and a snapshot re-capture is required.


def test_phase_b_closure_hashing_behavior_snapshot() -> None:
    """Phase B Test 8: closure_hashing.compute_bytes_hash output is byte-stable.

    Captured value: ``sha256:7e3849047077040f30bbab03278adefccd7beba842425cb3b35dee4b9299baa9``
    against the input ``b"phase_b_capture_v1_known_input"``.

    If a future move (Phase B → core/audit/, or any further refactor) changes
    the closure-hashing algorithm, this test fails. The expected behavior is
    SHA-256 of the input bytes, prefixed with ``"sha256:"``.
    """
    from autoskillit.core.audit.closure_hashing import compute_bytes_hash

    assert (
        compute_bytes_hash(b"phase_b_capture_v1_known_input")
        == "sha256:7e3849047077040f30bbab03278adefccd7beba842425cb3b35dee4b9299baa9"
    )


# ── Phase D Test 4: semantic-rule registry populated after sub-package moves ─
# Every @semantic_rule decorator must register in the canonical _RULE_REGISTRY.
# The registry is populated purely as an import side effect of recipe/__init__.py
# pulling in each rule module, so a dropped import silently removes rules rather
# than raising. The floor below is therefore a regression detector, and it is set
# just under the measured count (242 on 2026-09-10) rather than at the Phase D
# plan's original 80: a floor of 80 would let two thirds of the registry vanish
# while still reporting green.
_RULE_REGISTRY_FLOOR = 240


def test_phase_d_semantic_rule_registry_populated() -> None:
    """Phase D Test 4: _RULE_REGISTRY keeps its full rule population.

    The registry is populated as a side effect of recipe/__init__.py importing
    every rule module for its @semantic_rule decorator. If a future commit
    removes an import from recipe/__init__.py, that rule module's decorator
    would not fire and this count would drop.

    When rules are intentionally added or retired, update _RULE_REGISTRY_FLOOR
    to sit just under the new measured count.
    """
    from autoskillit.recipe.registry import _RULE_REGISTRY

    assert len(_RULE_REGISTRY) >= _RULE_REGISTRY_FLOOR, (
        f"_RULE_REGISTRY has {len(_RULE_REGISTRY)} entries; expected "
        f"≥{_RULE_REGISTRY_FLOOR}. A rule module may have lost its import in "
        f"recipe/__init__.py, so its @semantic_rule decorators never fired."
    )
