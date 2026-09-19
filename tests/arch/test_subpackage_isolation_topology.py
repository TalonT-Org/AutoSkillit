from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.arch._helpers import SRC_ROOT
from tests.arch._line_budget import count_budget_lines

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]


def test_sync_manifest_module_deleted():
    """REQ-SYNC-002: sync_manifest.py does not exist."""
    sync_path = SRC_ROOT / "sync_manifest.py"
    assert not sync_path.exists()


def test_no_sync_manifest_imports_in_production_code():
    """REQ-SYNC-001: No production module imports from autoskillit.sync_manifest."""
    src_dir = SRC_ROOT.parent
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

    toml_path = SRC_ROOT.parents[1] / "pyproject.toml"
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

    pyproject = SRC_ROOT.parents[1] / "pyproject.toml"
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
    """T2: recipe/contracts/contracts.py exposes StaleItem and load_bundled_manifest."""
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
        lines = count_budget_lines(server / "tools" / name)
        assert lines <= 750, f"{name} has {lines} non-import lines, exceeds 750"


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


_EXCLUDED_TEST_DIRS: frozenset[str] = frozenset({"__pycache__", "fixtures"})

# Tree-entry regex: matches the ASCII tree-drawing characters that head each directory
# entry in tests/AGENTS.md. The character class accepts letters, digits, underscores,
# and hyphens — sufficient for every current top-level dir while excluding path
# separators and tree-drawing characters. The terminator after ``/`` allows a trailing
# inline comment (the inventory lines are all annotated) and end-of-line.
_TREE_ENTRY_PATTERN = re.compile(
    r"^(?:├── |└── |│   └── )([a-z0-9_\-]+)/(?:$|\s)",
    re.MULTILINE,
)

# Fence-block regex: capture the body of a triple-backtick code block.
_FENCE_PATTERN = re.compile(r"```([^\n]*)\n(.*?)\n```", re.DOTALL)

# Anchor regex: match the ``tests/`` directory entry that heads the inventory tree.
_TESTS_ANCHOR_PATTERN = re.compile(r"^tests/$", re.MULTILINE)


def _iter_top_level_test_dirs(tests_root: Path) -> list[str]:
    """Return the sorted list of immediate-child directories of ``tests_root``,
    excluding ``__pycache__`` and ``fixtures``."""
    return sorted(
        p.name for p in tests_root.iterdir() if p.is_dir() and p.name not in _EXCLUDED_TEST_DIRS
    )


def _extract_tests_tree_block(agents_md: str) -> str:
    """Return the body of the triple-backtick fence that contains the ``tests/`` tree."""
    for match in _FENCE_PATTERN.finditer(agents_md):
        body = match.group(2)
        if _TESTS_ANCHOR_PATTERN.search(body):
            return body
    raise AssertionError("tests/ directory-tree fence not found in tests/AGENTS.md")


def test_test_suite_has_domain_subdirectories() -> None:
    """Every top-level tests/<dir> directory (excluding __pycache__ and fixtures/) exists
    and contains at least one test_*.py file or documented purpose.

    Replaces the prior 12-entry hardcoded subset check (#4600 C6.3): each top-level
    subdirectory must be wired up either as a Python package (carries __init__.py) or as a
    test-collection-only subtree (carries test_*.py files). The package-style vs.
    test-collection-only distinction is enforced separately by
    test_package_style_domain_subdirectories_carry_init_py.
    """
    tests_root = SRC_ROOT.parents[1] / "tests"
    top_level = _iter_top_level_test_dirs(tests_root)
    assert top_level, f"No top-level directories found under {tests_root}"

    missing: list[str] = []
    for name in top_level:
        dir_path = tests_root / name
        has_init = (dir_path / "__init__.py").is_file()
        has_tests = any(dir_path.glob("test_*.py"))
        if not (has_init or has_tests):
            missing.append(name)

    assert not missing, (
        f"Top-level tests/ subdirectory count is {len(top_level)}; "
        f"directories missing both __init__.py and test_*.py: {missing}"
    )


def test_test_suite_inventory_docstring_matches_actual_count() -> None:
    """Every actual top-level tests/ dir appears as a tree entry in tests/AGENTS.md (#4600 C6.5).

    Parses the fenced code block in tests/AGENTS.md whose body contains ``tests/`` and
    verifies that every immediate-child directory of ``tests_root`` (excluding
    __pycache__ and fixtures) is present as a tree entry. The contract enforces the
    "every actual dir is listed" direction; an entry that appears in the inventory but
    is not on disk (e.g., the nested ``context_admission_journals/`` under fixtures/) is
    informational only and does not violate the contract.
    """
    tests_root = SRC_ROOT.parents[1] / "tests"
    agents_md = (tests_root / "AGENTS.md").read_text(encoding="utf-8")

    tree_block = _extract_tests_tree_block(agents_md)
    listed_dirs = set(_TREE_ENTRY_PATTERN.findall(tree_block))

    actual_dirs = set(_iter_top_level_test_dirs(tests_root))
    missing = sorted(actual_dirs - listed_dirs)

    assert not missing, (
        f"tests/AGENTS.md directory inventory is incomplete (#4600 C6.5): "
        f"{missing} missing from inventory; "
        f"actual top-level dirs ({len(actual_dirs)}) vs listed ({len(listed_dirs)})"
    )


def test_no_stale_smoke_utils_exemption_rationale() -> None:
    """REQ-CNST-004-E1 retirement guard (#4600 C6.4): the historical 'Exempt at 1348 lines'
    rationale that justified keeping tests/test_smoke_utils.py as a monolith must stay gone.

    Commit 89236acd8 split the monolith into tests/smoke_utils/ shards and dropped the
    exemption; this guard prevents either the rationale or the bare-monolith file from
    silently returning under the same REQ-CNST-004-E1 label anywhere under tests/.
    """
    tests_root = SRC_ROOT.parents[1] / "tests"
    assert not (tests_root / "test_smoke_utils.py").is_file(), (
        "Bare-monolith tests/test_smoke_utils.py returned; the REQ-CNST-004-E1 split "
        "into tests/smoke_utils/ shards must not be reverted."
    )
    guard_path = Path(__file__).resolve()
    forbidden_substrings = ("REQ-CNST-004-E1", "Exempt at 1348 lines", "tests/test_smoke_utils.py")
    missing: list[tuple[str, str]] = []
    for py_file in sorted(tests_root.rglob("*.py")):
        if py_file.resolve() == guard_path:
            continue
        text = py_file.read_text(encoding="utf-8")
        for needle in forbidden_substrings:
            if needle in text:
                missing.append((str(py_file.relative_to(tests_root)), needle))
    assert not missing, (
        "Stale REQ-CNST-004-E1 / 1348-line exemption rationale reappeared (#4600 C6.4):\n  "
        + "\n  ".join(f"{path}: {needle}" for path, needle in missing)
    )


def test_package_style_domain_subdirectories_carry_init_py() -> None:
    """The package-style test subdirectories each carry __init__.py.

    Splits test_test_suite_has_domain_subdirectories' conflated "all dirs must be
    packages" claim: exploration/ and report/ are intentionally package-less data
    subtrees; the remaining top-level dirs that carry __init__.py stay that way. The
    22-name set is curated: see ``tests/AGENTS.md`` for the documented partition.
    """
    package_style = {
        "arch",
        "assets",
        "backend",
        "cli",
        "config",
        "contracts",
        "core",
        "docs",
        "execution",
        "fleet",
        "hooks",
        "infra",
        "integration",
        "migration",
        "pipeline",
        "planner",
        "recipe",
        "server",
        "skills",
        "skills_extended",
        "smoke_utils",
        "workspace",
    }
    tests_root = SRC_ROOT.parents[1] / "tests"
    missing_init = sorted(
        name for name in package_style if not (tests_root / name / "__init__.py").is_file()
    )
    assert not missing_init, (
        f"Package-style test subdirectories lost their __init__.py: {missing_init}"
    )


def test_smoke_utils_suite_is_split() -> None:
    tests_root = SRC_ROOT.parents[1] / "tests"
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
    src = SRC_ROOT
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
