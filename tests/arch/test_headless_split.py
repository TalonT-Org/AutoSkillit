"""Structural guards for the test_headless.py split (P1-F01 audit fix)."""

import ast
from pathlib import Path

import pytest

EXECUTION_DIR = Path(__file__).parent.parent / "execution"


def test_split_files_exist():
    for name in [
        "test_headless_core.py",
        "test_headless_synthesis.py",
        "test_headless_path_validation.py",
        "test_headless_dispatch.py",
    ]:
        assert (EXECUTION_DIR / name).exists(), f"Missing: {name}"


def test_original_file_deleted():
    assert not (EXECUTION_DIR / "test_headless.py").exists()


def _get_pytestmark(path: Path) -> str:
    src = path.read_text()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == "pytestmark":
                    return ast.unparse(node.value)
    return ""


@pytest.mark.parametrize(
    "name",
    [
        "test_headless_core.py",
        "test_headless_synthesis.py",
        "test_headless_path_validation.py",
        "test_headless_dispatch.py",
    ],
)
def test_pytestmark_preserved(name):
    src = _get_pytestmark(EXECUTION_DIR / name)
    assert "execution" in src
    assert "small" in src


def test_cross_validation_uses_parametrize():
    src = (EXECUTION_DIR / "test_headless_core.py").read_text()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "TestBuildSkillResultCrossValidation":
            method_names = [n.name for n in node.body if isinstance(n, ast.FunctionDef)]
            assert "test_stale_schema" not in method_names
            assert "test_timeout_schema" not in method_names
            assert "test_normal_success_schema" not in method_names
            assert "test_empty_stdout_schema" not in method_names
            assert "test_schema_keys" in method_names


def test_make_session_renamed_in_path_validation():
    src = (EXECUTION_DIR / "test_headless_path_validation.py").read_text()
    assert "def make_session(" not in src
    assert "def make_headless_session(" in src


def test_make_session_renamed_in_adjudication():
    # test_session_adjudication.py was split into three files; check all of them
    for fname in (
        "test_session_adjudication_success.py",
        "test_session_adjudication_retry.py",
        "test_session_adjudication_outcome.py",
    ):
        adj = (EXECUTION_DIR / fname).read_text()
        assert "make_headless_session" not in adj, f"{fname} must not use make_headless_session"


def test_make_success_stdout_not_duplicated():
    src = (EXECUTION_DIR / "test_headless_dispatch.py").read_text()
    assert src.count("def _make_success_stdout") == 1
    lines = src.splitlines()
    defn_lines = [line for line in lines if "def _make_success_stdout" in line]
    assert len(defn_lines) == 1
    assert not defn_lines[0].startswith(" ")  # module-level: no leading indent


def test_timestamp_assertion_captures_return_value():
    src = (EXECUTION_DIR / "test_headless_core.py").read_text()
    assert "datetime.fromisoformat(record.timestamp)  # must parse as ISO" not in src
    assert "assert datetime.fromisoformat(record.timestamp)" in src
