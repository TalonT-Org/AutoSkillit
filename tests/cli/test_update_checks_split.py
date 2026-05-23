"""Structural guards for the update_checks test split."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("cli"), pytest.mark.small]

_CLI_SRC = Path(__file__).parent.parent.parent / "src" / "autoskillit" / "cli"


def test_update_checks_fetch_importable():
    from autoskillit.cli.update._update_checks_fetch import (  # noqa: F401
        _fetch_latest_version,
        _fetch_with_cache,
        invalidate_fetch_cache,
    )


def test_update_checks_source_importable():
    from autoskillit.cli.update._update_checks_source import (  # noqa: F401
        find_source_repo,
        resolve_reference_sha,
    )


def test_update_checks_facade_public_api():
    from autoskillit.cli.update._update_checks import Signal, run_update_checks  # noqa: F401


def test_order_module_file_exists():
    assert (_CLI_SRC / "session" / "_order.py").exists()


def test_order_importable_from_submodule():
    from autoskillit.cli.session._order import _get_subsets_needed, order  # noqa: F401


_CLI_TESTS = Path(__file__).parent


def _has_pytestmark_cli(path: Path) -> bool:
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "pytestmark":
                    src = ast.unparse(node.value)
                    return 'layer("cli")' in src or "layer('cli')" in src
    return False


def test_update_checks_lifecycle_file_exists():
    """test_update_checks_lifecycle.py must exist after the split."""
    assert (_CLI_TESTS / "test_update_checks_lifecycle.py").exists()


def test_update_checks_lifecycle_has_correct_pytestmark():
    p = _CLI_TESTS / "test_update_checks_lifecycle.py"
    assert _has_pytestmark_cli(p), (
        "test_update_checks_lifecycle.py missing layer('cli') pytestmark"
    )
