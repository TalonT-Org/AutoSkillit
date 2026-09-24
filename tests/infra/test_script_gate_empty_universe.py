"""Script gates must fail when their input universe has no entries."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

pytestmark = pytest.mark.small

_SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"


def _load_script(name: str) -> ModuleType:
    script_path = _SCRIPTS_DIR / name
    spec = importlib.util.spec_from_file_location(script_path.stem, script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    ("script_name", "root_attribute", "entrypoint", "returns_violations"),
    [
        ("check_pyi_stub_format.py", "SRC_ROOT", "check", True),
        ("check_pyi_stub_symbols.py", "SRC_ROOT", "check", True),
        ("check_contract_freshness.py", "RECIPES_DIR", "main", False),
        ("compile_recipes.py", "RECIPES_DIR", "main", False),
    ],
    ids=["stub-format", "stub-symbols", "contract-freshness", "recipe-compiler"],
)
def test_script_gate_fails_for_an_empty_universe(
    script_name: str,
    root_attribute: str,
    entrypoint: str,
    returns_violations: bool,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_script(script_name)
    monkeypatch.setattr(module, root_attribute, tmp_path)
    if script_name == "compile_recipes.py":
        monkeypatch.setattr(sys, "argv", [script_name, "--check"])

    result = getattr(module, entrypoint)()

    if returns_violations:
        assert result, f"{script_name} passed with no files to check"
    else:
        assert result == 1, f"{script_name} passed with no files to check"
