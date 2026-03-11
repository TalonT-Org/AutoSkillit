"""Release-readiness sanity checks."""

import getpass
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent


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


def test_batch_impl_marked_experimental():
    """batch-implementation.yaml must carry experimental: true."""
    import yaml

    recipe_path = REPO_ROOT / "src/autoskillit/recipes/batch-implementation.yaml"
    assert recipe_path.exists(), f"batch-implementation.yaml not found at {recipe_path}"
    recipe = yaml.safe_load(recipe_path.read_text())
    assert recipe.get("experimental") is True
