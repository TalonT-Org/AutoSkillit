# tests/infra/test_ci_shard_config.py
"""Validate shard ownership and routing in the CI test workflow."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests._test_filter import FilterMode, FullRunReason, build_test_scope

pytestmark = [pytest.mark.layer("infra"), pytest.mark.small]

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "tests.yml"

EXPECTED_EXPLICIT_SHARDS: dict[str, tuple[str, ...]] = {
    "EXECUTION": (
        "tests/execution",
        "tests/contracts",
        "tests/core",
        "tests/exploration",
        "tests/planner",
        "tests/pipeline",
        "tests/migration",
        "tests/integration",
    ),
    "RECIPE": (
        "tests/recipe",
        "tests/docs",
        "tests/server",
    ),
}

_SHARD_ASSIGNMENT_RE: re.Pattern[str] = re.compile(r'SHARD_([A-Z][A-Z0-9_]*)_DIRS="([^"]+)"')


def _read_workflow_text() -> str:
    if not WORKFLOW_PATH.exists():
        pytest.fail(f"Workflow file not found: {WORKFLOW_PATH}")
    return WORKFLOW_PATH.read_text(encoding="utf-8")


def _parse_shard_assignments(text: str) -> dict[str, tuple[str, ...]]:
    """Return the workflow's explicit directory assignments by shard name."""
    assignments: dict[str, tuple[str, ...]] = {}
    for match in _SHARD_ASSIGNMENT_RE.finditer(text):
        name = match.group(1)
        dirs = tuple(match.group(2).split())
        if name in assignments:
            raise ValueError(
                f"SHARD_{name}_DIRS declared more than once in workflow; "
                "duplicate definitions are not allowed."
            )
        assignments[name] = dirs
    return assignments


def _assert_expected_assignments_present(
    assignments: dict[str, tuple[str, ...]],
    expected: dict[str, tuple[str, ...]] = EXPECTED_EXPLICIT_SHARDS,
) -> None:
    """Fail the guard if any required explicit shard is missing from ``assignments``."""
    missing = set(expected) - set(assignments)
    if missing:
        pytest.fail(
            "Workflow is missing required explicit shard declarations: "
            f'{sorted(missing)}. Declare each as SHARD_<NAME>_DIRS="..." '
            "in the Compute test paths step."
        )


def _compute_test_paths_body(text: str) -> str:
    """Return the shell body of the ``Compute test paths`` step.

    The step is identified by ``id: test-paths`` and a following ``run: |``
    block; its body extends until the first non-empty line at a smaller indent
    than the body's first non-empty line. Raises ``pytest.fail`` when the step
    is missing or malformed.
    """
    marker = "id: test-paths"
    if marker not in text:
        pytest.fail("Compute test paths step (id: test-paths) not found in workflow")
    step_start = text.index(marker)
    run_marker = "run: |"
    run_start = text.find(run_marker, step_start)
    if run_start == -1:
        pytest.fail("Compute test paths step is missing its `run: |` block")
    body_start = run_start + len(run_marker)
    lines = text[body_start:].splitlines(keepends=True)
    indent: int | None = None
    body_lines: list[str] = []
    for line in lines:
        if not line.strip():
            body_lines.append(line)
            continue
        stripped_indent = len(line) - len(line.lstrip())
        if indent is None:
            indent = stripped_indent
        if stripped_indent < indent:
            break
        body_lines.append(line)
    if indent is None:
        pytest.fail("Compute test paths step has empty run body")
    return "".join(body_lines)


def _parse_case_arms(body: str) -> dict[str, str]:
    """Parse a ``case X in ... esac`` block into arm-name -> body.

    The default ``*`` arm is keyed as ``"*_default"``. Each returned body is
    the trimmed text between the arm's ``)`` and its terminating ``;;``.
    """
    case_match = re.search(r"\bcase\s+(.+?)\s+in\b", body, re.DOTALL)
    if not case_match:
        pytest.fail("`case ... in` statement not found in Compute test paths body")
    esac_match = re.search(r"\besac\b", body[case_match.end() :])
    if not esac_match:
        pytest.fail("`esac` terminator not found in Compute test paths body")
    block = body[case_match.end() : case_match.end() + esac_match.start()]
    arms: dict[str, str] = {}
    for part in re.split(r"\s*;;\s*", block):
        part = part.strip()
        if not part:
            continue
        match = re.match(r"^([\w?*.\-${}]+)\)\s*(.*)$", part, re.DOTALL)
        if not match:
            pytest.fail(f"Malformed case arm in Compute test paths body: {part!r}")
        pattern, arm_body = match.group(1), match.group(2).strip()
        key = "*_default" if pattern == "*" else pattern
        arms[key] = arm_body
    return arms


def _exit_status(body: str) -> int | None:
    match = re.search(r"\bexit\s+(\d+)\b", body)
    return int(match.group(1)) if match else None


def _supported_test_files(tests_root: Path) -> set[str]:
    """Return repo-relative paths of every supported test file under ``tests_root``."""
    return {
        str(p.relative_to(tests_root.parent)) for p in tests_root.rglob("test_*.py") if p.is_file()
    }


def _root_test_files(tests_root: Path) -> set[str]:
    """Return repo-relative paths of root-level ``tests/test_*.py`` files."""
    return {
        str(p.relative_to(tests_root.parent))
        for p in tests_root.iterdir()
        if p.is_file() and p.name.startswith("test_") and p.suffix == ".py"
    }


def _assign_files_to_shards(
    supported_files: set[str],
    explicit_sets: dict[str, tuple[str, ...]],
    root_files: set[str],
) -> dict[str, set[str]]:
    """Assign each supported test file to its lower-case shard name."""
    names = sorted({name.lower() for name in explicit_sets} | {"general"})
    ownership: dict[str, set[str]] = {name: set() for name in names}
    ownership["execution"].update(root_files)
    for f in supported_files:
        if f in root_files:
            continue
        assigned = False
        for shard_name, dirs in explicit_sets.items():
            if any(f == d or f.startswith(d + "/") for d in dirs):
                ownership[shard_name.lower()].add(f)
                assigned = True
                break
        if not assigned:
            ownership["general"].add(f)
    return ownership


def _expand_scope_to_files(scope: set[Path], tests_root: Path) -> set[str]:
    """Expand a filter scope into repo-relative test-file paths."""
    result: set[str] = set()
    for entry in scope:
        if entry.is_dir():
            for child in entry.rglob("test_*.py"):
                if child.is_file():
                    result.add(str(child.relative_to(tests_root.parent)))
        elif entry.is_file():
            result.add(str(entry.relative_to(tests_root.parent)))
    return result


def _intersected_shards(files: set[str], ownership: dict[str, set[str]]) -> set[str]:
    return {shard for shard, shard_files in ownership.items() if files & shard_files}


class TestCIShardConfig:
    def test_explicit_sets_define_execution_and_recipe_with_exact_paths(self) -> None:
        text = _read_workflow_text()
        assignments = _parse_shard_assignments(text)
        _assert_expected_assignments_present(assignments)
        assert set(assignments) == {"EXECUTION", "RECIPE"}
        assert assignments["EXECUTION"] == EXPECTED_EXPLICIT_SHARDS["EXECUTION"]
        assert assignments["RECIPE"] == EXPECTED_EXPLICIT_SHARDS["RECIPE"]

    def test_duplicate_or_missing_explicit_set_fails(self) -> None:
        text_missing = 'SHARD_EXECUTION_DIRS="tests/execution"\n'
        assignments = _parse_shard_assignments(text_missing)
        with pytest.raises(pytest.fail.Exception, match="RECIPE"):
            _assert_expected_assignments_present(assignments)
        text_duplicate = (
            'SHARD_EXECUTION_DIRS="tests/execution"\n'
            'SHARD_RECIPE_DIRS="tests/recipe"\n'
            'SHARD_EXECUTION_DIRS="tests/core"\n'
        )
        with pytest.raises(ValueError, match="EXECUTION"):
            _parse_shard_assignments(text_duplicate)

    def test_compute_paths_rejects_missing_run_block(self) -> None:
        with pytest.raises(pytest.fail.Exception, match="missing its `run: \\|` block"):
            _compute_test_paths_body("- name: Compute test paths\n  id: test-paths\n")

    def test_case_parser_rejects_malformed_arm(self) -> None:
        body = 'case "$SHARD" in\nexecution echo "missing delimiter";;\nesac'
        with pytest.raises(pytest.fail.Exception, match="Malformed case arm"):
            _parse_case_arms(body)

    def test_explicit_directories_exist_and_contain_tests(self) -> None:
        text = _read_workflow_text()
        assignments = _parse_shard_assignments(text)
        for dirs in assignments.values():
            for d in dirs:
                dir_path = REPO_ROOT / d
                assert dir_path.is_dir(), f"Declared shard directory {d} does not exist"
                test_files = list(dir_path.rglob("test_*.py"))
                assert test_files, f"Declared shard directory {d} contains no supported test files"

    def test_explicit_sets_are_disjoint(self) -> None:
        text = _read_workflow_text()
        assignments = _parse_shard_assignments(text)
        names = list(assignments.keys())
        for i in range(len(names)):
            for j in range(i + 1, len(names)):
                a = set(assignments[names[i]])
                b = set(assignments[names[j]])
                assert a.isdisjoint(b), (
                    f"{names[i]} and {names[j]} share directories: {sorted(a & b)}"
                )

    def test_root_files_owned_only_by_execution(self) -> None:
        text = _read_workflow_text()
        assignments = _parse_shard_assignments(text)
        tests_root = REPO_ROOT / "tests"
        root_files = _root_test_files(tests_root)
        ownership = _assign_files_to_shards(
            _supported_test_files(tests_root), assignments, root_files
        )
        for f in root_files:
            assert f in ownership["execution"], f"Root test file {f} not owned by execution"
            for other in ownership:
                if other == "execution":
                    continue
                assert f not in ownership[other], f"Root test file {f} wrongly owned by {other}"

    def test_explicit_directory_files_owned_by_named_shard(self) -> None:
        text = _read_workflow_text()
        assignments = _parse_shard_assignments(text)
        tests_root = REPO_ROOT / "tests"
        ownership = _assign_files_to_shards(
            _supported_test_files(tests_root), assignments, _root_test_files(tests_root)
        )
        for shard_name, dirs in assignments.items():
            key = shard_name.lower()
            for d in dirs:
                for p in (REPO_ROOT / d).rglob("test_*.py"):
                    if not p.is_file():
                        continue
                    f = str(p.relative_to(REPO_ROOT))
                    assert f in ownership[key], (
                        f"File {f} under explicit directory {d} not owned by {key}"
                    )

    def test_three_ownership_sets_disjoint_and_exhaustive(self) -> None:
        text = _read_workflow_text()
        assignments = _parse_shard_assignments(text)
        tests_root = REPO_ROOT / "tests"
        ownership = _assign_files_to_shards(
            _supported_test_files(tests_root), assignments, _root_test_files(tests_root)
        )
        shards = list(ownership.values())
        for i in range(len(shards)):
            for j in range(i + 1, len(shards)):
                assert shards[i].isdisjoint(shards[j]), (
                    f"Ownership sets overlap: {sorted(shards[i] & shards[j])[:3]}"
                )
        union: set[str] = set()
        for s in shards:
            union.update(s)
        assert union == _supported_test_files(tests_root), (
            "Ownership union does not match the supported test-file set"
        )

    def test_unassigned_top_level_dir_defaults_to_general(self, tmp_path: Path) -> None:
        tests_root = tmp_path / "tests"
        tests_root.mkdir()
        # Mirror the production explicit-set membership for execution and recipe.
        for d in ("execution", "contracts", "core", "recipe", "docs", "server"):
            (tests_root / d).mkdir()
            (tests_root / d / f"test_{d}.py").write_text("")
        # Brand-new unassigned top-level directory.
        new_dir = tests_root / "brand_new_unassigned"
        new_dir.mkdir()
        (new_dir / "test_brand_new.py").write_text("")
        # Root-level test file.
        (tests_root / "test_root_one.py").write_text("")

        assignments = {
            "EXECUTION": (
                "tests/execution",
                "tests/contracts",
                "tests/core",
            ),
            "RECIPE": (
                "tests/recipe",
                "tests/docs",
                "tests/server",
            ),
        }
        supported = _supported_test_files(tests_root)
        root_files = _root_test_files(tests_root)
        ownership = _assign_files_to_shards(supported, assignments, root_files)

        assert "tests/test_root_one.py" in ownership["execution"]
        assert "tests/execution/test_execution.py" in ownership["execution"]
        assert "tests/recipe/test_recipe.py" in ownership["recipe"]
        assert "tests/docs/test_docs.py" in ownership["recipe"]
        assert "tests/server/test_server.py" in ownership["recipe"]
        assert "tests/brand_new_unassigned/test_brand_new.py" in ownership["general"]

    def test_case_arms_include_execution_recipe_general_and_default_error(self) -> None:
        text = _read_workflow_text()
        body = _compute_test_paths_body(text)
        arms = _parse_case_arms(body)
        matrix_match = re.search(r"(?m)^\s*shard:\s*\[([^]]+)]\s*$", text)
        assert matrix_match, "Workflow test matrix has no inline shard declaration"
        matrix_shards = {name.strip() for name in matrix_match.group(1).split(",")}
        assert set(arms) == matrix_shards | {"*_default"}
        default_body = arms["*_default"]
        assert "::error::" in default_body
        assert "Unknown test shard" in default_body or "unknown shard" in default_body
        exit_status = _exit_status(default_body)
        assert exit_status is not None and exit_status != 0, (
            f"Default arm does not exit with a nonzero status: {default_body!r}"
        )

    @pytest.mark.parametrize(("body", "expected"), [("exit 10", 10), ("exit 01", 1)])
    def test_exit_status_parses_multidigit_and_zero_padded_values(
        self, body: str, expected: int
    ) -> None:
        assert _exit_status(body) == expected

    def test_case_arm_wiring_matches_ownership_model(self) -> None:
        text = _read_workflow_text()
        body = _compute_test_paths_body(text)
        arms = _parse_case_arms(body)

        assert "ROOT_FILES=$(find tests/ -maxdepth 1 -name 'test_*.py' | sort" in body
        assert "ROOT_IGNORES=$(find tests/ -maxdepth 1 -name 'test_*.py' | sort" in body

        exec_body = arms["execution"]
        assert "SHARD_EXECUTION_DIRS" in exec_body
        assert "${ROOT_FILES}" in exec_body
        assert re.search(
            r'(?m)^\s*echo "PYTEST_TEST_PATHS=\${SHARD_EXECUTION_DIRS} '
            r'\${ROOT_FILES}" >> "\$GITHUB_ENV"\s*$',
            exec_body,
        )
        assert "PYTEST_IGNORE_PATHS" not in exec_body

        recipe_body = arms["recipe"]
        assert "SHARD_RECIPE_DIRS" in recipe_body
        assert re.search(
            r'(?m)^\s*echo "PYTEST_TEST_PATHS=\${SHARD_RECIPE_DIRS}" '
            r'>> "\$GITHUB_ENV"\s*$',
            recipe_body,
        )
        assert "find tests/ -maxdepth 1" not in recipe_body
        assert "ROOT_FILES" not in recipe_body
        assert "ROOT_IGNORES" not in recipe_body

        general_body = arms["general"]
        assert re.search(
            r'(?m)^\s*echo "PYTEST_TEST_PATHS=tests/" >> "\$GITHUB_ENV"\s*$',
            general_body,
        ), f"General arm does not select tests/ root: {general_body!r}"
        assert re.search(
            r'(?m)^\s*echo "PYTEST_IGNORE_PATHS=\${EXEC_IGNORES} '
            r'\${RECIPE_IGNORES} \${ROOT_IGNORES}" >> "\$GITHUB_ENV"\s*$',
            general_body,
        )
        assert "SHARD_EXECUTION_DIRS" in general_body
        assert "SHARD_RECIPE_DIRS" in general_body
        assert "${ROOT_IGNORES}" in general_body


class TestConservativeFilterShardIntersection:
    """Cross-check conservative filter selections against shard ownership."""

    def _workflow_text(self) -> str:
        return _read_workflow_text()

    def _ownership(self) -> dict[str, set[str]]:
        text = self._workflow_text()
        tests_root = REPO_ROOT / "tests"
        assignments = _parse_shard_assignments(text)
        return _assign_files_to_shards(
            _supported_test_files(tests_root), assignments, _root_test_files(tests_root)
        )

    def test_headless_execute_intersects_execution_and_recipe(self, tmp_path: Path) -> None:
        tests_root = tmp_path / "tests"
        (tests_root / "execution").mkdir(parents=True)
        (tests_root / "server").mkdir()
        exec_file = "tests/execution/test_headless_execute.py"
        server_file = "tests/server/test_run_skill_locks.py"
        (tests_root / "execution" / "test_headless_execute.py").write_text("")
        (tests_root / "server" / "test_run_skill_locks.py").write_text("")

        scope = build_test_scope(
            {"src/autoskillit/execution/headless/_headless_execute.py"},
            FilterMode.CONSERVATIVE,
            tests_root=tests_root,
        )
        assert not isinstance(scope, FullRunReason), (
            f"Conservative filter requested a full run: {scope}"
        )
        expanded = _expand_scope_to_files(set(scope), tests_root)
        ownership = self._ownership()

        assert _intersected_shards(expanded, ownership) == {"execution", "recipe"}
        assert exec_file in expanded
        assert server_file in expanded

    def test_recipe_schema_intersects_recipe_execution_and_general(self, tmp_path: Path) -> None:
        tests_root = tmp_path / "tests"
        for d in ("recipe", "server", "execution", "cli"):
            (tests_root / d).mkdir(parents=True)
        placeholders = {
            "recipe": "test_recipe_validation.py",
            "server": "test_tools_load_recipe.py",
            "execution": "test_headless_path_validation.py",
            "cli": "test_cli_prompts.py",
        }
        for d, name in placeholders.items():
            (tests_root / d / name).write_text("")

        scope = build_test_scope(
            {"src/autoskillit/recipe/schema.py"},
            FilterMode.CONSERVATIVE,
            tests_root=tests_root,
        )
        assert not isinstance(scope, FullRunReason), (
            f"Conservative filter requested a full run: {scope}"
        )
        expanded = _expand_scope_to_files(set(scope), tests_root)
        ownership = self._ownership()

        assert _intersected_shards(expanded, ownership) == {
            "execution",
            "general",
            "recipe",
        }

    def test_server_state_intersects_recipe_and_execution(self, tmp_path: Path) -> None:
        tests_root = tmp_path / "tests"
        for d in ("server", "pipeline"):
            (tests_root / d).mkdir(parents=True)
        # test_factory.py lives under tests/server/ in production and exercises
        # the server-side cascade. test_context.py lives under tests/pipeline/
        # in production and exercises the cross-layer pipeline overlay.
        (tests_root / "server" / "test_factory.py").write_text("")
        (tests_root / "pipeline" / "test_context.py").write_text("")

        scope = build_test_scope(
            {"src/autoskillit/server/_state.py"},
            FilterMode.CONSERVATIVE,
            tests_root=tests_root,
        )
        assert not isinstance(scope, FullRunReason), (
            f"Conservative filter requested a full run: {scope}"
        )
        expanded = _expand_scope_to_files(set(scope), tests_root)
        ownership = self._ownership()

        assert _intersected_shards(expanded, ownership) == {"execution", "recipe"}

    def test_unrelated_cli_change_intersects_all_three_shards(self, tmp_path: Path) -> None:
        tests_root = tmp_path / "tests"
        for d in ("contracts", "docs", "infra"):
            (tests_root / d).mkdir(parents=True)
        # A file under contracts (execution shard via always-run).
        (tests_root / "contracts" / "test_discipline_delivery_matrix.py").write_text("")
        # The always-run direct selection for docs.
        (tests_root / "docs" / "test_doc_counts.py").write_text("")
        # An infra unconditional file (general shard).
        (tests_root / "infra" / "test_manifest_completeness.py").write_text("")

        scope = build_test_scope(
            {"src/autoskillit/cli/app.py"},
            FilterMode.CONSERVATIVE,
            tests_root=tests_root,
        )
        assert not isinstance(scope, FullRunReason), (
            f"Conservative filter requested a full run: {scope}"
        )
        expanded = _expand_scope_to_files(set(scope), tests_root)
        ownership = self._ownership()

        assert _intersected_shards(expanded, ownership) == {
            "execution",
            "general",
            "recipe",
        }
