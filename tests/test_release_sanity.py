"""Release-readiness sanity checks."""
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent


def test_no_personal_home_paths_in_test_files():
    """No tracked test files may contain /home/<user> absolute paths."""
    result = subprocess.run(
        ["git", "grep", "-rn", r"/home/", "--", "tests/"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    # Filter out lines that are just test comments / docstrings discussing env vars
    hits = [
        line
        for line in result.stdout.splitlines()
        if "/home/" in line and not line.strip().startswith("#")
    ]
    assert hits == [], f"Personal home paths found in tests:\n" + "\n".join(hits)


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
