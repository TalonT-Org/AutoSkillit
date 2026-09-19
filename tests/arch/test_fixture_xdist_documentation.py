"""Regression guard: conftest session fixtures and overrides must document xdist semantics."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

CONFTEST = Path(__file__).resolve().parent.parent / "conftest.py"
TEST_LOGGING = Path(__file__).resolve().parent.parent / "core" / "test_logging.py"


def _extract_scope(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str | None:
    """Return the ``scope=`` kwarg from the @pytest.fixture(...) decorator, or None."""
    for decorator in node.decorator_list:
        if not isinstance(decorator, ast.Call):
            continue
        for kw in decorator.keywords:
            if kw.arg == "scope" and isinstance(kw.value, ast.Constant):
                value = kw.value.value
                if isinstance(value, str):
                    return value
    return None


def _fixture_metadata(path: Path, fixture_name: str) -> tuple[str | None, str | None]:
    """Return (docstring, scope) for the @pytest.fixture named ``fixture_name`` in ``path``.

    Walks every FunctionDef/AsyncFunctionDef in the file (top-level or nested
    inside a class) so fixtures defined inside test classes are located too.
    Returns (None, None) when the fixture is not found (the test then fails
    with a clear message).

    ``scope`` is the value of the ``scope=`` keyword argument on the
    ``@pytest.fixture(...)`` decorator, or ``None`` when no scope was passed
    (pytest's default is ``'function'``).
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
        return ast.get_docstring(node), _extract_scope(node)
    return None, None


@pytest.mark.parametrize(
    ("path", "fixture_name", "required_keywords", "expected_scope"),
    [
        pytest.param(
            CONFTEST,
            "_detect_tmp_git_contamination",
            ("xdist", "worker"),
            "session",
            id="conftest_detect_tmp_git_contamination",
        ),
        pytest.param(
            CONFTEST,
            "_structlog_session_init",
            ("xdist", "worker"),
            "session",
            id="conftest_structlog_session_init",
        ),
        pytest.param(
            TEST_LOGGING,
            "_structlog_to_null",
            ("override", "xdist"),
            None,
            id="test_logging_structlog_to_null",
        ),
    ],
)
def test_fixture_documents_required_semantics(
    path: Path,
    fixture_name: str,
    required_keywords: tuple[str, ...],
    expected_scope: str | None,
) -> None:
    """C7.2/C7.3: session-scoped and override fixtures must call out their semantics.

    Under ``pytest -n 4`` (xdist, ``--dist load``), session-scoped fixtures run
    once per worker process, not once for the whole run, and class-level
    autouse fixtures shadow same-named parent fixtures in the test class's MRO.
    Docstrings must call these mechanisms out so future readers do not assume
    single-execution semantics or mistake an empty-body override for a bug.

    Keyword selection rationale: every protected fixture documents two
    distinct concepts — the mechanism (``override``, ``session``) and the
    execution environment (``xdist``, ``worker``). Each tuple has exactly two
    keywords so the assertion shape is uniform across fixtures and a
    regression that drops either concept fails the guard. ``all(...)`` is
    used (not ``any(...)``) so a docstring mentioning only one of the two
    required keywords does not silently satisfy the assertion.

    Scope verification rationale: the xdist callout on a session-scoped
    fixture only makes sense if the fixture is actually ``scope='session'``.
    A regression that drops the scope kwarg while keeping the docstring (or
    vice versa) would otherwise pass. The override fixture's scope is not
    asserted (``expected_scope=None``) because the override is class-level
    autouse — its per-worker isolation comes from MRO shadowing, not from
    session scope.
    """
    doc, actual_scope = _fixture_metadata(path, fixture_name)
    assert doc is not None, f"{fixture_name} fixture not found in {path}"
    doc_lower = doc.lower()
    assert all(keyword in doc_lower for keyword in required_keywords), (
        f"{fixture_name} docstring must mention ALL of {required_keywords!r}; got: {doc!r}"
    )
    if expected_scope is not None:
        assert actual_scope == expected_scope, (
            f"{fixture_name} scope is {actual_scope!r}; expected {expected_scope!r}. "
            f"The xdist callout only applies if the fixture is actually "
            f"{expected_scope}-scoped."
        )
