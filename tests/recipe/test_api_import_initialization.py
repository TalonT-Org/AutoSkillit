"""Cold-import coverage for the extracted recipe API packages."""

from __future__ import annotations

import subprocess
import sys

import pytest

from tests.conftest import production_interpreter_env

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.medium]


_COLD_IMPORT_CHECK = """
import autoskillit.recipe as recipe

legacy_modules = (
    "_api",
    "_api_cache",
    "_api_listing",
    "_api_orchestration",
    "_api_orchestration_assemble",
    "_api_orchestration_cache",
    "_api_orchestration_match",
    "_api_orchestration_parse",
    "_api_orchestration_text",
    "_api_orchestration_types",
    "_api_orchestration_validate",
    "api",
    "api_orchestration",
)
assert set(legacy_modules) <= vars(recipe).keys()

api = recipe.api
orchestration = recipe.api_orchestration
api_before_dir = set(vars(api))
orchestration_before_dir = set(vars(orchestration))
assert set(api.__all__) | set(api._LAZY_MODULES) <= set(dir(api))
assert set(orchestration.__all__) | set(orchestration._LAZY_MODULES) <= set(dir(orchestration))
assert set(vars(api)) == api_before_dir
assert set(vars(orchestration)) == orchestration_before_dir

from autoskillit.recipe.api import load_and_validate as api_load_and_validate
from autoskillit.recipe.api_orchestration import (
    load_and_validate as orchestration_load_and_validate,
)
from autoskillit.recipe.api._api import (
    load_and_validate as canonical_api_load_and_validate,
)
from autoskillit.recipe.api_orchestration import _api_orchestration as dispatcher
from autoskillit.recipe.api_orchestration import _api_orchestration_cache as cache_shard

assert api_load_and_validate is dispatcher.load_and_validate
assert orchestration_load_and_validate is dispatcher.load_and_validate
assert canonical_api_load_and_validate is dispatcher.load_and_validate
assert recipe.load_and_validate is dispatcher.load_and_validate
assert dispatcher.load_and_validate.__module__ == (
    "autoskillit.recipe.api_orchestration._api_orchestration"
)
assert cache_shard._orch is dispatcher
"""


def test_recipe_api_packages_initialize_in_cold_interpreter() -> None:
    result = subprocess.run(
        [sys.executable, "-c", _COLD_IMPORT_CHECK],
        capture_output=True,
        env=production_interpreter_env(),
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr
