"""Script gates must fail when their input universe has no entries."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

pytestmark = pytest.mark.small

_SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"

# Cache script-module loads so repeated parametrizations don't re-execute
# module-level code. Side effects (atexit, sys.path mutation, signal handlers)
# would otherwise accumulate across parametrizations within a worker.
_SCRIPT_CACHE: dict[str, ModuleType] = {}


def _load_script(name: str) -> ModuleType:
    cached = _SCRIPT_CACHE.get(name)
    if cached is not None:
        return cached
    script_path = _SCRIPTS_DIR / name
    spec = importlib.util.spec_from_file_location(script_path.stem, script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    _SCRIPT_CACHE[name] = module
    return module


@pytest.mark.parametrize(
    (
        "script_name",
        "root_attribute",
        "entrypoint",
        # True → entrypoint returns a list[str] of violations (pyi pair).
        # False → entrypoint returns an exit code and writes to stderr (recipe pair).
        # The split reflects the intentional script-pair divergence documented
        # in scripts/check_pyi_stub_format.py and scripts/compile_recipes.py.
        "returns_violations",
        "cli_args",
        "expected_message_substring",
    ),
    [
        ("check_pyi_stub_format.py", "SRC_ROOT", "check", True, (), "no __init__.pyi"),
        ("check_pyi_stub_symbols.py", "SRC_ROOT", "check", True, (), "no __init__.pyi"),
        ("check_contract_freshness.py", "RECIPES_DIR", "main", False, (), "no recipe YAML"),
        ("compile_recipes.py", "RECIPES_DIR", "main", False, ("--check",), "no recipe YAML"),
    ],
    ids=["stub-format", "stub-symbols", "contract-freshness", "recipe-compiler"],
)
def test_script_gate_fails_for_an_empty_universe(
    script_name: str,
    root_attribute: str,
    entrypoint: str,
    returns_violations: bool,
    cli_args: tuple[str, ...],
    expected_message_substring: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _load_script(script_name)
    monkeypatch.setattr(module, root_attribute, tmp_path)
    if cli_args:
        monkeypatch.setattr(sys, "argv", [script_name, *cli_args])

    if returns_violations:
        result = getattr(module, entrypoint)()
        assert result, f"{script_name} passed with no files to check"
        # Lock the contract: the violation message must identify the empty universe.
        assert any(expected_message_substring in line for line in result), (
            f"{script_name} returned violations but none identified the "
            f"empty-universe condition '{expected_message_substring}': {result!r}"
        )
    else:
        result = getattr(module, entrypoint)()
        assert result == 1, f"{script_name} passed with no files to check"
        captured = capsys.readouterr()
        stderr = captured.err
        assert expected_message_substring in stderr, (
            f"{script_name} exited 1 but stderr did not identify the "
            f"empty-universe condition '{expected_message_substring}': "
            f"stderr={stderr!r}"
        )
