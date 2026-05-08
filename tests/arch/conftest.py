from __future__ import annotations

import os
import warnings
from pathlib import Path

import pytest

from tests._test_filter import git_changed_files
from tests.arch._deselection import deselect_arch_items

_ARCH_DIR = Path(__file__).parent
_PROJECT_ROOT = _ARCH_DIR.parent.parent


def pytest_collection_modifyitems(
    config: pytest.Config,
    items: list[pytest.Item],
) -> None:
    """Deselect source_file-parametrized arch cases for unchanged files.

    Fail-open: any error (git failure, env parse error, exception) leaves
    all items selected.
    """
    filter_mode = os.environ.get("AUTOSKILLIT_TEST_FILTER", "").strip().lower()
    if not filter_mode or filter_mode in ("0", "false", "no", "none"):
        return
    try:
        changed = git_changed_files(cwd=_PROJECT_ROOT)
        if changed is None:
            return
        changed_abs = {(_PROJECT_ROOT / f).resolve() for f in changed}
        selected, deselected = deselect_arch_items(items, changed_abs, _ARCH_DIR)
        if deselected:
            config.hook.pytest_deselected(items=deselected)
            items[:] = selected
    except Exception as exc:
        warnings.warn(
            f"arch deselection failed (fail-open): {exc!r}",
            stacklevel=2,
        )
        return
