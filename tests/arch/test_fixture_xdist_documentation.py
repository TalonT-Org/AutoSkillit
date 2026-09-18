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

    Walks top-level FunctionDef/AsyncFunctionDef nodes matching the name. Returns
    None when the fixture is not found (the test then fails with a clear message).
    """
    tree = ast.parse(path.read_text())
    for node in ast.iter_child_nodes(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name != fixture_name:
            continue
        decorators = [ast.unparse(d) for d in node.decorator_list]
        if not any("fixture" in d for d in decorators):
            continue
        return ast.get_docstring(node)
    return None


def test_detect_tmp_git_contamination_documents_xdist_worker_semantics():
    """C7.2: conftest._detect_tmp_git_contamination is session-scoped and autouse.

    Under pytest -n 4 (xdist, --dist load), session-scoped fixtures run once per
    worker process, not once for the whole run. The docstring must call this out
    so future readers do not assume single-execution semantics.
    """
    doc = _fixture_docstring(CONFTEST, "_detect_tmp_git_contamination")
    assert doc is not None, "_detect_tmp_git_contamination fixture not found in tests/conftest.py"
    assert "xdist" in doc or "worker" in doc.lower(), (
        f"_detect_tmp_git_contamination docstring must mention xdist/worker "
        f"semantics; got: {doc!r}"
    )


def test_structlog_session_init_documents_xdist_worker_semantics():
    """C7.2: conftest._structlog_session_init runs once per xdist worker.

    _structlog_proxies is a module-level list populated by this fixture's per-worker
    invocation; cross-worker state cannot leak because each worker is a separate
    process. The docstring must call this out explicitly.
    """
    doc = _fixture_docstring(CONFTEST, "_structlog_session_init")
    assert doc is not None, "_structlog_session_init fixture not found in tests/conftest.py"
    assert "xdist" in doc or "worker" in doc.lower(), (
        f"_structlog_session_init docstring must mention xdist/worker semantics; got: {doc!r}"
    )


def test_logging_override_structlog_to_null_documents_override_pattern():
    """C7.3: TestConfigureLogging._structlog_to_null overrides the conftest autouse.

    pytest resolves fixtures by name in the test class's MRO; a class-level
    autouse with the same name as a parent fixture shadows the parent. The
    docstring must explain this mechanism so readers do not mistake the empty
    body for a bug.
    """
    doc = _fixture_docstring(TEST_LOGGING, "_structlog_to_null")
    assert doc is not None, "_structlog_to_null fixture not found in tests/core/test_logging.py"
    assert "override" in doc.lower(), (
        f"_structlog_to_null docstring must state it overrides the parent "
        f"autouse fixture; got: {doc!r}"
    )
