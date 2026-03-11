"""Release-readiness sanity checks."""

import getpass
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent


def test_no_personal_home_paths_in_test_files():
    """No tracked test files may contain the current user's /home/<user> absolute paths."""
    username = getpass.getuser()
    personal_prefix = f"/home/{username}/"
    result = subprocess.run(
        ["git", "grep", "-rn", personal_prefix, "--", "tests/"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    hits = [
        line
        for line in result.stdout.splitlines()
        if personal_prefix in line and not line.strip().startswith("#")
    ]
    assert hits == [], "Personal home paths found in tests:\n" + "\n".join(hits)


def test_sync_manifest_in_gitignore():
    """.autoskillit/sync_manifest.json must be gitignored."""
    gitignore = (REPO_ROOT / ".gitignore").read_text()
    assert ".autoskillit/sync_manifest.json" in gitignore


def test_batch_impl_marked_experimental():
    """batch-implementation.yaml must carry experimental: true."""
    import yaml

    recipe = yaml.safe_load(
        (REPO_ROOT / "src/autoskillit/recipes/batch-implementation.yaml").read_text()
    )
    assert recipe.get("experimental") is True
