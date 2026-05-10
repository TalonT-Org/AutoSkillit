"""Tests for recipe_contract_freshness pre-commit script."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("hooks"), pytest.mark.medium]


def test_hook_detects_stale_contract(tmp_path: Path) -> None:
    """Pre-commit script should fail when a recipe's contract is stale."""
    recipe_content = """
name: test-freshness
version: "1.0.0"
ingredients: []
steps:
  test_step:
    tool: run_cmd
    with:
      cmd: echo test
"""
    recipe_file = tmp_path / "test-recipe.yaml"
    recipe_file.write_text(recipe_content)

    contracts_dir = tmp_path / "contracts"
    contracts_dir.mkdir()
    stale_card = {
        "schema_version": "1.0",
        "pipeline_hash": "old_hash",
        "step_fields_hash": "old_fields",
        "block_fingerprints": {},
        "skill_hashes": {},
        "generated_at": "2020-01-01T00:00:00Z",
    }
    (contracts_dir / "test-recipe.yaml").write_text(str(stale_card))

    result = subprocess.run(
        [
            sys.executable,
            str(
                Path(__file__).resolve().parent.parent.parent.parent
                / "scripts"
                / "recipe_contract_freshness.py"
            ),
            str(recipe_file),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0, (
        f"Expected non-zero exit for stale contract, got {result.returncode}"
    )
    assert (
        "stale" in result.stdout.lower()
        or "outdated" in result.stdout.lower()
        or "missing" in result.stdout.lower()
    ), f"Expected stale/missing diagnostic in output, got: {result.stdout}"


def test_hook_passes_when_contract_is_fresh(tmp_path: Path) -> None:
    """Pre-commit script should pass when the contract is up-to-date."""
    recipe_content = """
name: test-freshness
version: "1.0.0"
ingredients: []
steps:
  test_step:
    tool: run_cmd
    with:
      cmd: echo test
"""
    recipe_file = tmp_path / "test-recipe.yaml"
    recipe_file.write_text(recipe_content)

    contracts_dir = tmp_path / "contracts"
    contracts_dir.mkdir()

    from autoskillit.recipe.contracts import generate_recipe_card

    card = generate_recipe_card(recipe_content.strip(), recipes_dir=tmp_path)
    assert card is not None

    result = subprocess.run(
        [
            sys.executable,
            str(
                Path(__file__).resolve().parent.parent.parent.parent
                / "scripts"
                / "recipe_contract_freshness.py"
            ),
            str(recipe_file),
        ],
        capture_output=True,
        text=True,
        cwd=tmp_path,
    )
    assert result.returncode == 0, f"Expected zero exit for fresh contract, got: {result.stderr}"


def test_hook_passes_when_no_contract_exists_but_no_recipe_changed(tmp_path: Path) -> None:
    """Hook should pass when no recipe files are passed (no recipe changes to check)."""
    result = subprocess.run(
        [
            sys.executable,
            str(
                Path(__file__).resolve().parent.parent.parent.parent
                / "scripts"
                / "recipe_contract_freshness.py"
            ),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"Expected zero exit with no args, got: {result.stderr}"
