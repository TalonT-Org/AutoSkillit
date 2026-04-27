"""REQ-C8-01 / C2-01: API surface alignment tests."""

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


def test_output_path_tokens_contains_all_file_path_contract_outputs() -> None:
    from autoskillit.execution.headless import (
        _INTENTIONALLY_EXCLUDED_PATH_TOKENS,
        _OUTPUT_PATH_TOKENS,
    )
    from autoskillit.recipe.contracts import load_bundled_manifest

    manifest = load_bundled_manifest()
    declared_path_tokens = {
        out["name"]
        for skill_data in manifest.get("skills", {}).values()
        for out in skill_data.get("outputs", [])
        if isinstance(out, dict) and out.get("type", "").startswith("file_path")
    }
    untracked = declared_path_tokens - _OUTPUT_PATH_TOKENS - _INTENTIONALLY_EXCLUDED_PATH_TOKENS
    assert not untracked, (
        f"These path tokens are declared in skill_contracts.yaml but missing from "
        f"_OUTPUT_PATH_TOKENS or _INTENTIONALLY_EXCLUDED_PATH_TOKENS: {untracked}"
    )
