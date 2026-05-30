"""AST guard: detect_model_drift must use normalize_model_id and _models_match.

Raw string comparison between the alias domain (config) and the full-ID domain
(API response) is a structural false-positive source. This guard prevents
regression where someone modifies detect_model_drift without going through
normalization and prefix matching.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

SRC = Path(__file__).resolve().parents[2] / "src" / "autoskillit"
ANOMALY_DETECTION = SRC / "execution" / "anomaly_detection.py"


def test_detect_model_drift_uses_normalize_model_id():
    """detect_model_drift must normalize both operands — AST enforcement."""
    source = ANOMALY_DETECTION.read_text()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "detect_model_drift":
            body_src = ast.dump(node)
            assert "normalize_model_id" in body_src, (
                "detect_model_drift must call normalize_model_id — "
                "raw string comparison between alias and full-ID domains is a false-positive"
            )
            assert "_models_match" in body_src, (
                "detect_model_drift must use _models_match for prefix-aware comparison — "
                "strict equality after normalization fails for alias-to-full-ID pairs"
            )
            return
    pytest.fail("detect_model_drift not found in anomaly_detection.py")
