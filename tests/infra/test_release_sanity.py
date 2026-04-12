"""Release-readiness sanity checks."""

import getpass
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent.parent


def test_no_personal_home_paths_in_test_files():
    """No tracked test files may contain the current user's /home/<user> absolute paths."""
    username = getpass.getuser()
    personal_prefix = f"/home/{username}/"
    tests_dir = REPO_ROOT / "tests"
    hits = []
    for py_file in tests_dir.rglob("*.py"):
        for lineno, line in enumerate(py_file.read_text(errors="replace").splitlines(), 1):
            if personal_prefix in line and not line.strip().startswith("#"):
                hits.append(f"{py_file.relative_to(REPO_ROOT)}:{lineno}: {line.rstrip()}")
    assert hits == [], "Personal home paths found in tests:\n" + "\n".join(hits)


def test_sync_manifest_in_gitignore():
    """.autoskillit/sync_manifest.json must be gitignored."""
    gitignore = (REPO_ROOT / ".gitignore").read_text()
    assert ".autoskillit/sync_manifest.json" in gitignore


def test_autoskillit_root_files_are_all_registered():
    """.autoskillit/ root must only contain files registered in _COMMITTED_BY_DESIGN
    or _AUTOSKILLIT_GITIGNORE_ENTRIES (or .gitignore itself).

    Catches any session-scoped file mistakenly placed outside temp/ at CI time.
    If .hook_config.json (or any future runtime artifact) is present in the root
    rather than temp/, this test fails immediately.
    """
    from autoskillit.core.io import _AUTOSKILLIT_GITIGNORE_ENTRIES, _COMMITTED_BY_DESIGN

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
        if not p.is_dir() and p.name not in registered_names
    ]

    assert not unregistered, (
        f"Unregistered files found in .autoskillit/: {unregistered}. "
        f"Session-scoped runtime files belong in .autoskillit/temp/ where they "
        f"are auto-gitignored by temp/.gitignore ('*'). Persistent files that "
        f"belong in .autoskillit/ root need an entry added to "
        f"_AUTOSKILLIT_GITIGNORE_ENTRIES in src/autoskillit/core/io.py."
    )
