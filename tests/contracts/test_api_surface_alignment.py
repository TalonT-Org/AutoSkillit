"""REQ-C8-01 / C8-02 / C8-03 / C2-01: API surface alignment tests."""
from __future__ import annotations

import inspect


# ── REQ-C8-01: recipe gateway exports schema types ──────────────────────────

def test_recipe_init_exports_recipe_ingredient():
    from autoskillit.recipe import RecipeIngredient
    assert inspect.isclass(RecipeIngredient)


def test_recipe_init_exports_autoskillit_version_key():
    from autoskillit.recipe import AUTOSKILLIT_VERSION_KEY
    assert isinstance(AUTOSKILLIT_VERSION_KEY, str)
    assert AUTOSKILLIT_VERSION_KEY == "autoskillit_version"


def test_recipe_init_exports_step_result_condition():
    from autoskillit.recipe import StepResultCondition
    assert inspect.isclass(StepResultCondition)


def test_recipe_init_exports_step_result_route():
    from autoskillit.recipe import StepResultRoute
    assert inspect.isclass(StepResultRoute)


def test_recipe_init_exports_dataflow_report():
    from autoskillit.recipe import DataFlowReport
    assert inspect.isclass(DataFlowReport)


def test_recipe_all_contains_schema_symbols():
    import autoskillit.recipe as m
    for name in (
        "RecipeIngredient",
        "AUTOSKILLIT_VERSION_KEY",
        "StepResultCondition",
        "StepResultRoute",
        "DataFlowReport",
    ):
        assert name in m.__all__, f"'{name}' missing from recipe.__all__"


# ── REQ-C8-02: core gateway exports dump_yaml ────────────────────────────────

def test_core_init_exports_dump_yaml():
    from autoskillit.core import dump_yaml
    import inspect
    assert callable(dump_yaml)


def test_core_all_contains_dump_yaml():
    import autoskillit.core as m
    assert "dump_yaml" in m.__all__


# ── REQ-C8-03: pipeline gateway exports fidelity helpers ─────────────────────

def test_pipeline_init_exports_extract_linked_issues():
    from autoskillit.pipeline import extract_linked_issues
    assert callable(extract_linked_issues)


def test_pipeline_init_exports_is_valid_fidelity_finding():
    from autoskillit.pipeline import is_valid_fidelity_finding
    assert callable(is_valid_fidelity_finding)


def test_pipeline_all_contains_fidelity_helpers():
    import autoskillit.pipeline as m
    assert "extract_linked_issues" in m.__all__
    assert "is_valid_fidelity_finding" in m.__all__


# ── REQ-C2-01: SubprocessRunner Protocol pty_mode default ────────────────────

def test_subprocess_runner_pty_mode_default_is_false():
    """SubprocessRunner Protocol pty_mode default must be False, not True."""
    import inspect
    from autoskillit.core import SubprocessRunner

    sig = inspect.signature(SubprocessRunner.__call__)
    param = sig.parameters.get("pty_mode")
    assert param is not None, "SubprocessRunner.__call__ must have pty_mode parameter"
    assert param.default is False, (
        f"SubprocessRunner.pty_mode default must be False, got {param.default!r}"
    )
