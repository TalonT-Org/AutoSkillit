"""A response projection must fail closed when its byte count cannot settle."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from autoskillit.server.response._response_budget import _spill
from autoskillit.server.response._response_budget._primitives import (
    RESPONSE_SPILL_METADATA_KEY,
    _ProjectionNonconvergentError,
)

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


def test_projection_raises_for_missing_metadata() -> None:
    with pytest.raises(_ProjectionNonconvergentError, match="projected byte field"):
        _spill._finalize_envelope({"success": False}, max_bytes=100)


def test_projection_raises_when_byte_count_never_converges(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    envelope = {RESPONSE_SPILL_METADATA_KEY: {"projected_utf8_bytes": 0}}

    def oscillating_json(value: dict[str, object]) -> str:
        projected = value[RESPONSE_SPILL_METADATA_KEY]["projected_utf8_bytes"]  # type: ignore[index]
        return "x" * (1 if projected == 2 else 2)

    monkeypatch.setattr(_spill, "_canonical_json", oscillating_json)
    with pytest.raises(_ProjectionNonconvergentError, match="did not converge"):
        _spill._finalize_envelope(envelope, max_bytes=1)


def test_both_projection_raise_sites_remain_present() -> None:
    source = Path(_spill.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    finalizer = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_finalize_envelope"
    )
    failures = [
        node
        for node in ast.walk(finalizer)
        if isinstance(node, ast.Raise)
        and isinstance(node.exc, ast.Call)
        and isinstance(node.exc.func, ast.Name)
        and node.exc.func.id == "_ProjectionNonconvergentError"
    ]
    assert len(failures) == 2
