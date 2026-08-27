from __future__ import annotations

from pathlib import Path

import pytest

from tests.arch._helpers import SRC_ROOT

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]


def test_sync_manifest_module_deleted():
    """REQ-SYNC-002: sync_manifest.py does not exist."""
    sync_path = Path(__file__).parent.parent.parent / "src" / "autoskillit" / "sync_manifest.py"
    assert not sync_path.exists()


def test_no_sync_manifest_imports_in_production_code():
    """REQ-SYNC-001: No production module imports from autoskillit.sync_manifest."""
    src_dir = Path(__file__).parent.parent.parent / "src"
    for py_file in src_dir.rglob("*.py"):
        content = py_file.read_text()
        for line in content.splitlines():
            stripped = line.strip()
            if stripped.startswith(("import ", "from ")):
                assert "sync_manifest" not in stripped, (
                    f"Found sync_manifest import in {py_file}: {line!r}"
                )


def test_pipeline_facade_reexports_subpackage_symbols() -> None:
    """Wavefront 1 of #4667: top-level facade must re-export DefaultContextAdmissionLedger."""
    import autoskillit.pipeline._context_admission_ledger as subpackage
    import autoskillit.pipeline.context_admission_ledger as facade

    assert facade.DefaultContextAdmissionLedger is subpackage.DefaultContextAdmissionLedger, (
        "Facade's DefaultContextAdmissionLedger must be the same class object as "
        "the subpackage's, so the public import path stays stable."
    )
    assert facade.__all__ == ["DefaultContextAdmissionLedger"], (
        f"Facade __all__ must list only DefaultContextAdmissionLedger; got {facade.__all__}"
    )


def test_pyproject_cyclopts_minimum_version() -> None:
    """cyclopts lower bound in pyproject.toml must be >=4.0, not >=3.0.

    cyclopts 3.x and 4.x have incompatible APIs. A >=3.0 constraint allows
    a conservative resolver to silently install 3.x, which fails at runtime.
    """
    import re

    toml_path = Path(__file__).parent.parent.parent / "pyproject.toml"
    content = toml_path.read_text()
    match = re.search(r'"cyclopts>=([\d.]+)"', content)
    assert match is not None, "cyclopts dependency not found in pyproject.toml"
    major = int(match.group(1).split(".")[0])
    assert major >= 4, (
        f"cyclopts minimum version is {match.group(1)}, expected >=4.0. "
        "cyclopts 3.x API is incompatible with the 4.x API used in this codebase."
    )


def test_pytest_asyncio_version_bound() -> None:
    """P11-2: pytest-asyncio lower bound must match the published 0.x stable series."""
    import tomllib

    pyproject = Path(__file__).parent.parent.parent / "pyproject.toml"
    data = tomllib.loads(pyproject.read_text())
    deps = data["project"]["optional-dependencies"]["dev"]
    asyncio_dep = next(d for d in deps if d.startswith("pytest-asyncio"))
    assert ">=1.0.0" in asyncio_dep, f"Expected pytest-asyncio>=1.0.0, got: {asyncio_dep!r}"


def test_recipe_subpackage_importable() -> None:
    """T1: recipe/ package exposes all expected symbols."""
    from autoskillit.recipe import (  # noqa: F401
        Recipe,
        RecipeStep,
        analyze_dataflow,
        check_contract_staleness,
        find_recipe_by_name,
        generate_recipe_card,
        iter_steps_with_context,
        list_recipes,
        load_bundled_manifest,
        load_recipe,
        load_recipe_card,
        run_semantic_rules,
        validate_recipe_cards,
        validate_recipe_structure,
    )


def test_contracts_module_has_staleitem() -> None:
    """T2: recipe/contracts.py exposes StaleItem and load_bundled_manifest."""
    from autoskillit.recipe.contracts import StaleItem, load_bundled_manifest  # noqa: F401


def test_validator_module_has_validate() -> None:
    """T3: validator.py exposes validate_recipe_structure + run_semantic_rules."""
    from autoskillit.recipe.validator import (  # noqa: F401
        analyze_dataflow,
        run_semantic_rules,
        validate_recipe_structure,
    )


def test_migration_subpackage_importable() -> None:
    """T4: migration/ package exposes MigrationEngine, applicable_migrations, FailureStore."""
    from autoskillit.migration import (  # noqa: F401
        FailureStore,
        MigrationEngine,
        applicable_migrations,
    )

    assert MigrationEngine is not None
    assert applicable_migrations is not None
    assert FailureStore is not None


def test_llm_triage_imports_from_contracts_not_validator() -> None:
    """T7: REQ-DSGN-007 — _llm_triage.py imports contract types, not recipe/validator.

    Accepts both direct sub-module import (recipe.contracts) and gateway import
    (autoskillit.recipe) since REQ-IMP-001 requires gateway imports for non-server/cli files.
    """
    src = (SRC_ROOT / "_llm_triage.py").read_text()
    assert (
        "recipe.contracts" in src
        or "recipe/contracts" in src
        or "from autoskillit.recipe import" in src
    ), "_llm_triage.py must import contract types from recipe package"
    assert "recipe.validator" not in src and "recipe_validator" not in src, (
        "_llm_triage.py must not import from recipe.validator or old recipe_validator"
    )


def test_old_flat_recipe_modules_removed() -> None:
    """T9a: old flat recipe modules must be deleted after sub-package migration."""
    for name in ("recipe_schema.py", "recipe_io.py", "recipe_loader.py", "recipe_validator.py"):
        assert not (SRC_ROOT / name).exists(), (
            f"{name} should be removed — code now lives in recipe/ sub-package"
        )


def test_old_flat_migration_modules_removed() -> None:
    """T9b: old flat migration modules must be deleted after sub-package migration."""
    for name in ("migration_engine.py", "migration_loader.py", "failure_store.py"):
        assert not (SRC_ROOT / name).exists(), (
            f"{name} should be removed — code now lives in migration/ sub-package"
        )


def test_server_is_package() -> None:
    """server/ must be a package directory, not a flat module."""
    assert (SRC_ROOT / "server").is_dir(), "server/ directory must exist"
    assert (SRC_ROOT / "server" / "__init__.py").exists()
    assert not (SRC_ROOT / "server.py").exists(), "server.py flat module must be deleted"


def test_cli_is_package() -> None:
    """cli/ must be a package directory, not a flat module."""
    assert (SRC_ROOT / "cli").is_dir(), "cli/ directory must exist"
    assert (SRC_ROOT / "cli" / "__init__.py").exists()
    assert not (SRC_ROOT / "cli.py").exists(), "cli.py flat module must be deleted"


def test_tools_integrations_replaced_by_split_modules() -> None:
    """tools_integrations.py deleted; four replacement modules exist."""
    server = SRC_ROOT / "server"
    assert not (server / "tools_integrations.py").exists()
    assert not (server / "tools" / "tools_issue_lifecycle.py").exists()
    assert (server / "tools" / "tools_github.py").exists()
    assert (server / "tools" / "tools_issue_headless.py").exists()
    assert (server / "tools" / "tools_issue_labels.py").exists()
    assert (server / "tools" / "tools_pr_ops.py").exists()


def test_split_files_under_750_lines() -> None:
    """Each split module must stay under the 750-line threshold."""
    server = SRC_ROOT / "server"
    for name in (
        "tools_github.py",
        "tools_issue_headless.py",
        "tools_issue_labels.py",
        "tools_pr_ops.py",
        "tools_execution/__init__.py",
        "tools_execution/_state.py",
        "tools_execution/_gates.py",
        "tools_execution/_audit_response.py",
        "tools_execution/_run_cmd.py",
        "tools_execution/_run_python.py",
        "tools_execution/_run_skill_admission.py",
        "tools_execution/_run_skill_prepare.py",
        "tools_execution/_run_skill_session.py",
        "tools_execution/_run_skill_finalize.py",
        "tools_execution/_run_skill_dispatch.py",
    ):
        lines = len((server / "tools" / name).read_text().splitlines())
        assert lines <= 750, f"{name} has {lines} lines, exceeds 750"


def test_extract_block_in_misc() -> None:
    """_extract_block lives in server/_misc.py."""
    from autoskillit.server._misc import _extract_block

    assert callable(_extract_block)


def test_all_tools_importable_from_split_modules() -> None:
    """All 8 tools are importable from their new home modules."""
    from autoskillit.server.tools.tools_github import (
        fetch_github_issue,
        get_issue_title,
        report_bug,
    )
    from autoskillit.server.tools.tools_issue_headless import prepare_issue
    from autoskillit.server.tools.tools_issue_labels import (
        claim_issue,
        release_issue,
    )
    from autoskillit.server.tools.tools_pr_ops import bulk_close_issues, get_pr_reviews

    for name, fn in [
        ("fetch_github_issue", fetch_github_issue),
        ("get_issue_title", get_issue_title),
        ("report_bug", report_bug),
        ("prepare_issue", prepare_issue),
        ("claim_issue", claim_issue),
        ("release_issue", release_issue),
        ("get_pr_reviews", get_pr_reviews),
        ("bulk_close_issues", bulk_close_issues),
    ]:
        assert callable(fn), f"{name} is not callable"


def test_git_operations_moved_to_server_package() -> None:
    """git_operations.py must be removed; its logic lives in server/git.py."""
    assert not (SRC_ROOT / "git_operations.py").exists()
    assert (SRC_ROOT / "server" / "git.py").exists()


def test_doctor_moved_to_cli_package() -> None:
    """_doctor.py must be removed; its logic lives in cli/_doctor.py."""
    assert not (SRC_ROOT / "_doctor.py").exists()
    assert (SRC_ROOT / "cli" / "doctor" / "__init__.py").exists()


def test_test_suite_has_domain_subdirectories():
    """All 12 domain-aligned test subdirectories exist after groupE reorganization."""
    tests_root = Path(__file__).parent.parent
    expected = [
        "core",
        "config",
        "pipeline",
        "execution",
        "workspace",
        "recipe",
        "migration",
        "server",
        "cli",
        "arch",
        "contracts",
        "infra",
    ]
    missing = [d for d in expected if not (tests_root / d / "__init__.py").exists()]
    assert not missing, f"Missing test subdirectories (run groupE): {missing}"


def test_test_suite_oversized_files_split():
    """No test file at tests/ root exceeds 1,000 lines after groupE split.

    Exemptions (rule ID | rationale):
      test_test_filter_core_cascade.py — REQ-CNST-004-E2: Cascade-map guard test
        whose per-stem expected-set mirroring is a one-line cascade consumers pin
        into ``expected_stems``. Adding issue #4741's three plugin-cache shards
        pushed the file to 1003 lines; splitting would scatter a single declared-
        vs-actual invariant across multiple files. Exempt at 1100 lines.
    """
    tests_root = Path(__file__).parent.parent
    over = [
        f"{f.name} ({len(f.read_text().splitlines())} lines)"
        for f in tests_root.glob("test_*.py")
        if len(f.read_text().splitlines()) > 1000
        and f.name != "test_test_filter_core_cascade.py"  # REQ-CNST-004-E2
    ]
    assert not over, f"Oversized test files remain (run groupE): {over}"


def test_smoke_utils_suite_is_split() -> None:
    tests_root = Path(__file__).parent.parent
    smoke_utils_root = tests_root / "smoke_utils"
    shards = sorted(smoke_utils_root.glob("test_*.py"))
    old_monolith = tests_root / f"test_{smoke_utils_root.name}.py"

    assert not old_monolith.exists()
    assert smoke_utils_root.is_dir()
    assert shards
    oversized = [path.name for path in shards if len(path.read_text().splitlines()) > 1000]
    assert not oversized, f"Smoke-utils shards exceed 1,000 lines: {oversized}"


def test_data_directories_are_not_python_packages() -> None:
    """REQ-ARCH-005: data-only directories under src/autoskillit/ must not
    contain __init__.py — that turns them into phantom Python packages
    distinct from the real IL-2 module of similar name."""
    src = Path(__file__).resolve().parents[2] / "src" / "autoskillit"
    data_dirs = {"migrations", "recipes", "skills", "skills_extended", "agents"}
    offenders: list[str] = []
    for name in data_dirs:
        d = src / name
        if not d.is_dir():
            continue
        init = d / "__init__.py"
        if init.exists():
            offenders.append(str(init.relative_to(src)))
    assert not offenders, (
        f"Data directories must not be Python packages. Remove __init__.py from: {offenders}"
    )
