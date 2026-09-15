"""Contract tests for recipe-path validation result variants."""

from __future__ import annotations

import inspect
import os
import shutil
import subprocess
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any, Literal, get_type_hints

import pytest

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.medium]


class _MappingOnly(Mapping[str, object]):
    """A Mapping deliberately not implemented as a dict."""

    def __init__(self, values: dict[str, object]) -> None:
        self._values = values

    def __getitem__(self, key: str) -> object:
        return self._values[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._values)

    def __len__(self) -> int:
        return len(self._values)


class _EmptyLister:
    def list_all(self) -> list[Any]:
        return []


def _completed_report(valid: bool = True) -> dict[str, object]:
    return {
        "valid": valid,
        "errors": ["structural error"],
        "quality": {"reachable": 1},
        "findings": [{"rule": "example", "message": "finding"}],
        "contracts": [{"step": 42}],
    }


def test_recipe_path_validation_typed_dicts_have_exact_required_keys_and_annotations() -> None:
    from autoskillit.core import (
        RecipePathValidationErrorFinding,
        RecipePathValidationInputError,
        RecipePathValidationReport,
    )

    assert RecipePathValidationErrorFinding.__required_keys__ == frozenset({"error"})
    assert get_type_hints(RecipePathValidationErrorFinding) == {"error": str}

    assert RecipePathValidationInputError.__required_keys__ == frozenset({"valid", "findings"})
    assert get_type_hints(RecipePathValidationInputError) == {
        "valid": Literal[False],
        "findings": list[RecipePathValidationErrorFinding],
    }

    assert RecipePathValidationReport.__required_keys__ == frozenset(
        {"valid", "errors", "quality", "findings", "contracts"}
    )
    assert get_type_hints(RecipePathValidationReport) == {
        "valid": bool,
        "errors": list[str],
        "quality": dict[str, object],
        "findings": list[dict[str, str]],
        "contracts": list[dict[str, Any]],
    }


def test_validate_from_path_annotations_are_consistent_across_implementations() -> None:
    from autoskillit.core import RecipePathValidationResult, RecipeRepository
    from autoskillit.recipe.api._api_listing import validate_from_path
    from autoskillit.recipe.repository import DefaultRecipeRepository
    from tests.fakes import InMemoryRecipeRepository

    assert get_type_hints(validate_from_path)["return"] is RecipePathValidationResult
    assert (
        get_type_hints(RecipeRepository.validate_from_path)["return"] is RecipePathValidationResult
    )
    assert (
        get_type_hints(InMemoryRecipeRepository.validate_from_path)["return"]
        is RecipePathValidationResult
    )
    assert (
        inspect.signature(DefaultRecipeRepository.validate_from_path).return_annotation
        == "RecipePathValidationResult"
    )


def test_core_gateway_exports_recipe_path_validation_contracts() -> None:
    import autoskillit.core as core
    from autoskillit.core.types._type_results_records import (
        RecipePathValidationErrorFinding,
        RecipePathValidationInputError,
        RecipePathValidationReport,
        RecipePathValidationResult,
        is_recipe_path_validation_report,
    )

    expected = {
        "RecipePathValidationErrorFinding": RecipePathValidationErrorFinding,
        "RecipePathValidationInputError": RecipePathValidationInputError,
        "RecipePathValidationReport": RecipePathValidationReport,
        "RecipePathValidationResult": RecipePathValidationResult,
        "is_recipe_path_validation_report": is_recipe_path_validation_report,
    }
    assert expected.keys() <= set(core.__all__)
    for name, value in expected.items():
        assert getattr(core, name) is value


@pytest.mark.parametrize("valid", [True, False])
def test_recipe_path_validation_report_guard_accepts_complete_reports(valid: bool) -> None:
    from autoskillit.core import is_recipe_path_validation_report

    assert is_recipe_path_validation_report(_completed_report(valid))


@pytest.mark.parametrize(
    "result",
    [
        {"valid": False, "findings": [{"error": "YAML parse error"}]},
        {
            "valid": True,
            "errors": [],
            "quality": {},
            "findings": [],
        },
        {
            "valid": True,
            "errors": [],
            "quality": {},
            "findings": [],
            "contracts": [],
            "unexpected": None,
        },
        _MappingOnly(_completed_report()),
    ],
)
def test_recipe_path_validation_report_guard_rejects_wrong_top_level_shape(
    result: object,
) -> None:
    from autoskillit.core import is_recipe_path_validation_report

    assert not is_recipe_path_validation_report(result)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("valid", "true"),
        ("errors", [42]),
        ("quality", []),
        ("quality", {42: "not a string key"}),
        ("findings", "not a list"),
        ("contracts", "not a list"),
        ("findings", [{"rule": 42}]),
        ("contracts", [{42: "not a string key"}]),
        ("quality", _MappingOnly({"reachable": 1})),
        ("findings", [_MappingOnly({"rule": "example"})]),
        ("contracts", [_MappingOnly({"step": 42})]),
    ],
)
def test_recipe_path_validation_report_guard_rejects_malformed_fields(
    field: str,
    value: object,
) -> None:
    from autoskillit.core import is_recipe_path_validation_report

    result = _completed_report()
    result[field] = value

    assert not is_recipe_path_validation_report(result)


_VALID_RECIPE = """\
name: validation-result-valid
description: A valid recipe for validation result testing.
kitchen_rules:
  - Use the terminal stop step to complete the recipe.
ingredients:
  task:
    description: A task ingredient.
    required: true
    default: test task
steps:
  done:
    action: stop
    message: Recipe validation completed successfully.
"""
_PARSED_INVALID_RECIPE = """\
name: validation-result-invalid
description: A parsed but structurally invalid recipe.
steps: {}
"""


@pytest.mark.parametrize(
    ("content", "expected_valid"),
    [(_VALID_RECIPE, True), (_PARSED_INVALID_RECIPE, False)],
    ids=["valid", "parsed-invalid"],
)
def test_validate_from_path_completed_reports_have_the_declared_shape(
    tmp_path: Path,
    content: str,
    expected_valid: bool,
) -> None:
    from autoskillit.core import is_recipe_path_validation_report
    from autoskillit.recipe.api._api_listing import validate_from_path

    path = tmp_path / "recipe.yaml"
    path.write_text(content, encoding="utf-8")

    result = validate_from_path(path, lister=_EmptyLister())

    assert is_recipe_path_validation_report(result)
    assert set(result) == {"valid", "errors", "quality", "findings", "contracts"}
    assert result["valid"] is expected_valid
    assert isinstance(result["errors"], list)
    assert all(isinstance(error, str) for error in result["errors"])
    assert isinstance(result["quality"], dict)
    assert all(isinstance(key, str) for key in result["quality"])
    assert isinstance(result["findings"], list)
    assert all(
        isinstance(finding, dict)
        and all(isinstance(key, str) and isinstance(value, str) for key, value in finding.items())
        for finding in result["findings"]
    )
    assert isinstance(result["contracts"], list)
    assert all(
        isinstance(contract, dict) and all(isinstance(key, str) for key in contract)
        for contract in result["contracts"]
    )


def test_mypy_accepts_recipe_path_validation_result_narrowing(tmp_path: Path) -> None:
    mypy_path = shutil.which("mypy")
    if mypy_path is None:
        pytest.skip("mypy not on PATH")

    snippet = """
from pathlib import Path
from typing import Any, Literal, assert_type

from autoskillit.core import (
    RecipePathValidationInputError,
    RecipePathValidationReport,
    RecipePathValidationResult,
    is_recipe_path_validation_report,
)
from autoskillit.recipe.repository import DefaultRecipeRepository


def examine(
    path: Path,
    early_error: RecipePathValidationInputError,
) -> None:
    result = DefaultRecipeRepository().validate_from_path(path)
    assert_type(result, RecipePathValidationResult)
    if is_recipe_path_validation_report(result):
        assert_type(result, RecipePathValidationReport)
        assert_type(result["valid"], bool)
        assert_type(result["errors"], list[str])
        assert_type(result["quality"], dict[str, object])
        assert_type(result["findings"], list[dict[str, str]])
        assert_type(result["contracts"], list[dict[str, Any]])
    assert_type(early_error["valid"], Literal[False])
"""
    snippet_path = tmp_path / "recipe_path_validation_result_narrowing.py"
    snippet_path.write_text(snippet, encoding="utf-8")

    src_dir = Path(__file__).resolve().parents[2] / "src"
    env = {**os.environ, "MYPYPATH": str(src_dir)}
    result = subprocess.run(
        [
            mypy_path,
            "--ignore-missing-imports",
            "--no-color-output",
            "--cache-dir",
            str(tmp_path / ".mypy_cache"),
            str(snippet_path),
        ],
        capture_output=True,
        text=True,
        env=env,
    )

    assert result.returncode == 0, (
        f"mypy unexpectedly rejected recipe path validation narrowing:\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
