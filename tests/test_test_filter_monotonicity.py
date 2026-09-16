"""Property test: the coverage oracle may only add to the structurally-selected scope.

The resolved *file* set with the oracle must always be a superset of the resolved
file set without it, for arbitrary coverage-map content. This is differential — it
compares oracle-on against oracle-off — so it is blind to a narrowing that would
affect both runs equally; the AST guard in tests/arch/test_scope_monotonicity_guard.py
covers that remaining blind spot.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
from hypothesis import example, given, settings
from hypothesis import strategies as st

from tests import _test_filter as test_filter
from tests._test_filter import FilterMode, FullRunReason, build_test_scope

pytestmark = [pytest.mark.medium]

_SOURCE_FILE_POOL: tuple[str, ...] = (
    "src/autoskillit/core/io.py",
    "src/autoskillit/execution/headless.py",
    "src/autoskillit/hooks/guards/ingredient_lock_guard.py",
    "src/autoskillit/server/_factory_helpers.py",
    "src/autoskillit/_test_filter.py",
)

_TEST_FILE_POOL: tuple[str, ...] = (
    "tests/core/test_io.py",
    "tests/execution/test_headless.py",
    "tests/hooks/test_hook_registry.py",
    "tests/hooks/test_ingredient_lock_guard.py",
    "tests/server/test_factory.py",
    "tests/arch/test_hook_flock_nonblocking.py",
    "tests/nonexistent/test_ghost.py",
    "tests/unrelated_pkg/test_other.py",
)

# Every directory that classification, always-run tiers, or reexport cascades could
# select for _SOURCE_FILE_POOL's entries, in either filter mode.
_ALL_TEST_DIRS: tuple[str, ...] = (
    "core",
    "execution",
    "hooks",
    "server",
    "config",
    "pipeline",
    "workspace",
    "recipe",
    "migration",
    "fleet",
    "cli",
    "planner",
    "smoke_utils",
    "report",
    "exploration",
    "arch",
    "contracts",
    "infra",
    "docs",
)


def _make_tests_root(case_dir: Path) -> Path:
    tests_root = case_dir / "tests"
    for name in _ALL_TEST_DIRS:
        directory = tests_root / name
        directory.mkdir(parents=True, exist_ok=True)
        (directory / f"test_{name}_dummy.py").write_text("")
    for filename in test_filter._INFRA_UNCONDITIONAL_FILES:
        (tests_root / "infra" / filename).write_text("")
    for filename in test_filter._HOOKS_UNCONDITIONAL_FILES:
        (tests_root / "hooks" / filename).write_text("")
    return tests_root


def expand_to_files(paths: set[Path]) -> set[Path]:
    """Expand directory scope entries to concrete files, matching real collection.

    ``pytest_collection_modifyitems`` matches a directory entry against descendants
    at any depth (``tests/conftest.py``), not just immediate children, so this must
    use a recursive glob rather than a shallow one.
    """
    expanded: set[Path] = set()
    for path in paths:
        if path.is_dir():
            expanded.update(path.rglob("test_*.py"))
        else:
            expanded.add(path)
    return expanded


def _write_coverage_map(path: Path, source_map: dict[str, list[str]], source_commit: str) -> None:
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "provenance": {"pytest_exit_code": 0, "source_commit": source_commit},
                "map": source_map,
            }
        ),
        encoding="utf-8",
    )


def _init_git_repo(case_dir: Path) -> str:
    """Initialize case_dir as a real git repo with one commit; return HEAD's SHA.

    The monotonicity property test must exercise the same ``load_coverage_map``
    lineage path the consumer uses, otherwise the test passes vacuously whenever
    the lineage check ever consumes git output. Initializing case_dir as a real
    git repo makes the additive-only assertion rest on the real code path, not
    on a stub of subprocess.
    """
    env = {
        "GIT_AUTHOR_NAME": "Test",
        "GIT_AUTHOR_EMAIL": "test@example.com",
        "GIT_COMMITTER_NAME": "Test",
        "GIT_COMMITTER_EMAIL": "test@example.com",
        "PATH": os.environ.get("PATH", ""),
    }
    subprocess.run(
        ["git", "init", "--initial-branch=main"],
        cwd=str(case_dir),
        check=True,
        capture_output=True,
        env=env,
        timeout=30,
    )
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", "initial"],
        cwd=str(case_dir),
        check=True,
        capture_output=True,
        env=env,
        timeout=30,
    )
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=str(case_dir),
        check=True,
        capture_output=True,
        text=True,
        env=env,
        timeout=10,
    )
    return result.stdout.strip()


_CHANGED_FILES = st.sets(st.sampled_from(_SOURCE_FILE_POOL), min_size=1, max_size=3)
_MODE = st.sampled_from([FilterMode.CONSERVATIVE, FilterMode.AGGRESSIVE])
_COVERAGE_MAP = st.dictionaries(
    keys=st.sampled_from(_SOURCE_FILE_POOL),
    values=st.lists(st.sampled_from(_TEST_FILE_POOL), max_size=4, unique=True),
    max_size=len(_SOURCE_FILE_POOL),
)


@settings(max_examples=500, deadline=None)
@example(
    changed_files={"src/autoskillit/_test_filter.py"},
    mode=FilterMode.CONSERVATIVE,
    coverage_map={
        "src/autoskillit/_test_filter.py": ["tests/arch/test_hook_flock_nonblocking.py"]
    },
)
@given(changed_files=_CHANGED_FILES, mode=_MODE, coverage_map=_COVERAGE_MAP)
def test_oracle_augmentation_is_monotonic(
    tmp_path_factory: pytest.TempPathFactory,
    changed_files: set[str],
    mode: FilterMode,
    coverage_map: dict[str, list[str]],
) -> None:
    """The oracle-augmented file scope must always be a superset of the unaugmented scope."""
    case_dir = tmp_path_factory.mktemp("monotonicity-case")
    tests_root = _make_tests_root(case_dir)
    source_commit = _init_git_repo(case_dir)
    map_file = case_dir / "test-source-map.json"
    _write_coverage_map(map_file, coverage_map, source_commit)

    without_oracle = build_test_scope(
        changed_files=set(changed_files),
        mode=mode,
        tests_root=tests_root,
        coverage_map_path=None,
        cwd=case_dir,
    )
    with_oracle = build_test_scope(
        changed_files=set(changed_files),
        mode=mode,
        tests_root=tests_root,
        coverage_map_path=map_file,
        cwd=case_dir,
    )

    if isinstance(without_oracle, FullRunReason):
        assert with_oracle == without_oracle
        return
    assert not isinstance(with_oracle, FullRunReason)

    assert expand_to_files(without_oracle) <= expand_to_files(with_oracle)
