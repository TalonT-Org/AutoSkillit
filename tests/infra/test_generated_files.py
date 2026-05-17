"""Tests that generated files with machine-local paths are not tracked in git."""

import re
import subprocess
from pathlib import Path

from autoskillit.core.paths import GENERATED_FILES, is_generated_path

REPO_ROOT = Path(__file__).parent.parent.parent


def test_generated_files_importable_from_core_paths():
    """GENERATED_FILES is importable from autoskillit.core.paths and is a frozenset[str]."""
    assert isinstance(GENERATED_FILES, frozenset)
    assert len(GENERATED_FILES) > 0
    for entry in GENERATED_FILES:
        assert isinstance(entry, str)


def test_no_generated_files_tracked():
    """Generated config and diagram files must not be tracked in git."""
    result = subprocess.run(
        ["git", "ls-files"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    tracked = set(result.stdout.splitlines())

    def _tracked_for_entry(entry: str) -> list[str]:
        if entry.endswith("/"):
            return [f for f in tracked if f.startswith(entry)]
        return [entry] if entry in tracked else []

    tracked_generated = [f for entry in GENERATED_FILES for f in _tracked_for_entry(entry)]
    assert tracked_generated == [], (
        f"Generated files must not be tracked in git: {tracked_generated}. "
        "Run 'git rm --cached <file>' and ensure the path is in .gitignore."
    )


def test_gitignore_covers_generated_paths():
    """`.gitignore` must have patterns for all generated config files."""
    gitignore = (REPO_ROOT / ".gitignore").read_text()
    for path in GENERATED_FILES:
        assert path in gitignore, f"Missing .gitignore entry for generated file: {path}"


def test_generated_files_covers_precommit_pattern():
    """Every entry in GENERATED_FILES must match the no-generated-configs pre-commit pattern."""
    config_text = (REPO_ROOT / ".pre-commit-config.yaml").read_text()
    # Extract the files: pattern from the no-generated-configs hook
    match = re.search(r"id:\s*no-generated-configs.*?files:\s*'([^']+)'", config_text, re.DOTALL)
    assert match, "Could not find no-generated-configs hook with files: pattern"
    pattern = match.group(1)
    for path in GENERATED_FILES:
        assert re.search(pattern, path), (
            f"GENERATED_FILES entry {path!r} does not match "
            f"no-generated-configs pattern {pattern!r}"
        )


def test_generated_files_covers_all_build_outputs():
    """Every file produced by build scripts must be covered by GENERATED_FILES."""
    recipes_dir = REPO_ROOT / "src" / "autoskillit" / "recipes"

    # Contract cards (YAML + JSON)
    contract_files = list((recipes_dir / "contracts").rglob("*"))
    for f in contract_files:
        if f.is_file():
            rel = str(f.relative_to(REPO_ROOT))
            assert is_generated_path(rel), (
                f"Build output {rel} is not covered by GENERATED_FILES. "
                "Add an entry or directory prefix."
            )

    # Diagram files
    diagram_files = list((recipes_dir / "diagrams").rglob("*.md"))
    for f in diagram_files:
        rel = str(f.relative_to(REPO_ROOT))
        assert is_generated_path(rel), f"Diagram {rel} not in GENERATED_FILES"


def test_regen_contracts_is_idempotent(tmp_path):
    """Running regen-contracts twice must produce identical output (no mtime changes)."""
    from autoskillit.recipe.contracts import generate_recipe_card
    from autoskillit.recipe.io import builtin_recipes_dir

    recipes_dir = builtin_recipes_dir()
    # Test on one recipe
    yaml_paths = sorted(recipes_dir.glob("*.yaml"))
    assert yaml_paths, "No recipe YAML files found"

    test_yaml = yaml_paths[0]
    out_dir = tmp_path / "contracts"
    out_dir.mkdir()

    # First run
    generate_recipe_card(test_yaml, recipes_dir=out_dir)
    card1 = out_dir / "contracts" / f"{test_yaml.stem}.yaml"
    assert card1.exists(), f"Contract card not created: {card1}"
    content1 = card1.read_text()

    # Second run
    generate_recipe_card(test_yaml, recipes_dir=out_dir)
    content2 = card1.read_text()

    assert content1 == content2, (
        "regen_contracts is not idempotent: second run produced different content"
    )


def test_compile_recipes_is_idempotent(tmp_path):
    """Running compile_recipes twice must not modify any JSON files when content is unchanged."""
    import shutil
    import sys

    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    from compile_recipes import _compile_one

    from autoskillit.recipe.io import builtin_recipes_dir

    recipes_dir = builtin_recipes_dir()
    yaml_paths = sorted(recipes_dir.rglob("*.yaml"))
    assert yaml_paths, "No recipe YAML files found"

    src_yaml = yaml_paths[0]
    test_yaml = tmp_path / src_yaml.name
    shutil.copy2(src_yaml, test_yaml)
    json_path = test_yaml.with_suffix(".json")

    _compile_one(test_yaml)
    assert json_path.exists(), f"JSON not created: {json_path}"
    mtime1 = json_path.stat().st_mtime_ns

    _compile_one(test_yaml)
    mtime2 = json_path.stat().st_mtime_ns

    assert mtime1 == mtime2, "compile_recipes is not idempotent: second run modified JSON mtime"
