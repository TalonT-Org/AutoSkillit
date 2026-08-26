"""Tests for scripts/compare-coverage-ast.py — AST extraction and coverage comparison."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("infra"), pytest.mark.medium]

REPO_ROOT = Path(__file__).parent.parent.parent
_SCRIPT = REPO_ROOT / "scripts" / "compare-coverage-ast.py"


@pytest.fixture(scope="module")
def cov_ast():
    """Import compare-coverage-ast.py as a module without polluting sys.path."""
    spec = importlib.util.spec_from_file_location("compare_coverage_ast", _SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    yield mod
    sys.modules.pop(spec.name, None)


# ── T1a: extract_functions finds all definitions ──


def test_extract_functions_finds_all_definitions(cov_ast, tmp_path: Path):
    """extract_functions() returns FuncInfo for every def/async def."""
    src = tmp_path / "sample.py"
    src.write_text(
        """\
def top_level():
    pass

async def async_top():
    return 1

class Foo:
    def method(self):
        pass
""",
        encoding="utf-8",
    )
    funcs = cov_ast.extract_functions(src)
    names = [f.qualname for f in funcs]
    assert "top_level" in names
    assert "async_top" in names
    assert "Foo.method" in names
    async_func = next(f for f in funcs if f.qualname == "async_top")
    assert async_func.is_async is True


# ── T1b: nested classes ──


def test_extract_functions_handles_nested_classes(cov_ast, tmp_path: Path):
    """Nested class methods get qualified names like 'Outer.Inner.method'."""
    src = tmp_path / "nested.py"
    src.write_text(
        """\
class Outer:
    class Inner:
        def method(self):
            pass
""",
        encoding="utf-8",
    )
    funcs = cov_ast.extract_functions(src)
    qualnames = [f.qualname for f in funcs]
    assert "Outer.Inner.method" in qualnames


# ── T1c: syntax errors ──


def test_extract_functions_skips_syntax_errors(cov_ast, tmp_path: Path, capsys):
    """Files with syntax errors are skipped with a warning, not a crash."""
    src = tmp_path / "bad.py"
    src.write_text("def broken(\n", encoding="utf-8")
    funcs = cov_ast.extract_functions(src)
    assert funcs == []
    captured = capsys.readouterr()
    assert "SyntaxError" in captured.err


# ── T1d: uncovered functions ──


def test_compare_finds_uncovered_functions(cov_ast):
    """Functions whose line ranges have zero intersection with covered lines are reported."""
    func = cov_ast.FuncInfo(
        name="orphan",
        qualname="orphan",
        filepath="mod.py",
        lineno=10,
        end_lineno=20,
        is_async=False,
    )
    ast_map = {"mod.py": [func]}
    coverage_map: dict[str, set[int]] = {}
    report = cov_ast.compare(ast_map, coverage_map)
    assert report.uncovered == 1
    assert report.covered == 0


# ── T1e: covered functions ──


def test_compare_marks_covered_functions(cov_ast):
    """Functions whose line ranges overlap with covered lines are marked as covered."""
    func = cov_ast.FuncInfo(
        name="tested",
        qualname="tested",
        filepath="mod.py",
        lineno=1,
        end_lineno=3,
        is_async=False,
    )
    ast_map = {"mod.py": [func]}
    coverage_map = {"mod.py": {1, 2, 3}}
    report = cov_ast.compare(ast_map, coverage_map)
    assert report.covered == 1
    assert report.uncovered == 0


# ── T1f: partial coverage ──


def test_compare_detects_partial_coverage(cov_ast):
    """Functions where some lines are covered but not all are marked as partially covered."""
    func = cov_ast.FuncInfo(
        name="half",
        qualname="half",
        filepath="mod.py",
        lineno=1,
        end_lineno=10,
        is_async=False,
    )
    ast_map = {"mod.py": [func]}
    coverage_map = {"mod.py": {1, 2, 3}}
    report = cov_ast.compare(ast_map, coverage_map)
    assert report.partial == 1
    assert report.covered == 0
    assert report.uncovered == 0


# ── TestBuildTestSourceMap ──


class TestBuildTestSourceMap:
    @staticmethod
    def _install_context_data(
        cov_ast,
        tmp_path: Path,
        monkeypatch,
        *,
        contexts_by_lineno: object,
        measured_files: list[str] | None = None,
    ) -> str:
        from unittest.mock import MagicMock

        import coverage as coverage_mod

        source_file = str(tmp_path / "src" / "autoskillit" / "core" / "io.py")
        data = MagicMock()
        data.measured_files.return_value = (
            [source_file] if measured_files is None else measured_files
        )
        data.contexts_by_lineno = contexts_by_lineno
        monkeypatch.setattr(coverage_mod, "CoverageData", MagicMock(return_value=data))
        monkeypatch.setattr(cov_ast, "PROJECT_ROOT", tmp_path)
        return source_file

    def test_query_contexts_map_inverts_correctly(self, cov_ast, tmp_path, monkeypatch):
        """query_contexts_map inverts {line: {ctx}} to {src_file: {test_file}}.

        Uses MagicMock to simulate CoverageData.
        Context names use |run suffix.
        """
        from unittest.mock import MagicMock

        import coverage as coverage_mod

        src_file = str(cov_ast.PROJECT_ROOT / "src" / "autoskillit" / "core" / "io.py")

        mock_data = MagicMock()
        mock_data.measured_files.return_value = [src_file]
        mock_data.contexts_by_lineno.return_value = {
            1: ["tests/core/test_io.py::TestIO::test_write|run"],
            2: ["tests/core/test_io.py::TestIO::test_write|run"],
        }
        monkeypatch.setattr(coverage_mod, "CoverageData", MagicMock(return_value=mock_data))

        result = cov_ast.query_contexts_map(tmp_path / ".coverage")
        assert "src/autoskillit/core/io.py" in result
        assert "tests/core/test_io.py" in result["src/autoskillit/core/io.py"]

    def test_setup_and_teardown_contexts_excluded(self, cov_ast, tmp_path, monkeypatch):
        """query_contexts_map excludes |setup and |teardown contexts.

        Only |run phase entries map source files to tests. A source file touched
        only during |setup or |teardown must NOT appear in the result.
        """
        from unittest.mock import MagicMock

        import coverage as coverage_mod

        src_file = str(cov_ast.PROJECT_ROOT / "src" / "autoskillit" / "core" / "io.py")

        mock_data = MagicMock()
        mock_data.measured_files.return_value = [src_file]
        mock_data.contexts_by_lineno.return_value = {
            1: [
                "tests/core/test_io.py::TestIO::test_write|setup",
                "tests/core/test_io.py::TestIO::test_write|teardown",
            ],
        }
        monkeypatch.setattr(coverage_mod, "CoverageData", MagicMock(return_value=mock_data))

        result = cov_ast.query_contexts_map(tmp_path / ".coverage")
        assert "src/autoskillit/core/io.py" not in result

    def test_build_test_source_map_writes_json(self, cov_ast, tmp_path, monkeypatch):
        """build_test_source_map() writes a valid JSON file to the output path."""
        import json
        from unittest.mock import MagicMock

        import coverage as coverage_mod

        src_file = str(cov_ast.PROJECT_ROOT / "src" / "autoskillit" / "core" / "io.py")
        mock_data = MagicMock()
        mock_data.measured_files.return_value = [src_file]
        mock_data.contexts_by_lineno.return_value = {
            1: ["tests/core/test_io.py::TestIO::test_write|run"],
        }
        monkeypatch.setattr(coverage_mod, "CoverageData", MagicMock(return_value=mock_data))

        db_path = tmp_path / ".coverage"
        db_path.touch()
        output_path = tmp_path / "test-source-map.json"
        assert (
            cov_ast.build_test_source_map(
                db_path,
                output_path,
                pytest_exit_code=0,
                source_commit="test-commit",
            )
            == 0
        )

        assert output_path.exists()
        parsed = json.loads(output_path.read_text())
        assert parsed["schema_version"] == 1
        assert parsed["provenance"]["pytest_exit_code"] == 0
        expected_key = "src/autoskillit/core/io.py"
        assert expected_key in parsed["map"]
        assert "tests/core/test_io.py" in parsed["map"][expected_key]

    def test_main_routes_build_test_source_map_mode(self, cov_ast, tmp_path, monkeypatch):
        """main() with --mode build-test-source-map calls build_test_source_map()."""

        called_with: dict = {}
        output_path = tmp_path / "test-source-map.json"

        def fake_build(db_path, output_path, *, pytest_exit_code, source_commit):
            called_with["db_path"] = db_path
            called_with["output_path"] = output_path
            called_with["pytest_exit_code"] = pytest_exit_code
            called_with["source_commit"] = source_commit
            return 0

        monkeypatch.setattr(cov_ast, "build_test_source_map", fake_build)
        monkeypatch.setattr(
            "sys.argv",
            [
                "compare-coverage-ast.py",
                "--mode",
                "build-test-source-map",
                "--pytest-status",
                "0",
                "--source-commit",
                "test-commit",
                "--output",
                str(output_path),
            ],
        )
        result = cov_ast.main()
        assert result == 0
        assert called_with["output_path"] == output_path
        assert called_with["db_path"] == cov_ast.PROJECT_ROOT / ".coverage"
        assert called_with["pytest_exit_code"] == 0
        assert called_with["source_commit"] == "test-commit"

    def test_map_json_values_are_lists(self, cov_ast, tmp_path, monkeypatch):
        """The written JSON has list values (not sets), loadable as JSON."""
        import json
        from unittest.mock import MagicMock

        import coverage as coverage_mod

        src_file = str(cov_ast.PROJECT_ROOT / "src" / "autoskillit" / "core" / "io.py")
        mock_data = MagicMock()
        mock_data.measured_files.return_value = [src_file]
        mock_data.contexts_by_lineno.return_value = {
            1: ["tests/core/test_io.py::TestIO::test_a|run"],
            2: ["tests/core/test_io.py::TestIO::test_b|run"],
        }
        monkeypatch.setattr(coverage_mod, "CoverageData", MagicMock(return_value=mock_data))

        db_path = tmp_path / ".coverage"
        db_path.touch()
        output_path = tmp_path / "test-source-map.json"
        assert (
            cov_ast.build_test_source_map(
                db_path,
                output_path,
                pytest_exit_code=0,
                source_commit="test-commit",
            )
            == 0
        )

        parsed = json.loads(output_path.read_text())
        for v in parsed["map"].values():
            assert isinstance(v, list)

    def test_build_test_source_map_refuses_when_pytest_failed(
        self, cov_ast, tmp_path, monkeypatch
    ):
        """A failed pytest run may write diagnostics but must not replace the oracle."""
        from unittest.mock import MagicMock

        source_file = self._install_context_data(
            cov_ast,
            tmp_path,
            monkeypatch,
            contexts_by_lineno=MagicMock(
                return_value={1: ["tests/core/test_io.py::test_write|run"]}
            ),
        )
        db_path = tmp_path / ".coverage"
        db_path.touch()
        output_path = tmp_path / ".autoskillit" / "test-source-map.json"
        output_path.parent.mkdir()
        output_path.write_bytes(b"canonical sentinel")

        result = cov_ast.build_test_source_map(
            db_path,
            output_path,
            pytest_exit_code=1,
            source_commit="test-commit",
        )

        candidate = tmp_path / ".autoskillit" / "temp" / "test-source-map-candidate.json"
        assert result != 0
        assert output_path.read_bytes() == b"canonical sentinel"
        assert json.loads(candidate.read_text())[str(Path(source_file).relative_to(tmp_path))] == [
            "tests/core/test_io.py"
        ]

    @pytest.mark.parametrize("error_kind", ["coverage", "os"])
    def test_build_test_source_map_refuses_on_coverage_read_failure(
        self, cov_ast, tmp_path, monkeypatch, capsys, error_kind: str
    ):
        """Unreadable coverage evidence is a typed builder failure, never an empty map."""
        from unittest.mock import MagicMock

        import coverage as coverage_mod

        error_type = coverage_mod.CoverageException if error_kind == "coverage" else OSError
        data = MagicMock()
        data.read.side_effect = error_type("unreadable")
        monkeypatch.setattr(coverage_mod, "CoverageData", MagicMock(return_value=data))
        monkeypatch.setattr(cov_ast, "PROJECT_ROOT", tmp_path)

        db_path = tmp_path / ".coverage"
        db_path.touch()
        output_path = tmp_path / ".autoskillit" / "test-source-map.json"
        output_path.parent.mkdir()
        output_path.write_bytes(b"canonical sentinel")

        with pytest.raises(cov_ast.CoverageReadError):
            cov_ast.build_test_source_map(
                db_path,
                output_path,
                pytest_exit_code=0,
                source_commit="test-commit",
            )

        assert output_path.read_bytes() == b"canonical sentinel"
        assert not (tmp_path / ".autoskillit" / "temp" / "test-source-map-candidate.json").exists()

        monkeypatch.setattr(
            "sys.argv",
            [
                "compare-coverage-ast.py",
                "--mode",
                "build-test-source-map",
                "--coverage-db",
                str(db_path),
                "--output",
                str(output_path),
                "--pytest-status",
                "0",
                "--source-commit",
                "test-commit",
            ],
        )
        assert cov_ast.main() != 0
        assert "coverage" in capsys.readouterr().err.lower()

    def test_build_test_source_map_refuses_on_per_file_context_error(
        self, cov_ast, tmp_path, monkeypatch, capsys
    ):
        """A context lookup failure aborts publication rather than producing partial evidence."""
        from unittest.mock import MagicMock

        self._install_context_data(
            cov_ast,
            tmp_path,
            monkeypatch,
            contexts_by_lineno=MagicMock(side_effect=OSError("context failure")),
        )
        db_path = tmp_path / ".coverage"
        db_path.touch()
        output_path = tmp_path / ".autoskillit" / "test-source-map.json"
        output_path.parent.mkdir()
        output_path.write_bytes(b"canonical sentinel")

        with pytest.raises(cov_ast.CoverageReadError):
            cov_ast.build_test_source_map(
                db_path,
                output_path,
                pytest_exit_code=0,
                source_commit="test-commit",
            )

        assert output_path.read_bytes() == b"canonical sentinel"
        assert not (tmp_path / ".autoskillit" / "temp" / "test-source-map-candidate.json").exists()

        monkeypatch.setattr(
            "sys.argv",
            [
                "compare-coverage-ast.py",
                "--mode",
                "build-test-source-map",
                "--coverage-db",
                str(db_path),
                "--output",
                str(output_path),
                "--pytest-status",
                "0",
                "--source-commit",
                "test-commit",
            ],
        )
        assert cov_ast.main() != 0
        assert "coverage" in capsys.readouterr().err.lower()

    def test_build_test_source_map_refuses_to_publish_empty_map(
        self, cov_ast, tmp_path, monkeypatch
    ):
        """An empty source map is diagnostic output, never a canonical oracle."""
        from unittest.mock import MagicMock

        self._install_context_data(
            cov_ast,
            tmp_path,
            monkeypatch,
            contexts_by_lineno=MagicMock(return_value={}),
            measured_files=[],
        )
        db_path = tmp_path / ".coverage"
        db_path.touch()
        output_path = tmp_path / ".autoskillit" / "test-source-map.json"
        output_path.parent.mkdir()
        output_path.write_bytes(b"canonical sentinel")

        result = cov_ast.build_test_source_map(
            db_path,
            output_path,
            pytest_exit_code=0,
            source_commit="test-commit",
        )

        candidate = tmp_path / ".autoskillit" / "temp" / "test-source-map-candidate.json"
        assert result != 0
        assert output_path.read_bytes() == b"canonical sentinel"
        assert json.loads(candidate.read_text()) == {}

    def test_build_test_source_map_requires_pytest_status(
        self, cov_ast, tmp_path, monkeypatch, capsys
    ):
        """Build mode rejects a request that lacks the test-run status evidence."""
        db_path = tmp_path / ".coverage"
        db_path.touch()
        monkeypatch.setattr(
            "sys.argv",
            [
                "compare-coverage-ast.py",
                "--mode",
                "build-test-source-map",
                "--coverage-db",
                str(db_path),
            ],
        )

        with pytest.raises(SystemExit) as exc_info:
            cov_ast.main()

        assert exc_info.value.code != 0
        assert "--pytest-status" in capsys.readouterr().err

    def test_published_map_carries_provenance_envelope(self, cov_ast, tmp_path, monkeypatch):
        """Successful publication records the evidence that authorizes step-7 narrowing."""
        from unittest.mock import MagicMock

        self._install_context_data(
            cov_ast,
            tmp_path,
            monkeypatch,
            contexts_by_lineno=MagicMock(
                return_value={1: ["tests/core/test_io.py::test_write|run"]}
            ),
        )
        db_path = tmp_path / ".coverage"
        db_path.touch()
        output_path = tmp_path / ".autoskillit" / "test-source-map.json"

        assert (
            cov_ast.build_test_source_map(
                db_path,
                output_path,
                pytest_exit_code=0,
                source_commit="test-commit",
            )
            == 0
        )

        published = json.loads(output_path.read_text())
        assert published["schema_version"] == 1
        provenance = published["provenance"]
        assert set(provenance) == {
            "generated_at",
            "source_commit",
            "pytest_exit_code",
            "tool_version",
            "source_file_count",
        }
        assert provenance["source_commit"] == "test-commit"
        assert provenance["pytest_exit_code"] == 0
        assert provenance["source_file_count"] == 1
        assert isinstance(provenance["generated_at"], str)
        assert isinstance(provenance["tool_version"], str)
        assert published["map"] == {"src/autoskillit/core/io.py": ["tests/core/test_io.py"]}

    def test_publication_is_atomic(self, cov_ast, tmp_path, monkeypatch):
        """A failed replace leaves the previous canonical oracle intact."""
        from unittest.mock import MagicMock

        self._install_context_data(
            cov_ast,
            tmp_path,
            monkeypatch,
            contexts_by_lineno=MagicMock(
                return_value={1: ["tests/core/test_io.py::test_write|run"]}
            ),
        )
        db_path = tmp_path / ".coverage"
        db_path.touch()
        output_path = tmp_path / ".autoskillit" / "test-source-map.json"
        output_path.parent.mkdir()
        output_path.write_bytes(b"canonical sentinel")

        def replace_fails(*_args, **_kwargs) -> None:
            raise OSError("replace failure")

        monkeypatch.setattr("autoskillit.core.io.os.replace", replace_fails)
        with pytest.raises(OSError, match="replace failure"):
            cov_ast.build_test_source_map(
                db_path,
                output_path,
                pytest_exit_code=0,
                source_commit="test-commit",
            )
        assert output_path.read_bytes() == b"canonical sentinel"

    def test_taskfile_coverage_audit_invokes_map_mode(self):
        """Taskfile.yml coverage-audit task includes --mode build-test-source-map."""
        taskfile = Path(__file__).parent.parent.parent / "Taskfile.yml"
        content = taskfile.read_text()
        assert "build-test-source-map" in content


def test_audit_mode_returns_zero_on_coverage_read_failure(cov_ast, tmp_path, monkeypatch, capsys):
    """Audit reports unknown coverage without becoming a task gate."""
    coverage_read_error = getattr(cov_ast, "CoverageReadError", RuntimeError)

    def read_failure(_db_path: Path):
        raise coverage_read_error("unreadable")

    src_root = tmp_path / "src"
    src_root.mkdir()
    db_path = tmp_path / ".coverage"
    db_path.touch()
    monkeypatch.setattr(cov_ast, "query_coverage_db", read_failure)
    monkeypatch.setattr(
        "sys.argv",
        [
            "compare-coverage-ast.py",
            "--coverage-db",
            str(db_path),
            "--src-root",
            str(src_root),
        ],
    )

    assert cov_ast.main() == 0
    assert "coverage status unknown" in capsys.readouterr().err.lower()


def test_audit_mode_does_not_require_pytest_status(cov_ast, tmp_path, monkeypatch):
    """The audit invocation remains valid without build-mode-only evidence arguments."""
    src_root = tmp_path / "src"
    src_root.mkdir()
    db_path = tmp_path / ".coverage"
    db_path.touch()
    monkeypatch.setattr(cov_ast, "build_ast_map", lambda _src_root: {})
    monkeypatch.setattr(cov_ast, "query_coverage_db", lambda _db_path: ({}, {}))
    monkeypatch.setattr(
        "sys.argv",
        [
            "compare-coverage-ast.py",
            "--coverage-db",
            str(db_path),
            "--src-root",
            str(src_root),
        ],
    )

    assert cov_ast.main() == 0


@pytest.mark.small
def test_test_source_map_is_committed():
    """Sentinel: .autoskillit/test-source-map.json must be committed to version control.

    Fails if the file is missing — run 'task coverage-audit' and commit the output.
    """
    import json

    map_path = Path(__file__).parent.parent.parent / ".autoskillit" / "test-source-map.json"
    assert map_path.exists(), (
        ".autoskillit/test-source-map.json is missing. "
        "Run 'task coverage-audit' and commit the output to activate the coverage oracle."
    )
    data = json.loads(map_path.read_text(encoding="utf-8"))
    assert data["schema_version"] == 1
    assert data["provenance"]["pytest_exit_code"] == 0
    assert isinstance(data["map"], dict)


@pytest.mark.small
def test_load_coverage_map_reads_committed_file():
    """load_coverage_map() returns a populated dict from the committed oracle file.

    Verifies that the file is also parseable and fresh (not older than 30 days).
    """
    from tests._test_filter import load_coverage_map

    map_path = Path(__file__).parent.parent.parent / ".autoskillit" / "test-source-map.json"
    assert map_path.exists(), (
        ".autoskillit/test-source-map.json is missing. "
        "Run 'task coverage-audit' and commit the output to activate the coverage oracle."
    )
    data = json.loads(map_path.read_text(encoding="utf-8"))
    assert data["schema_version"] == 1
    assert data["provenance"]["pytest_exit_code"] == 0
    assert isinstance(data["map"], dict)
    result = load_coverage_map(map_path)
    assert result is not None, (
        "load_coverage_map() returned None — oracle file is older than 30 days or malformed. "
        "Run 'task coverage-audit' and commit the result."
    )
    assert len(result) >= 50, f"Oracle returned only {len(result)} entries"
