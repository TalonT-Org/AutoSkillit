"""Structural guards for the update_checks test split."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from ._split_helpers import _has_pytestmark_cli

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


def test_update_checks_lifecycle_file_exists():
    """test_update_checks_lifecycle.py must exist after the split."""
    assert (_CLI_TESTS / "test_update_checks_lifecycle.py").exists()


def test_update_checks_lifecycle_has_correct_pytestmark():
    p = _CLI_TESTS / "test_update_checks_lifecycle.py"
    assert _has_pytestmark_cli(p), (
        "test_update_checks_lifecycle.py missing layer('cli') pytestmark"
    )


def test_update_checks_lifecycle_contains_expected_functions():
    p = _CLI_TESTS / "test_update_checks_lifecycle.py"
    tree = ast.parse(p.read_text())
    func_names = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    assert "test_stale_fetch_cache_after_install_detected_by_epoch" in func_names
    assert (
        "test_full_lifecycle_install_clears_stale_cache_then_check_detects_new_version"
        in func_names
    )
    assert "test_run_update_sequence_invalidates_fetch_cache" in func_names
