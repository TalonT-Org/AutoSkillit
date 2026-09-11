"""Static-scan guard: every shim file must use explicit re-exports, not ``import *``.

The shims themselves must use **explicit re-exports**, not ``import *``,
to avoid ambiguity: a wildcard import silently forwards a different surface
than the shim author declared, and ``import *`` excludes underscore-prefixed
names unless the source module declares ``__all__``.

A shim using ``from <module> import *`` is fragile because the public surface
of the source module can change without any test failure in the shim itself,
and ``import *`` excludes underscore-prefixed names unless the source
module declares ``__all__``. The shim author must enumerate the names they
want to forward.

This test scans every shim listed in ``_SHIM_FILENAMES`` (core) and
``_RECIPE_SHIM_FILENAMES`` (recipe) and fails on any ``import *`` pattern.
Shims not yet present on disk are skipped (the registry can name a shim
that a future commit will create).

Coverage:
    - ``core/*.py`` shims listed in ``_SHIM_FILENAMES``
    - ``recipe/*.py`` shims listed in ``_RECIPE_SHIM_FILENAMES``
"""

from __future__ import annotations

import re

import pytest

from tests.arch._helpers import SRC_ROOT
from tests.arch.test_subpackage_isolation_file_counts import (
    _RECIPE_SHIM_FILENAMES,
    _SHIM_FILENAMES,
)

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]


# Match `from X import *` (with or without leading-dot relative imports) and
# bare `import *` (which is invalid syntax, but defensive).
_WILDCARD_IMPORT_RE = re.compile(r"^\s*from\s+[\w.]+\s+import\s+\*\s*(#.*)?$", re.MULTILINE)


def _collect_shim_paths() -> list[tuple[str, str]]:
    """Return ``[(package_label, shim_path), ...]`` for every registered shim."""
    paths: list[tuple[str, str]] = []
    for name in sorted(_SHIM_FILENAMES):
        paths.append(("core", str(SRC_ROOT / "core" / name)))
    for name in sorted(_RECIPE_SHIM_FILENAMES):
        paths.append(("recipe", str(SRC_ROOT / "recipe" / name)))
    return paths


def test_no_shim_uses_wildcard_import() -> None:
    """REQ-CNST-003: every shim must enumerate its re-exports.

    The test fails with a per-shim message identifying the package and the
    shim file. Empty ``__all__`` declarations are also flagged — they
    silently expose nothing via ``from <shim> import *``, defeating the
    shim's backward-compat purpose.
    """
    violations: list[str] = []
    for package_label, shim_path in _collect_shim_paths():
        # shim_path is "src/autoskillit/<pkg>/<name>" — convert back to Path.
        rel = shim_path.removeprefix(str(SRC_ROOT) + "/")
        full = SRC_ROOT / rel
        if not full.is_file():
            # Registry may name a shim that a future commit will create;
            # the on-disk-existence check lives in
            # ``test_subpackage_isolation_file_counts``.
            continue
        body = full.read_text(encoding="utf-8")
        if _WILDCARD_IMPORT_RE.search(body):
            violations.append(f"{package_label}/{rel}: uses `from X import *`")
        if "__all__: list[str] = []" in body or "__all__ = []" in body:
            violations.append(
                f"{package_label}/{rel}: empty __all__ defeats wildcard-import surface"
            )
    assert not violations, "Shims must use explicit re-exports:\n" + "\n".join(
        f"  {v}" for v in violations
    )


def test_shim_registry_files_exist() -> None:
    """Every name in ``_SHIM_FILENAMES`` / ``_RECIPE_SHIM_FILENAMES`` resolves to a real file.

    The shim registries are paired with the shim-skip predicate in
    ``test_subpackage_isolation_file_counts``. A registry entry that names
    a non-existent file silently no-ops the predicate, allowing the file
    count to drift upward without test failure.
    """
    missing: list[str] = []
    for name in sorted(_SHIM_FILENAMES):
        if not (SRC_ROOT / "core" / name).is_file():
            missing.append(f"core/{name}")
    for name in sorted(_RECIPE_SHIM_FILENAMES):
        if not (SRC_ROOT / "recipe" / name).is_file():
            missing.append(f"recipe/{name}")
    assert not missing, "Shim registry entries without matching files:\n" + "\n".join(
        f"  {m}" for m in missing
    )
