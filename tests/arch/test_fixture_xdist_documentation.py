"""Regression guard: conftest session fixtures and overrides must document xdist semantics."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

CONFTEST = Path(__file__).resolve().parent.parent / "conftest.py"
TEST_LOGGING = Path(__file__).resolve().parent.parent / "core" / "test_logging.py"


def _fixture_docstring(path: Path, fixture_name: str) -> str | None:
    """Return the docstring of the @pytest.fixture function named ``fixture_name`` in ``path``.

    Walks every FunctionDef/AsyncFunctionDef in the file (top-level or nested
    inside a class) so fixtures defined inside test classes are located too.
    Returns None when the fixture is not found (the test then fails with a
    clear message).
    """
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name != fixture_name:
            continue
        decorators = [ast.unparse(d) for d in node.decorator_list]
        if not any("fixture" in d for d in decorators):
            continue
        return ast.get_docstring(node)
    return None


@pytest.mark.parametrize(
    ("path", "fixture_name", "required_keywords"),
    [
        pytest.param(
            CONFTEST,
            "_detect_tmp_git_contamination",
            ("xdist", "worker"),
            id="conftest_detect_tmp_git_contamination",
        ),
        pytest.param(
            CONFTEST,
            "_structlog_session_init",
            ("xdist", "worker"),
            id="conftest_structlog_session_init",
        ),
        pytest.param(
            TEST_LOGGING,
            "_structlog_to_null",
            ("override",),
            id="logging_override_structlog_to_null",
        ),
    ],
)
def test_fixture_documents_required_semantics(
    path: Path,
    fixture_name: str,
    required_keywords: tuple[str, ...],
) -> None:
    """C7.2/C7.3: session-scoped and override fixtures must call out their semantics.

    Under ``pytest -n 4`` (xdist, ``--dist load``), session-scoped fixtures run
    once per worker process, not once for the whole run, and class-level
    autouse fixtures shadow same-named parent fixtures in the test class's MRO.
    Docstrings must call these mechanisms out so future readers do not assume
    single-execution semantics or mistake an empty-body override for a bug.
    """
    doc = _fixture_docstring(path, fixture_name)
    assert doc is not None, f"{fixture_name} fixture not found in {path}"
    doc_lower = doc.lower()
    assert any(keyword in doc_lower for keyword in required_keywords), (
        f"{fixture_name} docstring must mention one of {required_keywords!r}; got: {doc!r}"
    )
