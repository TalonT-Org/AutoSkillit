"""Shared helpers for run_skill input-contract and dry-walkthrough tests.

Centralizes deterministic-UUID fixtures (consumed by dry-walkthrough and CWD
tests) and contract-spec lookup tables (consumed by real-contracts and
contract-validation tests). Lives under ``tests/server/`` with a leading
underscore to mark it as test-internal (not collected by pytest).

YAML path resolution: ``Path(__file__).parents[2] / "src/autoskillit/recipe/skill_contracts.yaml"``
— ``tests/server/`` -> ``tests/`` -> repo root -> ``src/...``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

# Deterministic UUID for tests that need to predict the per-invocation marker.
_DETERMINISTIC_HEX = "a1b2c3d4e5f6a7b890123456"
_DETERMINISTIC_MARKER = f"%%ORDER_UP::{_DETERMINISTIC_HEX[:8]}%%"


class _FixedUUID:
    hex: str = _DETERMINISTIC_HEX


def _patch_uuid4(monkeypatch: pytest.MonkeyPatch) -> None:
    """Monkeypatch uuid4 to return a deterministic value for marker prediction."""
    monkeypatch.setattr("uuid.uuid4", lambda: _FixedUUID())


def _make_input_contract_resolver() -> object:
    """Create a concrete InputContractResolver using the bundled manifest."""
    from autoskillit.recipe._contracts_manifest import resolve_input_specs

    return resolve_input_specs


def _collect_all_path_input_specs() -> list[tuple[str, str, str]]:
    from autoskillit.core.io import load_yaml

    yaml_path = Path(__file__).parents[2] / "src/autoskillit/recipe/skill_contracts.yaml"
    raw = load_yaml(yaml_path)
    result = []
    for skill_name, contract in sorted(raw.get("skills", {}).items()):
        for inp in contract.get("inputs", []):
            if inp.get("type") in ("file_path", "directory_path", "file_path_list"):
                result.append((skill_name, inp["name"], inp["type"]))
    return result


_ALL_PATH_INPUT_SPECS = _collect_all_path_input_specs()
assert _ALL_PATH_INPUT_SPECS, (
    "skill_contracts.yaml yielded no path-typed inputs — either the manifest "
    "relocated, lost its `skills:` key, or dropped all file_path/directory_path/"
    "file_path_list entries. Parametrized suites would collapse to zero cases."
)


def _collect_file_path_list_specs() -> list[tuple[str, str, str]]:
    return [(s, i, t) for (s, i, t) in _ALL_PATH_INPUT_SPECS if t == "file_path_list"]


_FILE_PATH_LIST_SPECS = _collect_file_path_list_specs()
assert _FILE_PATH_LIST_SPECS, (
    "skill_contracts.yaml yielded no file_path_list inputs — the parametrized "
    "file_path_list suite would collapse to zero cases."
)
