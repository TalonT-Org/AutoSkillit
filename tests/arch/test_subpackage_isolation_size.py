from __future__ import annotations

import pytest

from tests.arch._helpers import SRC_ROOT
from tests.arch._subpackage_isolation_line_limits import _LINE_LIMIT_EXEMPTIONS

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]


def test_pipeline_shard_size_ceiling() -> None:
    """REQ-CNST-010-Wavefront1: each shard in _context_admission_ledger is ≤750 lines."""
    subpackage_root = SRC_ROOT / "pipeline" / "_context_admission_ledger"
    assert subpackage_root.is_dir(), (
        f"expected private subpackage at {subpackage_root}; Wavefront 1 of #4667"
    )
    offenders: list[str] = []
    for py_file in sorted(subpackage_root.rglob("*.py")):
        line_count = len(py_file.read_text(encoding="utf-8").splitlines())
        if line_count > 750:
            offenders.append(f"{py_file.relative_to(SRC_ROOT)}: {line_count} lines (max 750)")
    assert not offenders, "Pipeline shards exceed the 750-line ceiling:\n  " + "\n  ".join(
        offenders
    )


def test_no_src_module_exceeds_line_limit() -> None:
    """REQ-CNST-010: No source module may exceed 1000 lines (exemptions require rule IDs).

    Exceptions are documented in _LINE_LIMIT_EXEMPTIONS with rationale.
    session.py (adjudication pipeline, ~864 lines) is intentionally near this
    limit; do NOT split below 1000 lines — see REQ-CNST-010-NOTE-1.
    """
    from tests.arch._helpers import _collect_line_limit_violations

    violations = _collect_line_limit_violations(_LINE_LIMIT_EXEMPTIONS)
    assert not violations, (
        "Source modules exceeding line limit "
        "(add entry to _LINE_LIMIT_EXEMPTIONS with rule ID + rationale):\n"
        + "\n".join(f"  {v}" for v in violations)
    )


def test_pipeline_exploration_context_is_a_package() -> None:
    """REQ-CNST-010-E22: ``pipeline/exploration_context`` is a sub-package (#4835)."""
    assert not (SRC_ROOT / "pipeline" / "exploration_context.py").exists(), (
        "Old monolithic pipeline/exploration_context.py must be removed (#4835)"
    )
    package_dir = SRC_ROOT / "pipeline" / "exploration_context"
    assert package_dir.is_dir(), "pipeline/exploration_context/ must be a package directory"
    expected_shards = [
        "__init__.py",
        "_constants.py",
        "_types.py",
        "_eligibility.py",
        "_failure_codes.py",
        "_launch_adapter.py",
        "_store.py",
    ]
    for shard in expected_shards:
        assert (package_dir / shard).is_file(), (
            f"Missing expected shard pipeline/exploration_context/{shard}"
        )


def test_exploration_context_facade_re_exports_contract() -> None:
    """REQ-CNST-010-E22: public facade re-exports every pre-decomposition name (#4835).

    Asserts that ``import autoskillit.pipeline.exploration_context as m``
    resolves every name the old ``exploration_context.py``'s ``__all__``
    advertised.  This is the behavioural-equivalence contract for
    external callers; if any name is missing, the facade has regressed.
    """
    import autoskillit.pipeline.exploration_context as m

    expected = [
        "CapabilityResolution",
        "CapabilityResolutionStatus",
        "EXPLORATION_STORE_FAILURE_CODES",
        "EXPLORER_ROLE_NAMES",
        "EXPLORER_INELIGIBLE_SESSION_TYPES",
        "EXPLORATION_AUTHORITY_PATH_ENV",
        "EXPLORATION_CAPABILITY_ENV",
        "EXPLORATION_PRINCIPAL_ROLE",
        "EXPLORATION_ROLE_ENV",
        "EXPLORATION_SESSION_ENV",
        "ExplorationLaunchBinding",
        "ExplorationContext",
        "ExplorationContextStoreProtocol",
        "ExplorationServiceProtocol",
        "OwnerBoundExplorationContextStore",
        "exploration_auto_provision_eligible",
        "is_explorer_binding_eligible",
        "resolve_exploration_store_failure_code",
    ]
    missing = [name for name in expected if name not in m.__all__]
    assert not missing, f"Public facade is missing names from pre-#4835 __all__: {missing}"


def test_pipeline_exploration_context_e22_retired() -> None:
    """REQ-CNST-010-E22 (pipeline/exploration_context.py) is retired per #4835.

    A separate hooks/_capture_artifacts.py exemption shares the same rule ID
    (a pre-existing latent registry violation tracked elsewhere).  This
    test scopes to the pipeline retirement.
    """
    exemptions = _LINE_LIMIT_EXEMPTIONS
    assert "pipeline/exploration_context.py" not in exemptions, (
        "E22 retirement for pipeline/exploration_context.py was not applied"
    )
    # Durable module's docstring no longer references E22
    durable_src = (SRC_ROOT / "pipeline" / "exploration_context_durable.py").read_text()
    assert "REQ-CNST-010-E22" not in durable_src, (
        "durable module's docstring still references the retired E22 ID"
    )


def test_pipeline_exploration_context_shards_under_900_lines() -> None:
    """REQ-CNST-010-E22: every shard in the package is at most 900 lines (#4835).

    The pre-decomposition monolithic file was 1061 lines.  After
    decomposition, every shard under ``pipeline/exploration_context/``
    must be ≤ 900 lines (the wavefront-1 ceiling).  The class body
    alone is ~720 lines; the remaining ~90 lines is module docstring,
    imports, and the package's ``__init__.py`` facade re-export
    surface, which the test also pins so the re-export facade itself
    cannot regress.
    """
    package_dir = SRC_ROOT / "pipeline" / "exploration_context"
    violations: list[tuple[str, int]] = []
    for shard in sorted(package_dir.glob("*.py")):
        line_count = len(shard.read_text().splitlines())
        if line_count > 900:
            violations.append((str(shard.relative_to(SRC_ROOT)), line_count))
    assert not violations, (
        "Exploration-context shards exceeding the 900-line wavefront-1 ceiling:\n"
        + "\n".join(f"  {rel}: {count} lines" for rel, count in violations)
    )


def test_pipeline_exploration_context_store_under_750_lines() -> None:
    """Keep the exploration-context Store shard within its permanent ceiling."""
    store_path = SRC_ROOT / "pipeline" / "exploration_context" / "_store.py"
    assert store_path.is_file(), "Missing pipeline/exploration_context/_store.py"
    line_count = len(store_path.read_text().splitlines())
    assert line_count <= 750, (
        f"pipeline/exploration_context/_store.py exceeds the 750-line ceiling: {line_count} lines"
    )


def test_session_skills_e13_e14_exemption_is_retired() -> None:
    """REQ-CNST-010-E13/E14 (workspace/session_skills.py) is retired without replacement.

    After the shard decomposition lands, ``workspace/session_skills.py`` is a
    thin identity-preserving facade and must be absent from
    ``_LINE_LIMIT_EXEMPTIONS``. No replacement exemption is added and no
    ``RETIRED_*`` or ``SKILL_CONTRACT_REMEDIATIONS`` entry is registered —
    ordinary Python module decomposition is outside those retirement surfaces.
    """
    exemptions = _LINE_LIMIT_EXEMPTIONS
    assert "workspace/session_skills.py" not in exemptions, (
        "E13/E14 retirement for workspace/session_skills.py was not applied; "
        "the decomposition replaces this module with a facade under the 1000-line limit"
    )


def test_stale_workspace_skill_line_limit_exemptions_are_retired() -> None:
    """Retired workspace skill ceilings stay absent from the exemption registry."""
    retired_exemptions = {
        "skills.py",
        "workspace/skill_capabilities.py",
        "workspace/skills.py",
    }
    stale_exemptions = retired_exemptions.intersection(_LINE_LIMIT_EXEMPTIONS)
    assert not stale_exemptions, (
        "Retired workspace skill line-limit exemptions remain registered: "
        + ", ".join(sorted(stale_exemptions))
    )


def test_hook_registry_e21_exemption_is_retired() -> None:
    """REQ-CNST-010-E21 was retired by #4853 when hook_registry.py became a package.

    PR #4898 reintroduced the entry while decomposing the exemption registry out of
    test_subpackage_isolation.py. No ``hook_registry.py`` source file exists; every
    shard of the ``hook_registry/`` package is under the 1000-line default ceiling,
    so no exemption — under any key form — is warranted.

    The basename check mirrors the lookup in ``_collect_line_limit_violations``
    (``exemptions.get(rel, exemptions.get(py_file.name, ...))``): a path-keyed entry
    ending in ``hook_registry.py`` would be an equivalent reintroduction.
    """
    assert "hook_registry.py" not in _LINE_LIMIT_EXEMPTIONS, (
        "REQ-CNST-010-E21 was retired by issue #4853; the hook_registry.py exemption "
        "must not be reintroduced (no such source file exists)"
    )
    basename_offenders = sorted(
        key for key in _LINE_LIMIT_EXEMPTIONS if key.rsplit("/", 1)[-1] == "hook_registry.py"
    )
    assert not basename_offenders, (
        "REQ-CNST-010-E21 reintroduced under a path-form key: " + ", ".join(basename_offenders)
    )


def test_hook_registry_package_needs_no_line_limit_exemption() -> None:
    """Every hook_registry/ shard stays under the 1000-line default, proving E21 is dead.

    This is the substantive claim behind the retirement: if a shard ever exceeded the
    default ceiling, ``test_no_src_module_exceeds_line_limit`` would fail and the
    absence assertion above would be actively harmful rather than merely correct.
    """
    package_root = SRC_ROOT / "hook_registry"
    assert package_root.is_dir(), (
        f"expected the hook_registry package at {package_root} (issue #4853)"
    )
    assert not (SRC_ROOT / "hook_registry.py").exists(), (
        "the flat hook_registry.py module reappeared; the E21 retirement rationale "
        "in tests assumes the package decomposition from #4853"
    )
    offenders = []
    for path in sorted(package_root.rglob("*.py")):
        line_count = len(path.read_text(encoding="utf-8").splitlines())
        if line_count > 1000:
            offenders.append(f"{path.relative_to(SRC_ROOT)}: {line_count} lines")
    assert not offenders, (
        "hook_registry shards exceed the 1000-line default ceiling:\n  " + "\n  ".join(offenders)
    )


def test_recipe_binding_e24_exemption_is_retired() -> None:
    """REQ-CNST-010-E24 was retired by #4854 when input parsing moved to _binding_input.py.

    PR #4898 reintroduced the entry. ``recipe/_binding.py`` is under the 1000-line
    default ceiling, so no exemption is warranted. Mirrors the two-function E21
    decomposition above so sibling retirements follow the same shape.
    """
    assert "recipe/_binding.py" not in _LINE_LIMIT_EXEMPTIONS, (
        "REQ-CNST-010-E24 was retired by issue #4854; the recipe/_binding.py exemption "
        "must not be reintroduced"
    )
    basename_offenders = sorted(
        key for key in _LINE_LIMIT_EXEMPTIONS if key.rsplit("/", 1)[-1] == "_binding.py"
    )
    assert not basename_offenders, (
        "REQ-CNST-010-E24 reintroduced under a path-form key: " + ", ".join(basename_offenders)
    )


def test_recipe_binding_module_under_1000_lines() -> None:
    """The pre-condition for E24 retirement: ``recipe/_binding.py`` stays under 1000 lines.

    Mirrors ``test_hook_registry_package_needs_no_line_limit_exemption``: the absence
    assertion above is actively harmful rather than merely correct only if the
    pre-condition (sub-1000 module) still holds. If #4854 follow-ups move this
    module or extract more, the assertion fails with a clear message instead of
    crashing with ``FileNotFoundError`` deeper in the call stack.
    """
    binding = SRC_ROOT / "recipe" / "_binding.py"
    assert binding.exists(), (
        f"recipe/_binding.py must exist at {binding} (the E24 retirement assumes "
        "the post-#4854 module shape; further extraction should keep _binding.py "
        "in place or update this guard)"
    )
    line_count = len(binding.read_text(encoding="utf-8").splitlines())
    assert line_count <= 1000, (
        f"recipe/_binding.py is {line_count} lines; the E24 retirement assumed it stays "
        "under the 1000-line default ceiling (issue #4854 extracted _binding_input.py)"
    )
