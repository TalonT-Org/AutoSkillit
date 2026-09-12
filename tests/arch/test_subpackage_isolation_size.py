from __future__ import annotations

import pytest

from tests.arch._helpers import SRC_ROOT
from tests.arch._line_budget import count_budget_lines
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
        line_count = count_budget_lines(py_file)
        if line_count > 750:
            offenders.append(
                f"{py_file.relative_to(SRC_ROOT)}: {line_count} non-import lines (max 750)"
            )
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
    assert not violations, "Source module line-limit violations:\n" + "\n".join(
        f"  {v}" for v in violations
    )


def test_basename_fallback_dead_exemptions_are_retired() -> None:
    """REQ-CNST-010 basename-fallback fix (#4662): two rubber-stamp entries stay removed.

    The four bare-basename keys removed by #4662 (`types.py`, `session.py`,
    `_doctor.py`, `tools_recipe.py`) matched no file anywhere in
    src/autoskillit/ and are already covered by
    test_every_exemption_key_matches_an_existing_file, which fails on any key
    that does not resolve to a real file -- reintroducing them needs no
    dedicated assertion here. `server/_recipe_delivery.py`'s 750/750 exemption
    (now decomposed into `server/recipe/_recipe_delivery/`) was a rubber-stamp
    ceiling equal to its own line count (see
    test_no_exemption_ceiling_equals_current_line_count) and, like
    `server/_recipe_section_pagination.py` (465 lines, limit 750, now
    `server/recipe/_recipe_section_pagination.py`), is redundant now that 750
    is the universal default under REQ-CNST-010's diff-scoped gate. The keys
    below are the historical flat-path strings that must stay absent -- do
    not path-migrate them to the post-#4673 locations, or a reintroduced
    exemption under the old key would silently escape this guard.
    """
    retired = {
        "server/_recipe_delivery.py",
        "server/_recipe_section_pagination.py",
    }
    stale = retired.intersection(_LINE_LIMIT_EXEMPTIONS)
    assert not stale, f"Retired basename-fallback exemptions reintroduced: {sorted(stale)}"


def test_new_recipe_delivery_canonical_paths_need_no_line_limit_exemption() -> None:
    """The post-#4673 canonical paths stay healthy, so the retirement protection
    in ``test_basename_fallback_dead_exemptions_are_retired`` actually moves with
    the code instead of only pinning the old flat-path keys absent.

    Mirrors ``test_hook_registry_package_needs_no_line_limit_exemption`` /
    ``test_recipe_binding_module_under_1000_lines``: each shard of the
    decomposed ``server/recipe/_recipe_delivery/`` package, plus the still-flat
    ``server/recipe/_recipe_section_pagination.py``, must carry no
    ``_LINE_LIMIT_EXEMPTIONS`` entry and stay under the 1000-line default
    ceiling.
    """
    canonical_paths = sorted((SRC_ROOT / "server" / "recipe" / "_recipe_delivery").rglob("*.py"))
    assert canonical_paths, "expected shards under server/recipe/_recipe_delivery/ (issue #4673)"
    canonical_paths.append(SRC_ROOT / "server" / "recipe" / "_recipe_section_pagination.py")

    offenders = []
    for path in canonical_paths:
        rel = str(path.relative_to(SRC_ROOT))
        if rel in _LINE_LIMIT_EXEMPTIONS:
            offenders.append(f"{rel}: unexpectedly present in _LINE_LIMIT_EXEMPTIONS")
            continue
        line_count = count_budget_lines(path)
        if line_count > 1000:
            offenders.append(
                f"{rel}: {line_count} non-import lines (exceeds default 1000-line ceiling, "
                "no exemption present)"
            )
    assert not offenders, (
        "New canonical post-#4673 paths need a fresh exemption or a split:\n  "
        + "\n  ".join(offenders)
    )


def test_every_exemption_key_matches_an_existing_file() -> None:
    """Every _LINE_LIMIT_EXEMPTIONS key must resolve to a real file under SRC_ROOT.

    A key matching nothing is dead weight no test can ever exercise -- exactly
    how types.py, session.py, and _doctor.py sat unnoticed until #4662's
    basename-fallback fix.
    """
    dead = sorted(key for key in _LINE_LIMIT_EXEMPTIONS if not (SRC_ROOT / key).is_file())
    assert not dead, f"_LINE_LIMIT_EXEMPTIONS keys with no matching file: {dead}"


def test_no_exemption_ceiling_equals_current_line_count() -> None:
    """An exemption ceiling equal to the file's current non-import line count is a rubber stamp.

    Cross-cutting finding from issue #4662: at the time the issue was filed,
    codex.py's ceiling (2444) and fleet/_api.py's ceiling (1590) each equaled
    the line count *at the moment they were written*, guaranteeing the very
    next line added trips the guard the ceiling was supposed to satisfy.
    Both have since been corrected -- codex.py's exemption is narrower than
    its original rubber-stamp value and fleet/_api.py carries no exemption at
    all -- this test guards against the pattern recurring for any entry.
    Ceilings must carry real headroom.
    """
    offenders = [
        f"{rel}: limit {exemption.limit} equals current non-import line count"
        for rel, exemption in sorted(_LINE_LIMIT_EXEMPTIONS.items())
        if (SRC_ROOT / rel).is_file() and count_budget_lines(SRC_ROOT / rel) == exemption.limit
    ]
    assert not offenders, (
        "Exemption ceilings equal to the current non-import line count are rubber stamps -- "
        "raise the ceiling to give real headroom, or remove the entry if the file "
        "no longer needs one:\n" + "\n".join(f"  {o}" for o in offenders)
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
    alone is ~720 lines; imports are excluded from the count; the
    remainder is module docstring and the package's ``__init__.py``
    facade re-export surface, which the test also pins so the
    re-export facade itself cannot regress.
    """
    package_dir = SRC_ROOT / "pipeline" / "exploration_context"
    violations: list[tuple[str, int]] = []
    for shard in sorted(package_dir.glob("*.py")):
        line_count = count_budget_lines(shard)
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
    line_count = count_budget_lines(store_path)
    assert line_count <= 750, (
        f"pipeline/exploration_context/_store.py exceeds the 750-line ceiling: "
        f"{line_count} non-import lines"
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
    so the retired root-level exemption is not warranted.
    """
    assert "hook_registry.py" not in _LINE_LIMIT_EXEMPTIONS, (
        "REQ-CNST-010-E21 was retired by issue #4853; the hook_registry.py exemption "
        "must not be reintroduced (no such source file exists)"
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
        line_count = count_budget_lines(path)
        if line_count > 1000:
            offenders.append(f"{path.relative_to(SRC_ROOT)}: {line_count} non-import lines")
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
    line_count = count_budget_lines(binding)
    assert line_count <= 1000, (
        f"recipe/_binding.py is {line_count} non-import lines; the E24 retirement assumed it "
        "stays under the 1000-line default ceiling (issue #4854 extracted _binding_input.py)"
    )
