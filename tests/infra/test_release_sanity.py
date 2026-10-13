"""Release-readiness sanity checks."""

import getpass
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("infra"), pytest.mark.medium]

REPO_ROOT = Path(__file__).parent.parent.parent
_PERSONAL_HOME_PATH_FIXTURES: dict[Path, frozenset[int]] = {}


def test_no_personal_home_paths_in_test_files():
    """No tracked test files may contain the current user's /home/<user> absolute paths."""
    username = getpass.getuser()
    personal_prefix = f"/home/{username}/"
    tests_dir = REPO_ROOT / "tests"
    hits = []
    for py_file in tests_dir.rglob("*.py"):
        relative_path = py_file.relative_to(REPO_ROOT)
        for lineno, line in enumerate(py_file.read_text(errors="replace").splitlines(), 1):
            if (
                personal_prefix in line
                and not line.strip().startswith("#")
                and lineno not in _PERSONAL_HOME_PATH_FIXTURES.get(relative_path, frozenset())
            ):
                hits.append(f"{relative_path}:{lineno}: {line.rstrip()}")
    assert hits == [], "Personal home paths found in tests:\n" + "\n".join(hits)


def test_sync_manifest_in_gitignore():
    """.autoskillit/sync_manifest.json must be gitignored."""
    gitignore = (REPO_ROOT / ".gitignore").read_text()
    assert ".autoskillit/sync_manifest.json" in gitignore


def test_autoskillit_root_entries_are_all_registered():
    """Every .autoskillit/ root entry must be registered or self-ignored.

    Files may be registered in _COMMITTED_BY_DESIGN or
    _AUTOSKILLIT_GITIGNORE_ENTRIES; .gitignore itself is always accepted. A
    runtime directory must have a valid self-ignore marker. A directory without
    one is a namespace left by an earlier version: delete it or run a dispatch
    to repair its marker.
    """
    from autoskillit.core.io import (
        _AUTOSKILLIT_GITIGNORE_ENTRIES,
        _COMMITTED_BY_DESIGN,
        is_self_ignored,
    )

    autoskillit_dir = REPO_ROOT / ".autoskillit"
    if not autoskillit_dir.is_dir():
        return  # project not initialized — skip

    registered_names = (
        _COMMITTED_BY_DESIGN
        | {e.rstrip("/") for e in _AUTOSKILLIT_GITIGNORE_ENTRIES}
        | {".gitignore"}  # the directory's own gitignore is always valid
    )

    unregistered = [
        p.name
        for p in autoskillit_dir.iterdir()
        if p.name not in registered_names and not (p.is_dir() and is_self_ignored(p))
    ]

    assert not unregistered, (
        f"Unregistered entries found in .autoskillit/: {unregistered}. "
        f"A runtime directory without a valid self-ignore marker is a namespace "
        f"left by an earlier version; delete it or run a dispatch to repair the "
        f"marker. Runtime files belong in .autoskillit/temp/ where they are "
        f"auto-gitignored by temp/.gitignore ('*'). Persistent files that belong "
        f"in .autoskillit/ root need an entry added to "
        f"_AUTOSKILLIT_GITIGNORE_ENTRIES in src/autoskillit/core/io/io.py."
    )
