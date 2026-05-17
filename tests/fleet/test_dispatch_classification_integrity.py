"""AST-based structural test: DispatchRecord(..., status=...) must route through classifier."""

from __future__ import annotations

import ast
import pathlib

import pytest

pytestmark = [pytest.mark.layer("fleet"), pytest.mark.small, pytest.mark.feature("fleet")]


def _is_dispatch_record_call(node: ast.Call) -> bool:
    if isinstance(node.func, ast.Name) and node.func.id == "DispatchRecord":
        return True
    if isinstance(node.func, ast.Attribute) and node.func.attr == "DispatchRecord":
        return True
    return False


class TestDispatchClassificationIntegrity:
    def test_no_hardcoded_dispatch_status_in_api_outside_classifier(self):
        """DispatchRecord(status=DispatchStatus.X) must never use a hardcoded enum member.

        The status= value must always come from classify_dispatch_outcome's return value
        (i.e., a Name node like `final_status`), not a direct Attribute access like
        `DispatchStatus.FAILURE`. This prevents bypass of the classifier.

        Allowed: status=final_status (variable from classifier)
        Forbidden: status=DispatchStatus.FAILURE (hardcoded bypass)
        """
        source = (pathlib.Path(__file__).parents[2] / "src/autoskillit/fleet/_api.py").read_text()
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and _is_dispatch_record_call(node):
                for kw in node.keywords:
                    if kw.arg == "status":
                        # An Attribute node means DispatchStatus.SOMETHING — a hardcoded bypass
                        if isinstance(kw.value, ast.Attribute):
                            assert False, (
                                f"Direct DispatchStatus enum assignment at line {node.lineno} "
                                f"bypasses classify_dispatch_outcome"
                            )
