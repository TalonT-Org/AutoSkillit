"""AST guard: detect_model_drift must use normalize_model_id and _models_match.

profile_name suppression guard must call _is_non_anthropic exactly once, on observed_model only.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

SRC = Path(__file__).resolve().parents[2] / "src" / "autoskillit"
ANOMALY_DETECTION = SRC / "execution" / "anomaly_detection.py"
SESSION_LOG = SRC / "execution" / "session_log.py"


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


def test_drift_call_site_uses_independent_observed_source():
    """_observed passed to detect_model_drift must come from _primary_model_identifier.

    Prevents regression where the call site collapses both arguments to the same
    source (model_identifier), causing detect_model_drift(X, X) which can never
    detect drift.
    """
    source = SESSION_LOG.read_text()
    tree = ast.parse(source)

    flush_func = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "flush_session_log":
            flush_func = node
            break
    assert flush_func is not None, "flush_session_log not found in session_log.py"

    observed_var_name = None
    for node in ast.walk(flush_func):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "detect_model_drift"
        ):
            assert len(node.args) >= 2, (
                "detect_model_drift call must have at least 2 positional args"
            )
            second_arg = node.args[1]
            assert isinstance(second_arg, ast.Name), (
                "second arg to detect_model_drift must be a Name node"
            )
            observed_var_name = second_arg.id
            break

    assert observed_var_name is not None, "detect_model_drift call not found in flush_session_log"

    for node in ast.walk(flush_func):
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            rhs = node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            target = node.target
            rhs = node.value
        else:
            continue

        if not (isinstance(target, ast.Name) and target.id == observed_var_name):
            continue

        rhs_dump = ast.dump(rhs)
        assert "_primary_model_identifier" in rhs_dump, (
            f"{observed_var_name} must be assigned from _primary_model_identifier() — "
            "the observed model must come from token_usage, not from the configured model"
        )
        for ifexp in ast.walk(rhs):
            if isinstance(ifexp, ast.IfExp):
                for branch in (ifexp.body, ifexp.orelse):
                    assert not (
                        isinstance(branch, ast.Name) and branch.id == "model_identifier"
                    ), (
                        f"{observed_var_name} must not be assigned from model_identifier "
                        "in any ternary branch — this collapses configured and observed to "
                        "the same source and permanently disables drift detection"
                    )
        return

    pytest.fail(f"No assignment to {observed_var_name!r} found in flush_session_log body")


def test_detect_model_drift_has_profile_suppression_guard():
    """detect_model_drift must contain a profile_name suppression check — AST enforcement."""
    source = ANOMALY_DETECTION.read_text()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "detect_model_drift":
            body_src = ast.dump(node)
            assert "profile_name" in body_src, (
                "detect_model_drift must reference profile_name in its body — "
                "profile-routed sessions require suppression to prevent false MODEL_DRIFT"
            )
            for child in ast.walk(node):
                if isinstance(child, ast.If):
                    test_src = ast.dump(child.test)
                    if "profile_name" in test_src:
                        assert "normalize_model_id" in test_src, (
                            "profile_name suppression guard must normalize observed_model "
                            "before calling _is_non_anthropic — raw-string check misclassifies "
                            "Anthropic short aliases ('sonnet', 'opus', 'haiku') as non-Anthropic"
                        )
                        non_anthropic_count = test_src.count("_is_non_anthropic")
                        assert non_anthropic_count == 1, (
                            f"profile_name suppression guard calls _is_non_anthropic "
                            f"{non_anthropic_count} times — must be exactly 1 (on observed_model "
                            f"only). Checking configured_model makes the guard dead for the "
                            f"standard production case (Anthropic alias configured + "
                            f"non-Anthropic observed via profile routing)"
                        )
                        assert "observed_model" in test_src, (
                            "profile_name suppression guard must call _is_non_anthropic "
                            "on observed_model, not configured_model — checking configured_model "
                            "reintroduces the production-blindness bug since configured_model "
                            "is always a Claude alias in production"
                        )
                        return
            pytest.fail(
                "detect_model_drift references profile_name but has no If guard on it — "
                "the parameter must be used for suppression, not just recording"
            )
    pytest.fail("detect_model_drift not found in anomaly_detection.py")
