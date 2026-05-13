"""Tests for recipe_contract_freshness pre-commit script."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from autoskillit.recipe.contracts import generate_recipe_card

_SCRIPT = (
    Path(__file__).resolve().parent.parent.parent / "scripts" / "recipe_contract_freshness.py"
)

pytestmark = [
    pytest.mark.layer("hooks"),
    pytest.mark.medium,
    pytest.mark.skipif(not _SCRIPT.exists(), reason=f"Script not found: {_SCRIPT}"),
]


def test_hook_detects_stale_contract(tmp_path: Path) -> None:
    """Pre-commit script should fail when a recipe's contract is stale."""
    recipe_content = """\
name: test-freshness
recipe_version: "1.0.0"
ingredients:
  task:
    description: A test task
    required: true
steps:
  test_step:
    tool: run_cmd
    with:
      cmd: echo test
    on_success: done
    on_failure: done
  done:
    action: stop
    message: Done
"""
    recipe_file = tmp_path / "test-recipe.yaml"
    recipe_file.write_text(recipe_content)

    contracts_dir = tmp_path / "contracts"
    contracts_dir.mkdir()
    (contracts_dir / "test-recipe.yaml").write_text(
        "schema_version: '1.0'\npipeline_hash: old_hash\nstep_fields_hash: old\n"
        "block_fingerprints: {}\nskill_hashes: {}\n"
    )

    result = subprocess.run(
        [sys.executable, str(_SCRIPT), str(recipe_file)],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0, (
        f"Expected non-zero exit for stale contract, got {result.returncode}\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert "stale" in result.stderr, (
        f"Expected 'stale' in stderr for contract staleness, got:\n{result.stderr}"
    )


def test_hook_passes_when_contract_is_fresh(tmp_path: Path) -> None:
    """Pre-commit script should pass when the contract is up-to-date."""
    recipe_content = """\
name: test-freshness
recipe_version: "1.0.0"
ingredients:
  task:
    description: A test task
    required: true
steps:
  test_step:
    tool: run_cmd
    with:
      cmd: echo test
    on_success: done
    on_failure: done
  done:
    action: stop
    message: Done
"""
    recipe_file = tmp_path / "test-recipe.yaml"
    recipe_file.write_text(recipe_content)

    contracts_dir = tmp_path / "contracts"
    contracts_dir.mkdir()

    generate_recipe_card(recipe_file, recipes_dir=tmp_path)

    result = subprocess.run(
        [sys.executable, str(_SCRIPT), str(recipe_file)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"Expected zero exit for fresh contract, got: {result.returncode}\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )


def test_hook_passes_when_no_recipe_args() -> None:
    """Hook should pass when no recipe files are passed (no recipe changes to check)."""
    result = subprocess.run(
        [sys.executable, str(_SCRIPT)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"Expected zero exit with no args, got: {result.stderr}"


def test_hook_fails_when_no_contract_exists(tmp_path: Path) -> None:
    """Hook should fail when a recipe is passed but has no corresponding contract."""
    recipe_content = """\
name: test-no-contract
recipe_version: "1.0.0"
ingredients:
  task:
    description: A test task
    required: true
steps:
  test_step:
    tool: run_cmd
    with:
      cmd: echo test
    on_success: done
    on_failure: done
  done:
    action: stop
    message: Done
"""
    recipe_file = tmp_path / "test-no-contract.yaml"
    recipe_file.write_text(recipe_content)
    (tmp_path / "contracts").mkdir()

    result = subprocess.run(
        [sys.executable, str(_SCRIPT), str(recipe_file)],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0, (
        f"Expected non-zero exit for missing contract, got {result.returncode}\n"
        f"stderr: {result.stderr}"
    )
    assert "missing contract" in result.stderr, (
        f"Expected 'missing contract' in stderr, got:\n{result.stderr}"
    )
