"""Only the materializer may produce a plan-set authority; only core may decode it."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

_ROOT = Path(__file__).resolve().parents[2]
_SOURCE = _ROOT / "src" / "autoskillit"
_MATERIALIZER = "src/autoskillit/server/_plan_set_materializer.py"
_VERIFIER = "src/autoskillit/core/planset/verifier.py"


def _sites(source: str, path: str) -> set[tuple[str, str]]:
    tree = ast.parse(source, filename=path)
    has_plan_set_authority = any(
        isinstance(node, ast.Name) and node.id == "PlanSetAuthority" for node in ast.walk(tree)
    )
    sites: set[tuple[str, str]] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id == "PlanSetAuthority":
                sites.add(("constructor", path))
            elif (
                isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "PlanSetAuthority"
                and node.func.attr in {"create", "from_dict"}
            ):
                sites.add((node.func.attr, path))
        elif (
            has_plan_set_authority
            and isinstance(node, ast.Attribute)
            and node.attr == "canonical_bytes"
        ):
            sites.add(("canonical_bytes", path))
    return sites


def _production_sites() -> set[tuple[str, str]]:
    return {
        site
        for path in _SOURCE.rglob("*.py")
        for site in _sites(path.read_text(), str(path.relative_to(_ROOT)))
    }


def _assert_sanctioned(sites: set[tuple[str, str]]) -> None:
    assert {path for operation, path in sites if operation == "create"} == {_MATERIALIZER}
    assert {path for operation, path in sites if operation == "from_dict"} == {_VERIFIER}
    assert not {path for operation, path in sites if operation == "constructor"}
    assert {path for operation, path in sites if operation == "canonical_bytes"} <= {
        _MATERIALIZER,
        _VERIFIER,
    }


def test_only_sanctioned_modules_construct_or_decode_plan_set_authority() -> None:
    _assert_sanctioned(_production_sites())


def test_synthetic_alternate_producer_is_rejected() -> None:
    forged = _sites(
        "PlanSetAuthority.create()\nPlanSetAuthority.from_dict({})\n"
        "PlanSetAuthority()\nauthority.canonical_bytes\n",
        "src/autoskillit/server/tools/forged.py",
    )
    with pytest.raises(AssertionError):
        _assert_sanctioned(_production_sites() | forged)
