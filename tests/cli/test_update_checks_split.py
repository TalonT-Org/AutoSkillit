from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("cli"), pytest.mark.small]

_CLI_SRC = Path(__file__).parent.parent.parent / "src" / "autoskillit" / "cli"


def test_update_checks_fetch_file_exists():
    assert (_CLI_SRC / "_update_checks_fetch.py").exists()


def test_update_checks_source_file_exists():
    assert (_CLI_SRC / "_update_checks_source.py").exists()


def test_update_checks_fetch_importable():
    from autoskillit.cli._update_checks_fetch import (  # noqa: F401
        _fetch_with_cache, invalidate_fetch_cache, _fetch_latest_version,
    )


def test_update_checks_source_importable():
    from autoskillit.cli._update_checks_source import (  # noqa: F401
        find_source_repo, resolve_reference_sha,
    )


def test_update_checks_facade_public_api():
    from autoskillit.cli._update_checks import Signal, run_update_checks  # noqa: F401


def test_order_module_file_exists():
    assert (_CLI_SRC / "_order.py").exists()


def test_order_importable_from_submodule():
    from autoskillit.cli._order import order, _get_subsets_needed  # noqa: F401
