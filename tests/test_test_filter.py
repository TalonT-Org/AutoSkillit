"""Tests for tests/_test_filter.py — standalone test-path filtering logic."""

from __future__ import annotations

import ast
import subprocess
from pathlib import Path

import pytest

from autoskillit._test_filter import apply_manifest as manifest_apply_manifest
from autoskillit._test_filter import load_manifest as manifest_load_manifest
from tests._test_filter import (
    ALWAYS_RUN_AGGRESSIVE,
    ALWAYS_RUN_CONSERVATIVE,
    LAYER_CASCADE_AGGRESSIVE,
    LAYER_CASCADE_CONSERVATIVE,
    ASTImportWalker,
    FilterMode,
    ImportContext,
    _expand_reexport_closure,
    apply_manifest,
    build_test_scope,
    check_bucket_a,
    git_changed_files,
    load_manifest,
)

pytest_plugins = ["pytester"]

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = PROJECT_ROOT / ".autoskillit" / "test-filter-manifest.yaml"

# ---------------------------------------------------------------------------
# Walker Tests (W1–W8)
# ---------------------------------------------------------------------------


class TestASTImportWalker:
    def test_walker_top_level_import(self) -> None:
        tree = ast.parse("import os")
        walker = ASTImportWalker()
        walker.visit(tree)
        assert ("os", ImportContext.TOP_LEVEL) in walker.imports

    def test_walker_top_level_from_import(self) -> None:
        tree = ast.parse("from pathlib import Path")
        walker = ASTImportWalker()
        walker.visit(tree)
        assert ("pathlib", ImportContext.TOP_LEVEL) in walker.imports

    def test_walker_relative_from_import(self) -> None:
        tree = ast.parse("from .sub import X")
        walker = ASTImportWalker()
        walker.visit(tree)
        assert (".sub", ImportContext.TOP_LEVEL) in walker.imports

    def test_walker_conditional_import(self) -> None:
        source = "import sys\nif sys.platform == 'linux':\n    import foo"
        tree = ast.parse(source)
        walker = ASTImportWalker()
        walker.visit(tree)
        assert ("foo", ImportContext.CONDITIONAL) in walker.imports

    def test_walker_type_checking_guard(self) -> None:
        source = "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    from foo import Bar"
        tree = ast.parse(source)
        walker = ASTImportWalker()
        walker.visit(tree)
        assert ("foo", ImportContext.TYPE_CHECKING) in walker.imports

    def test_walker_type_checking_attribute(self) -> None:
        source = "import typing\nif typing.TYPE_CHECKING:\n    from bar import Baz"
        tree = ast.parse(source)
        walker = ASTImportWalker()
        walker.visit(tree)
        assert ("bar", ImportContext.TYPE_CHECKING) in walker.imports

    def test_walker_deferred_import(self) -> None:
        source = "def f():\n    import foo"
        tree = ast.parse(source)
        walker = ASTImportWalker()
        walker.visit(tree)
        assert ("foo", ImportContext.DEFERRED) in walker.imports

    def test_walker_importlib_literal(self) -> None:
        source = 'import importlib\nimportlib.import_module("foo")'
        tree = ast.parse(source)
        walker = ASTImportWalker()
        walker.visit(tree)
        assert ("foo", ImportContext.IMPORTLIB) in walker.imports


# ---------------------------------------------------------------------------
# Bucket A Tests (B1–B9)
# ---------------------------------------------------------------------------


class TestCheckBucketA:
    def test_bucket_a_conftest(self) -> None:
        assert check_bucket_a({"tests/conftest.py"}) is True

    def test_bucket_a_helpers(self) -> None:
        assert check_bucket_a({"tests/_helpers.py"}) is True

    def test_bucket_a_arch_helpers(self) -> None:
        assert check_bucket_a({"tests/arch/_helpers.py"}) is True
        assert check_bucket_a({"tests/arch/_rules.py"}) is True

    def test_bucket_a_pyproject(self) -> None:
        assert check_bucket_a({"pyproject.toml"}) is True

    def test_bucket_a_uv_lock(self) -> None:
        assert check_bucket_a({"uv.lock"}) is True

    def test_bucket_a_precommit(self) -> None:
        assert check_bucket_a({".pre-commit-config.yaml"}) is True

    def test_bucket_a_factory(self) -> None:
        assert check_bucket_a({"src/autoskillit/server/_factory.py"}) is True

    def test_bucket_a_subdir_conftest(self) -> None:
        assert check_bucket_a({"tests/execution/conftest.py"}) is True

    def test_bucket_a_negative(self) -> None:
        assert check_bucket_a({"src/autoskillit/core/io.py"}) is False


# ---------------------------------------------------------------------------
# build_test_scope Tests (S1–S10)
# ---------------------------------------------------------------------------


class TestBuildTestScope:
    def test_scope_none_changed_returns_none(self, tmp_path: Path) -> None:
        result = build_test_scope(
            changed_files=None,
            mode=FilterMode.CONSERVATIVE,
            tests_root=tmp_path / "tests",
        )
        assert result is None

    def test_scope_large_changeset_returns_none(self, tmp_path: Path) -> None:
        files = {f"src/autoskillit/core/f{i}.py" for i in range(31)}
        result = build_test_scope(
            changed_files=files,
            mode=FilterMode.CONSERVATIVE,
            tests_root=tmp_path / "tests",
        )
        assert result is None

    def test_scope_bucket_a_returns_none(self, tmp_path: Path) -> None:
        result = build_test_scope(
            changed_files={"pyproject.toml"},
            mode=FilterMode.CONSERVATIVE,
            tests_root=tmp_path / "tests",
        )
        assert result is None

    def test_scope_l0_core_conservative(self, tmp_path: Path) -> None:
        tests_root = tmp_path / "tests"
        for d in [
            "core",
            "config",
            "execution",
            "pipeline",
            "workspace",
            "recipe",
            "migration",
            "server",
            "cli",
            "hooks",
            "skills",
            "arch",
            "contracts",
            "infra",
            "docs",
        ]:
            (tests_root / d).mkdir(parents=True, exist_ok=True)

        result = build_test_scope(
            changed_files={"src/autoskillit/core/io.py"},
            mode=FilterMode.CONSERVATIVE,
            tests_root=tests_root,
        )
        assert result is not None
        dir_names = {p.name for p in result}
        for expected in [
            "core",
            "config",
            "execution",
            "pipeline",
            "workspace",
            "recipe",
            "migration",
            "server",
            "cli",
            "hooks",
            "skills",
        ]:
            assert expected in dir_names, f"{expected} missing from cascade"
        for always in ["arch", "contracts", "infra", "docs"]:
            assert always in dir_names, f"always-run {always} missing"

    def test_scope_l1_execution_conservative(self, tmp_path: Path) -> None:
        tests_root = tmp_path / "tests"
        for d in [
            "execution",
            "core",
            "workspace",
            "migration",
            "server",
            "cli",
            "infra",
            "skills",
            "arch",
            "contracts",
            "docs",
        ]:
            (tests_root / d).mkdir(parents=True, exist_ok=True)

        result = build_test_scope(
            changed_files={"src/autoskillit/execution/headless.py"},
            mode=FilterMode.CONSERVATIVE,
            tests_root=tests_root,
        )
        assert result is not None
        dir_names = {p.name for p in result}
        for expected in [
            "execution",
            "core",
            "workspace",
            "migration",
            "server",
            "cli",
            "infra",
            "skills",
        ]:
            assert expected in dir_names, f"{expected} missing from cascade"

    def test_scope_l2_recipe_conservative(self, tmp_path: Path) -> None:
        tests_root = tmp_path / "tests"
        for d in [
            "recipe",
            "execution",
            "server",
            "cli",
            "infra",
            "skills",
            "arch",
            "contracts",
            "docs",
        ]:
            (tests_root / d).mkdir(parents=True, exist_ok=True)

        result = build_test_scope(
            changed_files={"src/autoskillit/recipe/schema.py"},
            mode=FilterMode.CONSERVATIVE,
            tests_root=tests_root,
        )
        assert result is not None
        dir_names = {p.name for p in result}
        for expected in ["recipe", "execution", "server", "cli", "infra", "skills"]:
            assert expected in dir_names, f"{expected} missing from cascade"

    def test_scope_l3_server_conservative(self, tmp_path: Path) -> None:
        tests_root = tmp_path / "tests"
        for d in ["server", "cli", "infra", "arch", "contracts", "docs"]:
            (tests_root / d).mkdir(parents=True, exist_ok=True)

        result = build_test_scope(
            changed_files={"src/autoskillit/server/helpers.py"},
            mode=FilterMode.CONSERVATIVE,
            tests_root=tests_root,
        )
        assert result is not None
        dir_names = {p.name for p in result}
        assert "server" in dir_names
        assert "cli" in dir_names
        assert "infra" in dir_names

    def test_scope_test_file_included_directly(self, tmp_path: Path) -> None:
        tests_root = tmp_path / "tests"
        for d in ["arch", "contracts", "infra", "docs"]:
            (tests_root / d).mkdir(parents=True, exist_ok=True)

        result = build_test_scope(
            changed_files={"tests/core/test_io.py"},
            mode=FilterMode.CONSERVATIVE,
            tests_root=tests_root,
        )
        assert result is not None
        assert Path("tests/core/test_io.py") in result

    def test_scope_nonpython_no_manifest_only_alwaysrun(self, tmp_path: Path) -> None:
        """Non-Python file with manifest=None → fail-open → result is None."""
        tests_root = tmp_path / "tests"
        for d in ["arch", "contracts", "infra", "docs"]:
            (tests_root / d).mkdir(parents=True, exist_ok=True)

        result = build_test_scope(
            changed_files={"README.md"},
            mode=FilterMode.CONSERVATIVE,
            tests_root=tests_root,
        )
        assert result is None

    def test_scope_nonpython_unmatched_manifest_returns_none(self, tmp_path: Path) -> None:
        """A non-Python changed file with no manifest match must return None (full run)."""
        tests_root = tmp_path / "tests"
        for d in ["arch", "contracts", "infra", "docs"]:
            (tests_root / d).mkdir(parents=True, exist_ok=True)
        manifest = {"docs/**/*.md": ["docs"]}
        result = build_test_scope(
            changed_files={"some/unknown/file.txt"},
            mode=FilterMode.CONSERVATIVE,
            manifest=manifest,
            tests_root=tests_root,
        )
        assert result is None

    def test_scope_nonpython_matched_manifest_adds_dirs(self, tmp_path: Path) -> None:
        """A non-Python file that DOES match a manifest pattern contributes its test dirs."""
        tests_root = tmp_path / "tests"
        for d in ["arch", "contracts", "infra", "docs"]:
            (tests_root / d).mkdir(parents=True, exist_ok=True)
        manifest = {"docs/**/*.md": ["docs"]}
        result = build_test_scope(
            changed_files={"docs/README.md"},
            mode=FilterMode.CONSERVATIVE,
            manifest=manifest,
            tests_root=tests_root,
        )
        assert result is not None
        dir_names = {p.name for p in result}
        assert dir_names == {"arch", "contracts", "infra", "docs"}

    def test_scope_none_mode_returns_none(self, tmp_path: Path) -> None:
        result = build_test_scope(
            changed_files={"src/autoskillit/core/io.py"},
            mode=FilterMode.NONE,
            tests_root=tmp_path / "tests",
        )
        assert result is None

    def test_scope_empty_changeset(self, tmp_path: Path) -> None:
        tests_root = tmp_path / "tests"
        for d in ["arch", "contracts", "infra", "docs"]:
            (tests_root / d).mkdir(parents=True, exist_ok=True)

        result = build_test_scope(
            changed_files=set(),
            mode=FilterMode.CONSERVATIVE,
            tests_root=tests_root,
        )
        assert result is not None
        dir_names = {p.name for p in result}
        assert dir_names == {"arch", "contracts", "infra", "docs"}


# ---------------------------------------------------------------------------
# Conservative vs Aggressive Tests (M1–M4)
# ---------------------------------------------------------------------------


class TestFilterModes:
    def test_conservative_always_run_includes_infra(self) -> None:
        assert "arch" in ALWAYS_RUN_CONSERVATIVE
        assert "contracts" in ALWAYS_RUN_CONSERVATIVE
        assert "infra" in ALWAYS_RUN_CONSERVATIVE
        assert "docs" in ALWAYS_RUN_CONSERVATIVE

    def test_aggressive_always_run_excludes_infra(self) -> None:
        assert "arch" in ALWAYS_RUN_AGGRESSIVE
        assert "contracts" in ALWAYS_RUN_AGGRESSIVE
        assert "infra" not in ALWAYS_RUN_AGGRESSIVE
        assert "docs" not in ALWAYS_RUN_AGGRESSIVE

    def test_aggressive_ast_refinement(self, tmp_path: Path) -> None:
        tests_root = tmp_path / "tests"
        (tests_root / "core").mkdir(parents=True)
        (tests_root / "arch").mkdir()
        (tests_root / "contracts").mkdir()

        result_aggressive = build_test_scope(
            changed_files={"src/autoskillit/core/io.py"},
            mode=FilterMode.AGGRESSIVE,
            tests_root=tests_root,
        )
        assert result_aggressive is not None
        dir_names = {p.name for p in result_aggressive}
        assert "core" in dir_names
        assert "arch" in dir_names
        assert "contracts" in dir_names

    def test_conservative_wider_cascade(self) -> None:
        for pkg in LAYER_CASCADE_CONSERVATIVE:
            if pkg in LAYER_CASCADE_AGGRESSIVE:
                assert LAYER_CASCADE_AGGRESSIVE[pkg] <= LAYER_CASCADE_CONSERVATIVE[pkg], (
                    f"Aggressive cascade for {pkg} is not a subset of conservative"
                )


# ---------------------------------------------------------------------------
# Git Diff Edge Cases (G1–G5)
# ---------------------------------------------------------------------------


class TestGitChangedFiles:
    def test_git_changed_files_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake_result = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="src/autoskillit/core/io.py\ntests/core/test_io.py\n",
        )
        monkeypatch.setattr(subprocess, "run", lambda *a, **kw: fake_result)
        result = git_changed_files("/fake", base_ref="main")
        assert result == {"src/autoskillit/core/io.py", "tests/core/test_io.py"}

    def test_git_changed_files_failure_returns_none(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        def _raise(*a: object, **kw: object) -> None:
            raise subprocess.CalledProcessError(1, "git")

        monkeypatch.setattr(subprocess, "run", _raise)
        result = git_changed_files("/fake", base_ref="main")
        assert result is None

    def test_git_changed_files_timeout_returns_none(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        def _raise(*a: object, **kw: object) -> None:
            raise subprocess.TimeoutExpired("git", 10)

        monkeypatch.setattr(subprocess, "run", _raise)
        result = git_changed_files("/fake", base_ref="main")
        assert result is None

    def test_git_changed_files_env_override(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("AUTOSKILLIT_TEST_BASE_REF", "feature-branch")
        monkeypatch.delenv("GITHUB_BASE_REF", raising=False)

        captured_args: list[list[str]] = []

        def _capture(*a: object, **kw: object) -> subprocess.CompletedProcess[str]:
            captured_args.append(list(a[0]))
            return subprocess.CompletedProcess(args=[], returncode=0, stdout="")

        monkeypatch.setattr(subprocess, "run", _capture)
        git_changed_files("/fake")
        assert captured_args
        assert "feature-branch...HEAD" in captured_args[0]

    def test_git_changed_files_github_base_ref(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("AUTOSKILLIT_TEST_BASE_REF", raising=False)
        monkeypatch.setenv("GITHUB_BASE_REF", "main")

        captured_args: list[list[str]] = []

        def _capture(*a: object, **kw: object) -> subprocess.CompletedProcess[str]:
            captured_args.append(list(a[0]))
            return subprocess.CompletedProcess(args=[], returncode=0, stdout="")

        monkeypatch.setattr(subprocess, "run", _capture)
        git_changed_files("/fake")
        assert captured_args
        assert "main...HEAD" in captured_args[0]


# ---------------------------------------------------------------------------
# Re-export Closure Tests (R1–R2)
# ---------------------------------------------------------------------------


class TestReexportClosure:
    def test_reexport_closure_direct_init(self, tmp_path: Path) -> None:
        pkg = tmp_path / "pkg"
        pkg.mkdir()
        (pkg / "sub.py").write_text("x = 1\n")
        (pkg / "__init__.py").write_text("from .sub import x\n")

        result = _expand_reexport_closure({"pkg/sub.py"}, tmp_path)
        assert "pkg/__init__.py" in result
        assert "pkg/sub.py" in result

    def test_reexport_closure_no_match(self, tmp_path: Path) -> None:
        pkg = tmp_path / "pkg"
        pkg.mkdir()
        (pkg / "other.py").write_text("y = 2\n")
        (pkg / "__init__.py").write_text("from .sub import x\n")

        result = _expand_reexport_closure({"pkg/other.py"}, tmp_path)
        assert "pkg/__init__.py" not in result
        assert "pkg/other.py" in result


# ---------------------------------------------------------------------------
# Manifest Tests — tests/_test_filter (MA1–MA4)
# ---------------------------------------------------------------------------


class TestLoadManifest:
    def test_load_manifest_absent(self, tmp_path: Path) -> None:
        result = load_manifest(tmp_path)
        assert result is None

    def test_load_manifest_valid(self, tmp_path: Path) -> None:
        manifest_dir = tmp_path / ".autoskillit"
        manifest_dir.mkdir()
        (manifest_dir / "test-filter-manifest.yaml").write_text(
            "patterns:\n  'docs/*.md':\n    - docs\n"
        )
        result = load_manifest(tmp_path)
        assert result is not None
        assert "patterns" in result
        assert "docs/*.md" in result["patterns"]

    def test_load_manifest_malformed_yaml(self, tmp_path: Path) -> None:
        manifest_dir = tmp_path / ".autoskillit"
        manifest_dir.mkdir()
        (manifest_dir / "test-filter-manifest.yaml").write_text(":\n  - :\n  bad: [")
        import warnings

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            result = load_manifest(tmp_path)
        assert result is None
        assert any("Malformed YAML" in str(warning.message) for warning in w)


class TestApplyManifest:
    def test_apply_manifest_none(self) -> None:
        result = apply_manifest({"README.md"}, None)
        assert result is None

    def test_apply_manifest_match(self) -> None:
        manifest = {"docs/*.md": ["docs"]}
        result = apply_manifest({"docs/README.md"}, manifest)
        assert result == {"docs"}

    def test_apply_manifest_no_match(self) -> None:
        manifest = {"tests/*.py": ["unit"]}
        result = apply_manifest({"src/foo.py"}, manifest)
        assert result is None

    def test_apply_manifest_list_dirs(self) -> None:
        manifest = {"*.yaml": ["config", "infra"]}
        result = apply_manifest({"defaults.yaml"}, manifest)
        assert result == {"config", "infra"}

    def test_apply_manifest_doublestar_zero_segments(self) -> None:
        """docs/**/*.md must match docs/README.md (zero intermediate path segments).

        This pattern is in the production manifest. fnmatch fails this; pathspec passes it.
        """
        manifest = {"docs/**/*.md": ["docs"]}
        result = apply_manifest({"docs/README.md"}, manifest)
        assert result == {"docs"}

    def test_apply_manifest_doublestar_nested(self) -> None:
        """docs/**/*.md must match docs/developer/SETUP.md (non-zero intermediate segments)."""
        manifest = {"docs/**/*.md": ["docs"]}
        result = apply_manifest({"docs/developer/SETUP.md"}, manifest)
        assert result == {"docs"}


# ---------------------------------------------------------------------------
# Behavioral equivalence cross-validation (EQ1–EQ2)
# ---------------------------------------------------------------------------


class TestApplyManifestEquivalence:
    """Cross-validates conftest apply_manifest against production manifest_apply_manifest.

    Both modules must agree on matched outputs. When a file matches patterns in one,
    it must match the same patterns in the other. The two implementations are allowed
    to differ only in how they handle the unmatched case (both must return None there too).
    """

    MANIFEST = {
        "src/autoskillit/recipes/*.yaml": ["recipe", "contracts"],
        "docs/**/*.md": ["docs"],
        "Taskfile.yml": ["infra"],
        "tests/recipe/fixtures/*": ["recipe"],
        ".pre-commit-config.yaml": ["infra"],
    }

    @pytest.mark.parametrize(
        "file_path,expected_dirs",
        [
            ("src/autoskillit/recipes/my.yaml", {"recipe", "contracts"}),
            ("docs/README.md", {"docs"}),
            ("docs/sub/dir/deep.md", {"docs"}),
            ("Taskfile.yml", {"infra"}),
            ("tests/recipe/fixtures/test.yaml", {"recipe"}),
            (".pre-commit-config.yaml", {"infra"}),
        ],
    )
    def test_matching_cases_agree(self, file_path: str, expected_dirs: set[str]) -> None:
        """For files that match manifest patterns, both implementations return the same dirs."""
        conftest_result = apply_manifest({file_path}, self.MANIFEST)
        production_result = manifest_apply_manifest([file_path], self.MANIFEST)
        assert conftest_result == production_result == expected_dirs, (
            f"Implementations disagree for {file_path!r}: "
            f"conftest={conftest_result!r}, production={production_result!r}"
        )

    @pytest.mark.parametrize(
        "file_path",
        [
            "some/unknown/file.txt",
            "README.md",
            "pyproject.toml",
        ],
    )
    def test_unmatched_cases_both_return_none(self, file_path: str) -> None:
        """For files matching no manifest pattern, both implementations return None."""
        conftest_result = apply_manifest({file_path}, self.MANIFEST)
        production_result = manifest_apply_manifest([file_path], self.MANIFEST)
        assert conftest_result is None, f"conftest returned {conftest_result!r} for {file_path!r}"
        assert production_result is None, (
            f"production returned {production_result!r} for {file_path!r}"
        )


# ---------------------------------------------------------------------------
# Manifest Tests — autoskillit._test_filter (pathspec-based)
# ---------------------------------------------------------------------------


class TestManifestLoadManifest:
    def test_load_manifest_parses_yaml(self) -> None:
        manifest = manifest_load_manifest(MANIFEST_PATH)
        assert isinstance(manifest, dict)
        assert len(manifest) >= 22
        for pattern, dirs in manifest.items():
            assert isinstance(dirs, list)
            assert len(dirs) > 0
            assert all(isinstance(d, str) for d in dirs)

    def test_load_manifest_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            manifest_load_manifest(tmp_path / "nonexistent.yaml")


class TestManifestApplyManifest:
    def test_apply_manifest_single_star_glob(self) -> None:
        manifest = {"src/autoskillit/recipes/*.yaml": ["recipe/"]}
        result = manifest_apply_manifest(["src/autoskillit/recipes/implementation.yaml"], manifest)
        assert result == {"recipe/"}

    def test_apply_manifest_doublestar_glob(self) -> None:
        manifest = {"docs/**/*.md": ["docs/"]}
        # Deep nested path
        result = manifest_apply_manifest(["docs/developer/README.md"], manifest)
        assert result == {"docs/"}
        # Zero intermediate segments
        result = manifest_apply_manifest(["docs/README.md"], manifest)
        assert result == {"docs/"}

    def test_apply_manifest_no_match_returns_none(self) -> None:
        manifest = {"src/autoskillit/recipes/*.yaml": ["recipe/"]}
        result = manifest_apply_manifest(["some/unknown/file.txt"], manifest)
        assert result is None

    def test_apply_manifest_multiple_files_union(self) -> None:
        manifest = {
            "src/autoskillit/recipes/*.yaml": ["recipe/"],
            "docs/**/*.md": ["docs/"],
        }
        result = manifest_apply_manifest(
            ["src/autoskillit/recipes/cook.yaml", "docs/guide.md"], manifest
        )
        assert result == {"recipe/", "docs/"}


# ---------------------------------------------------------------------------
# Conftest filter plugin – pytester integration tests (P1–P8)
# ---------------------------------------------------------------------------

_CONFTEST_HOOKS_SOURCE = """
import os
import warnings
import pytest
from pathlib import Path

_scope_key = pytest.StashKey[set | None]()

def pytest_addoption(parser):
    parser.addoption("--filter-mode", default=None,
                     choices=("none", "conservative", "aggressive"))
    parser.addoption("--filter-base-ref", default=None)

def pytest_configure(config):
    config.stash[_scope_key] = None
    cli_mode = config.getoption("--filter-mode", default=None)
    env_val = os.environ.get("AUTOSKILLIT_TEST_FILTER", "")
    if not cli_mode and not env_val:
        return
    if not cli_mode and env_val.lower() in ("0", "false", "no"):
        return
    try:
        mode = cli_mode or ("conservative" if env_val.lower() in ("1", "true", "yes") else env_val)
        if mode == "none":
            return
        # Stub scope: only include files under subdir_a/
        config.stash[_scope_key] = {config.rootpath / "subdir_a"}
    except Exception as exc:
        warnings.warn(f"Test filter setup failed: {exc}", stacklevel=1)

def pytest_collection_modifyitems(items, config):
    scope = config.stash.get(_scope_key, None)
    if scope is None:
        return
    try:
        selected, deselected = [], []
        for item in items:
            matched = any(
                item.path == sp if sp.is_file() else _is_under(item.path, sp)
                for sp in scope
            )
            (selected if matched else deselected).append(item)
        if deselected:
            config.hook.pytest_deselected(items=deselected)
            items[:] = selected
            warnings.warn(
                f"Test filter: {len(selected)} selected, {len(deselected)} deselected",
                stacklevel=1,
            )
    except Exception as exc:
        warnings.warn(f"Test filter deselection failed: {exc}", stacklevel=1)

def _is_under(path, parent):
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False
"""

_CONFTEST_ERROR_CONFIGURE_SOURCE = """
import os
import warnings
import pytest

_scope_key = pytest.StashKey[set | None]()

def pytest_addoption(parser):
    parser.addoption("--filter-mode", default=None)
    parser.addoption("--filter-base-ref", default=None)

def pytest_configure(config):
    config.stash[_scope_key] = None
    env_val = os.environ.get("AUTOSKILLIT_TEST_FILTER", "")
    if not env_val:
        return
    try:
        raise RuntimeError("simulated configure failure")
    except Exception as exc:
        warnings.warn(f"Test filter setup failed: {exc}", stacklevel=1)
"""

_CONFTEST_ERROR_MODIFYITEMS_SOURCE = """
import os
import warnings
import pytest

_scope_key = pytest.StashKey[set | None]()

def pytest_addoption(parser):
    parser.addoption("--filter-mode", default=None)
    parser.addoption("--filter-base-ref", default=None)

def pytest_configure(config):
    config.stash[_scope_key] = None
    env_val = os.environ.get("AUTOSKILLIT_TEST_FILTER", "")
    if env_val:
        config.stash[_scope_key] = {"will_cause_error"}

def pytest_collection_modifyitems(items, config):
    scope = config.stash.get(_scope_key, None)
    if scope is None:
        return
    try:
        raise RuntimeError("simulated modifyitems failure")
    except Exception as exc:
        warnings.warn(f"Test filter deselection failed: {exc}", stacklevel=1)
"""


class TestConftestFilterPlugin:
    """pytester-based integration tests for conftest filter hook wiring."""

    def test_filter_inactive_by_default(self, pytester: pytest.Pytester) -> None:
        pytester.makeconftest(_CONFTEST_HOOKS_SOURCE)
        pytester.makepyfile(test_a="def test_one(): pass", test_b="def test_two(): pass")
        result = pytester.runpytest("-v")
        result.assert_outcomes(passed=2)

    def test_filter_activates_with_env_var(
        self, pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("AUTOSKILLIT_TEST_FILTER", "1")
        pytester.makeconftest(_CONFTEST_HOOKS_SOURCE)
        pytester.mkdir("subdir_a")
        pytester.makepyfile(**{"subdir_a/test_a": "def test_one(): pass"})
        pytester.makepyfile(test_b="def test_two(): pass")
        result = pytester.runpytest("-v")
        result.assert_outcomes(passed=1, deselected=1)

    def test_deselection_reports_correctly(
        self, pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("AUTOSKILLIT_TEST_FILTER", "1")
        pytester.makeconftest(_CONFTEST_HOOKS_SOURCE)
        pytester.makepyfile(test_keep="def test_keep(): pass")
        pytester.makepyfile(test_drop="def test_drop(): pass")
        result = pytester.runpytest("-v")
        # Both are at root level, not under subdir_a — both deselected
        result.assert_outcomes(deselected=2)

    def test_fail_open_on_configure_error(
        self, pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("AUTOSKILLIT_TEST_FILTER", "1")
        pytester.makeconftest(_CONFTEST_ERROR_CONFIGURE_SOURCE)
        pytester.makepyfile(test_a="def test_one(): pass")
        result = pytester.runpytest("-v")
        result.assert_outcomes(passed=1)

    def test_fail_open_on_modifyitems_error(
        self, pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("AUTOSKILLIT_TEST_FILTER", "1")
        pytester.makeconftest(_CONFTEST_ERROR_MODIFYITEMS_SOURCE)
        pytester.makepyfile(test_a="def test_one(): pass")
        result = pytester.runpytest("-v")
        result.assert_outcomes(passed=1)

    def test_filter_mode_cli_flag(self, pytester: pytest.Pytester) -> None:
        pytester.makeconftest(_CONFTEST_HOOKS_SOURCE)
        pytester.makepyfile(test_a="def test_one(): pass")
        result = pytester.runpytest("--filter-mode=none", "-v")
        result.assert_outcomes(passed=1)

    def test_filter_base_ref_cli_flag(self, pytester: pytest.Pytester) -> None:
        pytester.makeconftest(_CONFTEST_HOOKS_SOURCE)
        pytester.makepyfile(test_a="def test_one(): pass")
        result = pytester.runpytest("--filter-base-ref=main", "-v")
        result.assert_outcomes(passed=1)

    def test_summary_warning_emitted(
        self, pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("AUTOSKILLIT_TEST_FILTER", "1")
        pytester.makeconftest(_CONFTEST_HOOKS_SOURCE)
        pytester.mkdir("subdir_a")
        pytester.makepyfile(**{"subdir_a/test_keep": "def test_keep(): pass"})
        pytester.makepyfile(test_drop="def test_drop(): pass")
        result = pytester.runpytest("-v", "-W", "always")
        result.stdout.fnmatch_lines(["*Test filter:*selected*deselected*"])


# ---------------------------------------------------------------------------
# Canary Test Pattern (development-time technique)
# ---------------------------------------------------------------------------
#
# Canary tests verify the filter itself by being intentionally placed to
# trigger specific filter behavior. They are gated behind a pytest marker:
#
#     @pytest.mark.canary
#     def test_canary_core_change(self):
#         """Touch a core/ file and verify conservative cascade includes all layers."""
#         ...
#
# Canary tests are excluded from default runs via ``-m 'not canary'`` in the
# Taskfile. They only run when explicitly invoked during filter development:
#
#     .venv/bin/pytest tests/test_test_filter.py -m canary
#
# This is a development-time technique, not a permanent test pattern.
# Canary tests should be removed once filter validation is complete.
# The ``canary`` marker must be registered in pyproject.toml [tool.pytest.ini_options].
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Shadow-diff verification tests (SD1)
# ---------------------------------------------------------------------------


class TestShadowDiff:
    """Shadow-diff verification tests (SD1)."""

    _COMM_ENV = {"LC_ALL": "C"}

    def test_shadow_diff_detects_missed_tests(self, tmp_path: Path) -> None:
        """Validate comm -23 logic: IDs in full but not in filtered are 'missed'."""
        full_ids = sorted(
            [
                "tests/core/test_core.py::test_a",
                "tests/core/test_core.py::test_b",
                "tests/execution/test_headless.py::test_c",
                "tests/pipeline/test_gate.py::test_d",
                "tests/server/test_init.py::test_e",
            ]
        )
        filtered_ids = sorted(
            [
                "tests/core/test_core.py::test_a",
                "tests/core/test_core.py::test_b",
                "tests/server/test_init.py::test_e",
            ]
        )

        full_file = tmp_path / "full_selected.txt"
        filtered_file = tmp_path / "filter_selected.txt"
        missed_file = tmp_path / "missed_tests.txt"

        full_file.write_text("\n".join(full_ids) + "\n")
        filtered_file.write_text("\n".join(filtered_ids) + "\n")

        result = subprocess.run(
            ["comm", "-23", str(full_file), str(filtered_file)],
            capture_output=True,
            text=True,
            env=self._COMM_ENV,
        )
        missed_file.write_text(result.stdout)

        missed = [line for line in result.stdout.strip().splitlines() if line]
        assert missed == [
            "tests/execution/test_headless.py::test_c",
            "tests/pipeline/test_gate.py::test_d",
        ]

    def test_shadow_diff_no_missed_tests(self, tmp_path: Path) -> None:
        """When filtered is a superset of full, no missed tests."""
        ids = sorted(
            [
                "tests/core/test_core.py::test_a",
                "tests/core/test_core.py::test_b",
            ]
        )

        full_file = tmp_path / "full_selected.txt"
        filtered_file = tmp_path / "filter_selected.txt"

        full_file.write_text("\n".join(ids) + "\n")
        filtered_file.write_text("\n".join(ids) + "\n")

        result = subprocess.run(
            ["comm", "-23", str(full_file), str(filtered_file)],
            capture_output=True,
            text=True,
            env=self._COMM_ENV,
        )
        missed = [line for line in result.stdout.strip().splitlines() if line]
        assert missed == []

    def test_shadow_diff_empty_filtered(self, tmp_path: Path) -> None:
        """When filter selects nothing, all full IDs are missed."""
        full_ids = sorted(["tests/core/test_core.py::test_a", "tests/core/test_core.py::test_b"])

        full_file = tmp_path / "full_selected.txt"
        filtered_file = tmp_path / "filter_selected.txt"

        full_file.write_text("\n".join(full_ids) + "\n")
        filtered_file.write_text("\n")

        result = subprocess.run(
            ["comm", "-23", str(full_file), str(filtered_file)],
            capture_output=True,
            text=True,
            env=self._COMM_ENV,
        )
        missed = [line for line in result.stdout.strip().splitlines() if line]
        assert missed == full_ids
