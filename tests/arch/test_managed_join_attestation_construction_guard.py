"""Keep managed join attestations behind the server issuer."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

_ROOT = Path(__file__).resolve().parents[2]
_ALLOWED = {
    "src/autoskillit/core/types/_type_skill_semantics.py",
    "src/autoskillit/server/_managed_join_attestation.py",
    "src/autoskillit/server/managed_join_prelaunch.py",
    "tests/fakes.py",
    "tests/server/_managed_join_fixtures.py",
    "tests/server/test_managed_join_record_store.py",
    "tests/server/test_write_managed_parent_binding.py",
}


def test_attestations_are_constructed_only_by_the_authority_and_test_fake() -> None:
    actual = set()
    for root in (_ROOT / "src", _ROOT / "tests"):
        for path in root.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            if any(
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "ManagedJoinAttestation"
                for node in ast.walk(tree)
            ):
                actual.add(path.relative_to(_ROOT).as_posix())
    assert actual <= _ALLOWED
    assert {
        "src/autoskillit/server/_managed_join_attestation.py",
        "tests/fakes.py",
    } <= actual
