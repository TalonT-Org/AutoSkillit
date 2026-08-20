"""Standing invariant: every CLI flag-arity spec table has a generative test.

Each of this rectify's CLI flag-arity spec tables (_GH_API_FLAG_SPEC and
_CURL_FLAG_SPEC in hooks/_github_mutation_analysis.py; _GIT_GLOBAL_FLAG_SPEC
and _PIP_GLOBAL_FLAG_SPEC in hooks/_command_classification.py) must have a
corresponding test parametrized directly from the table's own keys (e.g.
`@pytest.mark.parametrize("flag", sorted(_GH_API_FLAG_SPEC))`), not a
hand-maintained flag list a future table update could silently drift out of
sync with -- adding a flag to the table then automatically extends the
parametrize's coverage with no separate test-list edit required.

This mirrors tests/arch/test_fail_closed_guard_contract.py's AST-scan and
batch-report shape: a structural test about the test suite's own
completeness, not about runtime behavior (the generative tests themselves,
in tests/hooks/test_command_classification.py and
tests/infra/test_unsafe_install_guard.py, exercise runtime behavior).
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

_REPO_ROOT = Path(__file__).resolve().parents[2]
_TESTS_ROOT = _REPO_ROOT / "tests"

# spec table name -> candidate test file(s), at least one of which must
# reference it via a pytest.mark.parametrize(...) call.
_SPEC_TABLE_TEST_FILES: dict[str, tuple[str, ...]] = {
    "_GH_API_FLAG_SPEC": ("hooks/test_command_classification.py",),
    "_CURL_FLAG_SPEC": ("hooks/test_command_classification.py",),
    "_GIT_GLOBAL_FLAG_SPEC": ("hooks/test_command_classification.py",),
    "_GIT_FETCH_FLAG_SPEC": ("infra/test_git_ops_guard.py",),
    "_PIP_GLOBAL_FLAG_SPEC": ("infra/test_unsafe_install_guard.py",),
}


def _references_spec_table_in_parametrize(source: str, table_name: str) -> bool:
    """Return True if *source* has a pytest.mark.parametrize(...) call whose

    arguments reference *table_name* by name (e.g.
    `sorted(_GH_API_FLAG_SPEC)`) -- proving the parametrize genuinely
    iterates the table's own current keys at collection time, rather than a
    frozen/hand-copied flag list that could silently drift out of sync with
    the table it was copied from.
    """
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr == "parametrize"):
            continue
        if any(isinstance(n, ast.Name) and n.id == table_name for n in ast.walk(node)):
            return True
    return False


def test_parametrize_detection_requires_actual_reference() -> None:
    """Prove the scanner is live, not vacuous: it must distinguish a

    parametrize that genuinely references the spec table by name from one
    that merely happens to sit in the same file.
    """
    referenced = (
        "import pytest\n\n"
        '@pytest.mark.parametrize("flag", sorted(_MY_SPEC))\n'
        "def test_x(flag):\n    pass\n"
    )
    not_referenced = (
        "import pytest\n\n"
        '@pytest.mark.parametrize("flag", ["a", "b"])\n'
        "def test_x(flag):\n    pass\n"
    )

    assert _references_spec_table_in_parametrize(referenced, "_MY_SPEC")
    assert not _references_spec_table_in_parametrize(not_referenced, "_MY_SPEC")


def test_every_cli_flag_spec_table_has_a_generative_parametrized_test() -> None:
    """Every table in _SPEC_TABLE_TEST_FILES must have a live parametrize

    reference in at least one of its candidate test files.
    """
    missing: list[str] = []
    for table_name, candidate_paths in _SPEC_TABLE_TEST_FILES.items():
        found = False
        for rel_path in candidate_paths:
            full_path = _TESTS_ROOT / rel_path
            if not full_path.exists():
                continue
            source = full_path.read_text(encoding="utf-8")
            if _references_spec_table_in_parametrize(source, table_name):
                found = True
                break
        if not found:
            missing.append(
                f"{table_name} — no pytest.mark.parametrize(...) referencing it by name "
                f"found in: {', '.join(candidate_paths)}"
            )
    assert not missing, (
        "CLI flag spec tables missing a generative parametrized test:\n"
        + "\n".join(f"  {m}" for m in missing)
    )
