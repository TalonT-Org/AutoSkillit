"""Parametrized JSON contract card freshness enforcement: contract cards must have
non-stale .json companions with content parity, and the contract collection must
not silently shrink.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from autoskillit.recipe.io import builtin_recipes_dir

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

_RECIPES_DIR = builtin_recipes_dir()
_CONTRACTS_DIR = _RECIPES_DIR / "contracts"
_CONTRACT_STEMS = (
    sorted(p.stem for p in _CONTRACTS_DIR.glob("*.yaml")) if _CONTRACTS_DIR.is_dir() else []
)

MINIMUM_CONTRACT_COUNT = 15


@pytest.mark.parametrize("stem", _CONTRACT_STEMS, ids=_CONTRACT_STEMS)
def test_json_card_exists(stem: str) -> None:
    json_path = _CONTRACTS_DIR / f"{stem}.json"
    assert json_path.is_file(), (
        f"Contract '{stem}' has a .yaml card but no .json companion. "
        f"Run 'task regen-contracts' to regenerate."
    )


@pytest.mark.parametrize("stem", _CONTRACT_STEMS, ids=_CONTRACT_STEMS)
def test_json_card_content_parity(stem: str) -> None:
    yaml_path = _CONTRACTS_DIR / f"{stem}.yaml"
    json_path = _CONTRACTS_DIR / f"{stem}.json"
    if not json_path.is_file():
        pytest.skip(f"JSON card missing for '{stem}' (covered by test_json_card_exists)")
    yaml_data = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    json_data = json.loads(json_path.read_text(encoding="utf-8"))
    assert yaml_data == json_data, (
        f"Content mismatch between '{stem}.yaml' and '{stem}.json'. "
        f"Run 'task regen-contracts' to regenerate."
    )


def test_collection_count_rot_guard() -> None:
    assert len(_CONTRACT_STEMS) >= MINIMUM_CONTRACT_COUNT, (
        f"Expected at least {MINIMUM_CONTRACT_COUNT} contract card YAMLs in "
        f"{_CONTRACTS_DIR}, found {len(_CONTRACT_STEMS)}. "
        "Is builtin_recipes_dir() resolving correctly? "
        "Run 'task regen-contracts' to regenerate."
    )


def test_no_orphan_json_cards() -> None:
    if not _CONTRACTS_DIR.is_dir():
        pytest.skip("Contracts directory does not exist")
    json_stems = {p.stem for p in _CONTRACTS_DIR.glob("*.json")}
    yaml_stems = set(_CONTRACT_STEMS)
    orphans = sorted(json_stems - yaml_stems)
    assert not orphans, (
        f"Found orphan JSON cards with no matching .yaml: {orphans}. "
        f"Remove stale .json files or run 'task regen-contracts'."
    )
