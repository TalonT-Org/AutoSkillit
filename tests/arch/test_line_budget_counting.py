"""Tests for count_budget_lines, REQ-CNST-010's non-import line measurement (#4965)."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tests.arch._helpers import _collect_line_limit_violations
from tests.arch._line_budget import count_budget_lines

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

REPO_ROOT = Path(__file__).resolve().parents[2]


def _count(tmp_path: Path, source: str) -> int:
    path = tmp_path / "m.py"
    path.write_text(source, encoding="utf-8")
    return count_budget_lines(path)


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        pytest.param("x = 1\ny = 2\n", 2, id="plain"),
        pytest.param("import os\nimport sys\nx = 1\n", 1, id="single_line_imports"),
        pytest.param("from __future__ import annotations\nx: int = 1\n", 1, id="future_import"),
        pytest.param(
            "import importlib\nmodule = importlib.import_module('os')\n"
            "other = __import__('sys')\n",
            2,
            id="dynamic_import_calls",
        ),
        pytest.param(
            "from a import (\n    b,\n    c,  # note\n)\nx = 1\n", 1, id="parenthesised_block"
        ),
        pytest.param(
            "from a import (\n    b,\n\n    # note\n    c,\n)\nx = 1\n",
            1,
            id="import_internal_blank_and_comment",
        ),
        pytest.param("import os; import sys\nx = 1\n", 1, id="overlapping_import_spans"),
        pytest.param("from a import b, \\\n    c\nx = 1\n", 1, id="backslash_continuation"),
        pytest.param(
            "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n"
            "    from a import b\n    from c import d\nx = 1\n",
            2,
            id="type_checking_block",
        ),
        pytest.param(
            "try:\n    import fast\nexcept ImportError:\n    fast = None\n", 3, id="try_import"
        ),
        pytest.param("def f():\n    import os\n    return os\n", 2, id="function_local"),
        pytest.param('"""import os\nfrom x import y\n"""\nx = 1\n', 4, id="docstring_lookalike"),
        pytest.param("# import os\n\nx = 1\n", 3, id="comment_and_blank"),
        pytest.param("", 0, id="empty"),
        pytest.param("x = 1", 1, id="unterminated_code"),
        pytest.param("import os", 0, id="unterminated_import"),
        pytest.param("import a\nimport b\n", 0, id="only_imports"),
        pytest.param("import os\r\nx = 1\r\n", 1, id="crlf"),
        pytest.param("import os\f\nx = 1\n", 1, id="form_feed"),
    ],
)
def test_count_budget_lines(tmp_path: Path, source: str, expected: int) -> None:
    assert _count(tmp_path, source) == expected


def test_unparseable_source_raises_syntax_error(tmp_path: Path) -> None:
    path = tmp_path / "m.py"
    path.write_text("def (:\n", encoding="utf-8")

    with pytest.raises(SyntaxError) as exc_info:
        count_budget_lines(path)

    assert exc_info.value.lineno == 1
    assert exc_info.value.filename == str(path)


def test_collect_line_limit_violations_measures_non_import_lines(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    src_root = tmp_path / "src"
    src_root.mkdir()
    monkeypatch.setattr("tests.arch._helpers.SRC_ROOT", src_root)
    (src_root / "a.py").write_text("import os\n" * 50 + "v = 1\n" * 1000, encoding="utf-8")
    (src_root / "b.py").write_text("v = 1\n" * 1001, encoding="utf-8")

    violations = _collect_line_limit_violations({})

    assert len(violations) == 1
    assert "b.py" in violations[0]
    assert "1001 non-import lines (limit 1000)" in violations[0]


def test_collect_line_limit_violations_reports_unparseable_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    src_root = tmp_path / "src"
    src_root.mkdir()
    monkeypatch.setattr("tests.arch._helpers.SRC_ROOT", src_root)
    (src_root / "c.py").write_text("def (:\n", encoding="utf-8")
    (src_root / "d.py").write_text("v = 1\n", encoding="utf-8")

    violations = _collect_line_limit_violations({})

    assert len(violations) == 1
    assert "c.py" in violations[0]
    assert "cannot be measured" in violations[0]


def _is_count_budget_lines_call(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "count_budget_lines"
    )


def _is_raw_splitlines_len_call(node: ast.AST) -> bool:
    if not (
        isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "len"
    ):
        return False
    if len(node.args) != 1:
        return False
    (arg,) = node.args
    return (
        isinstance(arg, ast.Call)
        and isinstance(arg.func, ast.Attribute)
        and arg.func.attr == "splitlines"
    )


@pytest.mark.parametrize(
    ("rel_path", "expected_calls"),
    [
        ("scripts/check_file_lengths.py", 1),
        ("tests/arch/_helpers.py", 1),
        ("tests/arch/test_skills_subpackage_size_ceilings.py", 1),
        ("tests/arch/test_subpackage_isolation_capture_layout.py", 1),
        ("tests/arch/test_subpackage_isolation_size.py", 7),
        ("tests/arch/test_subpackage_isolation_smoke_review.py", 2),
        ("tests/hooks/test_hook_registry_decomposition.py", 1),
        ("tests/migration/test_engine_decomposition.py", 2),
        ("tests/recipe/rules_merge/test_split_lines_under_ceiling.py", 2),
        ("tests/recipe/rules_skills/test_split_lines_under_ceiling.py", 1),
        # tests/arch/test_subpackage_isolation_topology.py is intentionally excluded:
        # it also has an unrelated len(<expr>.splitlines()) call (in
        # test_smoke_utils_suite_is_split, a tests/ shard-size check outside
        # REQ-CNST-010's src/autoskillit/ scope) that would trip this test's
        # raw-splitlines guard for reasons unrelated to the count_budget_lines migration.
    ],
)
def test_req_cnst_010_enforcers_measure_through_count_budget_lines(
    rel_path: str, expected_calls: int
) -> None:
    path = REPO_ROOT / rel_path
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

    imported = any(
        isinstance(node, ast.ImportFrom)
        and any(alias.name == "count_budget_lines" for alias in node.names)
        for node in ast.walk(tree)
    )
    assert imported, f"{rel_path}: count_budget_lines is not imported"

    calls = [node for node in ast.walk(tree) if _is_count_budget_lines_call(node)]
    assert len(calls) == expected_calls, (
        f"{rel_path}: expected {expected_calls} count_budget_lines call(s), found "
        f"{len(calls)} at lines {[getattr(c, 'lineno', '?') for c in calls]}"
    )

    raw_calls = [node for node in ast.walk(tree) if _is_raw_splitlines_len_call(node)]
    assert not raw_calls, (
        f"{rel_path}: raw len(<expr>.splitlines()) call(s) remain at lines "
        f"{[getattr(c, 'lineno', '?') for c in raw_calls]}"
    )
