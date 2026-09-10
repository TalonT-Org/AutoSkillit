"""Canonical-import and layout guards for the issue #4673 folder decomposition.

Stage-gated: cases are added only for the current stage and previously
completed stages. Stages are ordered A (recipe) -> B (lifecycle) -> C
(response) -> D (fleet campaign state); a failure stops progression and a
later stage cannot repair an earlier broken gate.

Cold-import checks use a fresh subprocess per case, never
``importlib.reload``. ``reload`` mutates the module object in place, so
existing ``from ... import name`` bindings keep pointing at the old object,
and deleting a name from ``sys.modules`` before re-importing builds a
*second* module object that other already-imported modules do not see.
Neither reproduces a first import, so neither can prove a canonical path --
only a genuinely fresh interpreter can.
"""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

from tests._subprocess_helpers import production_interpreter_env
from tests.arch._helpers import SRC_ROOT

pytestmark = [pytest.mark.layer("arch"), pytest.mark.medium]

_COLD_IMPORT_TIMEOUT_SECONDS = 30.0


def _run_cold_import(code: str) -> dict[str, object]:
    """Run ``code`` in a fresh interpreter, bounded and with captured output."""
    env = production_interpreter_env()
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        env=env,
        timeout=_COLD_IMPORT_TIMEOUT_SECONDS,
    )
    assert result.returncode == 0, (
        f"cold-import subprocess failed (rc={result.returncode}):\n"
        f"stdout: {result.stdout[-4000:]}\nstderr: {result.stderr[-4000:]}"
    )
    last_line = result.stdout.strip().splitlines()[-1] if result.stdout.strip() else ""
    try:
        return json.loads(last_line)
    except json.JSONDecodeError:  # pragma: no cover - diagnostic path
        raise AssertionError(
            f"cold-import subprocess produced non-JSON stdout:\n{result.stdout[-4000:]}"
        ) from None


# ---------------------------------------------------------------------------
# T1 -- Stage A: canonical imports and removed old locations
# ---------------------------------------------------------------------------

_STAGE_A_SCRIPT = """
import importlib
import json

results = {}

# The new recipe-delivery module is the first real import in this process.
from autoskillit.server.recipe._recipe_delivery import finalize_recipe_delivery
results["recipe_delivery_first_import"] = finalize_recipe_delivery.__module__

# Representative real `from ... import ...` and identity check.
from autoskillit.server.recipe._recipe_artifact import (
    build_canonical_recipe_artifact_payload,
)
_artifact_mod = importlib.import_module("autoskillit.server.recipe._recipe_artifact")
results["artifact_identity_ok"] = (
    build_canonical_recipe_artifact_payload
    is getattr(_artifact_mod, "build_canonical_recipe_artifact_payload")
)

# Every relocated module resolves by its full canonical name, including the
# moved section package's descendants.
canonical_modules = [
    "autoskillit.server.recipe._recipe_artifact",
    "autoskillit.server.recipe._recipe_delivery",
    "autoskillit.server.recipe._recipe_delivery_helpers",
    "autoskillit.server.recipe._recipe_execution",
    "autoskillit.server.recipe._recipe_generation",
    "autoskillit.server.recipe._recipe_initialization",
    "autoskillit.server.recipe._recipe_section_pagination",
    "autoskillit.server.recipe._recipe_section_planning",
    "autoskillit.server.recipe._recipe_segment_delivery",
    "autoskillit.server.recipe.section",
    "autoskillit.server.recipe.section._contracts",
    "autoskillit.server.recipe.section._lifecycle",
    "autoskillit.server.recipe.section._rendering",
    "autoskillit.server.recipe.section._verification",
]
for _name in canonical_modules:
    importlib.import_module(_name)
results["canonical_imports_ok"] = True

# The interacting response-budget implementation, imported afterward.
from autoskillit.server.response._response_budget import enforce_response_budget
results["response_budget_import_ok"] = (
    enforce_response_budget.__name__ == "enforce_response_budget"
)

# Every corresponding old full module name must be gone.
old_names = [
    "autoskillit.server._recipe_artifact",
    "autoskillit.server._recipe_delivery",
    "autoskillit.server._recipe_delivery_helpers",
    "autoskillit.server._recipe_execution",
    "autoskillit.server._recipe_generation",
    "autoskillit.server._recipe_initialization",
    "autoskillit.server._recipe_section_pagination",
    "autoskillit.server._recipe_section_planning",
    "autoskillit.server._recipe_segment_delivery",
    "autoskillit.server.recipe_section",
]
old_name_results = {}
for _name in old_names:
    try:
        importlib.import_module(_name)
        old_name_results[_name] = "IMPORTED"
    except ModuleNotFoundError:
        old_name_results[_name] = "ModuleNotFoundError"
    except Exception as exc:  # pragma: no cover - diagnostic path
        old_name_results[_name] = f"{type(exc).__name__}: {exc}"
results["old_name_results"] = old_name_results

print(json.dumps(results))
"""


def test_stage_a_canonical_recipe_imports_resolve_and_old_paths_are_gone() -> None:
    """New `server/recipe/` imports resolve; every old `server/_recipe_*` path is gone."""
    results = _run_cold_import(_STAGE_A_SCRIPT)
    assert (
        results["recipe_delivery_first_import"]
        == "autoskillit.server.recipe._recipe_delivery._finalize"
    )
    assert results["artifact_identity_ok"] is True
    assert results["canonical_imports_ok"] is True
    assert results["response_budget_import_ok"] is True
    old_name_results = results["old_name_results"]
    assert isinstance(old_name_results, dict)
    not_removed = {
        name: outcome
        for name, outcome in old_name_results.items()
        if outcome != "ModuleNotFoundError"
    }
    assert not not_removed, f"old recipe module path(s) still importable: {not_removed}"


# ---------------------------------------------------------------------------
# T2 -- Stage A: layout, documentation, and real ceilings
# ---------------------------------------------------------------------------

_STAGE_A_PACKAGES: tuple[tuple[str, int], ...] = (
    ("server/recipe", 10),
    ("server/recipe/section", 10),
)


@pytest.mark.parametrize("rel_path,max_files", _STAGE_A_PACKAGES)
def test_stage_a_package_exists_within_file_limit_with_docs(rel_path: str, max_files: int) -> None:
    """Each new Stage A package exists, stays within its nested-file ceiling, and is documented.

    `server/recipe/section/` sits two directory levels below `server/` and is
    therefore reached by neither `test_server_file_count_under_limit` (root
    only) nor `test_no_subpackage_exceeds_10_files` (one level of nesting
    only) -- this parameterized case is what actually keeps it covered.
    """
    pkg_dir = SRC_ROOT / rel_path
    assert pkg_dir.is_dir(), f"{rel_path}/ does not exist"

    py_files = list(pkg_dir.glob("*.py"))
    assert len(py_files) <= max_files, (
        f"{rel_path}/ has {len(py_files)} direct Python files, max is {max_files}"
    )

    agents_md = pkg_dir / "AGENTS.md"
    assert agents_md.is_file(), f"{rel_path}/AGENTS.md is missing"
    agents_text = agents_md.read_text(encoding="utf-8")
    assert agents_text.strip(), f"{rel_path}/AGENTS.md is empty"

    claude_md = pkg_dir / "CLAUDE.md"
    assert claude_md.is_file(), f"{rel_path}/CLAUDE.md is missing"
    assert claude_md.read_text(encoding="utf-8") == "@AGENTS.md\n", (
        f"{rel_path}/CLAUDE.md must be the exact `@AGENTS.md` shim"
    )


# ---------------------------------------------------------------------------
# T1 -- Stage B: canonical imports and removed old locations
# ---------------------------------------------------------------------------

_STAGE_B_SCRIPT = """
import importlib
import json

results = {}

# The new lifecycle lifespan package is the first real import in this process.
from autoskillit.server.lifecycle._lifespan import _autoskillit_lifespan
results["lifespan_first_import"] = _autoskillit_lifespan.__module__

# Representative real `from ... import ...` and identity check.
from autoskillit.server.lifecycle._state import _get_ctx
_state_mod = importlib.import_module("autoskillit.server.lifecycle._state")
results["state_identity_ok"] = _get_ctx is getattr(_state_mod, "_get_ctx")

# Every relocated module resolves by its full canonical name, including the
# moved lifespan package's descendants.
canonical_modules = [
    "autoskillit.server.lifecycle._state",
    "autoskillit.server.lifecycle._guards",
    "autoskillit.server.lifecycle._session_type",
    "autoskillit.server.lifecycle._editable_guard",
    "autoskillit.server.lifecycle._lifespan",
    "autoskillit.server.lifecycle._lifespan._lifespan",
    "autoskillit.server.lifecycle._lifespan._session_boots",
    "autoskillit.server.lifecycle._lifespan._startup_checks",
]
for _name in canonical_modules:
    importlib.import_module(_name)
results["canonical_imports_ok"] = True

# The interacting recipe (Stage A) implementation and the response-budget
# (Stage C) implementation, imported afterward.
from autoskillit.server.recipe._recipe_artifact import (
    build_canonical_recipe_artifact_payload,
)
results["recipe_import_ok"] = (
    build_canonical_recipe_artifact_payload.__name__
    == "build_canonical_recipe_artifact_payload"
)
from autoskillit.server.response._response_budget import enforce_response_budget
results["response_budget_import_ok"] = (
    enforce_response_budget.__name__ == "enforce_response_budget"
)

# Every corresponding old full module name must be gone.
old_names = [
    "autoskillit.server._state",
    "autoskillit.server._guards",
    "autoskillit.server._session_type",
    "autoskillit.server._editable_guard",
    "autoskillit.server._lifespan",
]
old_name_results = {}
for _name in old_names:
    try:
        importlib.import_module(_name)
        old_name_results[_name] = "IMPORTED"
    except ModuleNotFoundError:
        old_name_results[_name] = "ModuleNotFoundError"
    except Exception as exc:  # pragma: no cover - diagnostic path
        old_name_results[_name] = f"{type(exc).__name__}: {exc}"
results["old_name_results"] = old_name_results

print(json.dumps(results))
"""


def test_stage_b_canonical_lifecycle_imports_resolve_and_old_paths_are_gone() -> None:
    """New `server/lifecycle/` imports resolve; every old `server/_state`-family path is gone."""
    results = _run_cold_import(_STAGE_B_SCRIPT)
    assert results["lifespan_first_import"] == "autoskillit.server.lifecycle._lifespan._lifespan"
    assert results["state_identity_ok"] is True
    assert results["canonical_imports_ok"] is True
    assert results["recipe_import_ok"] is True
    assert results["response_budget_import_ok"] is True
    old_name_results = results["old_name_results"]
    assert isinstance(old_name_results, dict)
    not_removed = {
        name: outcome
        for name, outcome in old_name_results.items()
        if outcome != "ModuleNotFoundError"
    }
    assert not not_removed, f"old lifecycle module path(s) still importable: {not_removed}"


# ---------------------------------------------------------------------------
# T2 -- Stage B: layout, documentation, and real ceilings
# ---------------------------------------------------------------------------

_STAGE_B_PACKAGES: tuple[tuple[str, int], ...] = (
    ("server/lifecycle", 10),
    ("server/lifecycle/_lifespan", 10),
)


@pytest.mark.parametrize("rel_path,max_files", _STAGE_B_PACKAGES)
def test_stage_b_package_exists_within_file_limit_with_docs(rel_path: str, max_files: int) -> None:
    """Each new Stage B package exists, stays within its nested-file ceiling, and is documented.

    `server/lifecycle/_lifespan/` sits two directory levels below `server/`
    and is underscore-prefixed, so it is reached by neither
    `test_server_file_count_under_limit` (root only) nor
    `test_no_subpackage_exceeds_10_files` (one level of nesting, non-underscore
    names only) -- this parameterized case is what actually keeps it covered.
    """
    pkg_dir = SRC_ROOT / rel_path
    assert pkg_dir.is_dir(), f"{rel_path}/ does not exist"

    py_files = list(pkg_dir.glob("*.py"))
    assert len(py_files) <= max_files, (
        f"{rel_path}/ has {len(py_files)} direct Python files, max is {max_files}"
    )

    agents_md = pkg_dir / "AGENTS.md"
    assert agents_md.is_file(), f"{rel_path}/AGENTS.md is missing"
    agents_text = agents_md.read_text(encoding="utf-8")
    assert agents_text.strip(), f"{rel_path}/AGENTS.md is empty"

    claude_md = pkg_dir / "CLAUDE.md"
    assert claude_md.is_file(), f"{rel_path}/CLAUDE.md is missing"
    assert claude_md.read_text(encoding="utf-8") == "@AGENTS.md\n", (
        f"{rel_path}/CLAUDE.md must be the exact `@AGENTS.md` shim"
    )


# ---------------------------------------------------------------------------
# T1 -- Stage C: canonical imports and removed old locations
# ---------------------------------------------------------------------------

_STAGE_C_SCRIPT = """
import importlib
import json

results = {}

# The new response-budget enforcement module is the first real import in this process.
from autoskillit.server.response._response_budget._enforce import enforce_response_budget
results["response_budget_enforce_first_import"] = enforce_response_budget.__module__

# Representative real `from ... import ...` and identity check.
from autoskillit.server.response._response_conformance import decide_response_conformance
_conformance_mod = importlib.import_module("autoskillit.server.response._response_conformance")
results["conformance_identity_ok"] = (
    decide_response_conformance is getattr(_conformance_mod, "decide_response_conformance")
)

# Every relocated module resolves by its full canonical name, including the
# moved response-budget package's descendants.
canonical_modules = [
    "autoskillit.server.response._response_conformance",
    "autoskillit.server.response._run_skill_completion",
    "autoskillit.server.response._response_budget",
    "autoskillit.server.response._response_budget._enforce",
    "autoskillit.server.response._response_budget._primitives",
    "autoskillit.server.response._response_budget._projection",
    "autoskillit.server.response._response_budget._spill",
]
for _name in canonical_modules:
    importlib.import_module(_name)
results["canonical_imports_ok"] = True

# The interacting recipe (Stage A) implementation, imported afterward -- it
# depends on response-budget for its own delivery finalization.
from autoskillit.server.recipe._recipe_delivery import finalize_recipe_delivery
results["recipe_delivery_import_ok"] = (
    finalize_recipe_delivery.__name__ == "finalize_recipe_delivery"
)

# Every corresponding old full module name must be gone.
old_names = [
    "autoskillit.server._response_conformance",
    "autoskillit.server._run_skill_completion",
    "autoskillit.server._response_budget",
]
old_name_results = {}
for _name in old_names:
    try:
        importlib.import_module(_name)
        old_name_results[_name] = "IMPORTED"
    except ModuleNotFoundError:
        old_name_results[_name] = "ModuleNotFoundError"
    except Exception as exc:  # pragma: no cover - diagnostic path
        old_name_results[_name] = f"{type(exc).__name__}: {exc}"
results["old_name_results"] = old_name_results

print(json.dumps(results))
"""


def test_stage_c_canonical_response_imports_resolve_and_old_paths_are_gone() -> None:
    """New `server/response/` imports resolve; every old `server/_response_*` path is gone."""
    results = _run_cold_import(_STAGE_C_SCRIPT)
    assert (
        results["response_budget_enforce_first_import"]
        == "autoskillit.server.response._response_budget._enforce"
    )
    assert results["conformance_identity_ok"] is True
    assert results["canonical_imports_ok"] is True
    assert results["recipe_delivery_import_ok"] is True
    old_name_results = results["old_name_results"]
    assert isinstance(old_name_results, dict)
    not_removed = {
        name: outcome
        for name, outcome in old_name_results.items()
        if outcome != "ModuleNotFoundError"
    }
    assert not not_removed, f"old response module path(s) still importable: {not_removed}"


# ---------------------------------------------------------------------------
# T2 -- Stage C: layout, documentation, and real ceilings
# ---------------------------------------------------------------------------

_STAGE_C_PACKAGES: tuple[tuple[str, int], ...] = (
    ("server/response", 10),
    ("server/response/_response_budget", 10),
)


@pytest.mark.parametrize("rel_path,max_files", _STAGE_C_PACKAGES)
def test_stage_c_package_exists_within_file_limit_with_docs(rel_path: str, max_files: int) -> None:
    """Each new Stage C package exists, stays within its nested-file ceiling, and is documented.

    `server/response/_response_budget/` sits two directory levels below
    `server/` and is underscore-prefixed, so it is reached by neither
    `test_server_file_count_under_limit` (root only) nor
    `test_no_subpackage_exceeds_10_files` (one level of nesting, non-underscore
    names only) -- this parameterized case is what actually keeps it covered.
    """
    pkg_dir = SRC_ROOT / rel_path
    assert pkg_dir.is_dir(), f"{rel_path}/ does not exist"

    py_files = list(pkg_dir.glob("*.py"))
    assert len(py_files) <= max_files, (
        f"{rel_path}/ has {len(py_files)} direct Python files, max is {max_files}"
    )

    agents_md = pkg_dir / "AGENTS.md"
    assert agents_md.is_file(), f"{rel_path}/AGENTS.md is missing"
    agents_text = agents_md.read_text(encoding="utf-8")
    assert agents_text.strip(), f"{rel_path}/AGENTS.md is empty"

    claude_md = pkg_dir / "CLAUDE.md"
    assert claude_md.is_file(), f"{rel_path}/CLAUDE.md is missing"
    assert claude_md.read_text(encoding="utf-8") == "@AGENTS.md\n", (
        f"{rel_path}/CLAUDE.md must be the exact `@AGENTS.md` shim"
    )


# ---------------------------------------------------------------------------
# T1 -- Stage D: canonical imports and removed old locations
# ---------------------------------------------------------------------------

_STAGE_D_SCRIPT = """
import importlib
import json

results = {}

# The relocated campaign-state module is the first real import in this process.
from autoskillit.fleet.campaign_state.state import read_state
results["campaign_state_first_import"] = read_state.__module__

# Representative real `from ... import ...`, resolved through the fleet
# gateway, which retains its exact existing `__all__`.
from autoskillit.fleet.campaign_state.state_records import CampaignState, DispatchRecord
from autoskillit.fleet import CampaignState as _gw_campaign_state
from autoskillit.fleet import DispatchRecord as _gw_dispatch_record
results["gateway_identity_ok"] = (
    CampaignState is _gw_campaign_state and DispatchRecord is _gw_dispatch_record
)

# Every relocated module resolves by its full canonical name.
canonical_modules = [
    "autoskillit.fleet.campaign_state.state",
    "autoskillit.fleet.campaign_state.state_effects",
    "autoskillit.fleet.campaign_state.state_error_codes",
    "autoskillit.fleet.campaign_state.state_gates",
    "autoskillit.fleet.campaign_state.state_outcomes",
    "autoskillit.fleet.campaign_state.state_records",
    "autoskillit.fleet.campaign_state.state_recovery",
    "autoskillit.fleet.campaign_state.state_transitions",
    "autoskillit.fleet.campaign_state._state_lock",
]
for _name in canonical_modules:
    importlib.import_module(_name)
results["canonical_imports_ok"] = True

# Every corresponding old full module name must be gone.
old_names = [
    "autoskillit.fleet.state",
    "autoskillit.fleet.state_effects",
    "autoskillit.fleet.state_error_codes",
    "autoskillit.fleet.state_gates",
    "autoskillit.fleet.state_outcomes",
    "autoskillit.fleet.state_records",
    "autoskillit.fleet.state_recovery",
    "autoskillit.fleet.state_transitions",
    "autoskillit.fleet._state_lock",
]
old_name_results = {}
for _name in old_names:
    try:
        importlib.import_module(_name)
        old_name_results[_name] = "IMPORTED"
    except ModuleNotFoundError:
        old_name_results[_name] = "ModuleNotFoundError"
    except Exception as exc:  # pragma: no cover - diagnostic path
        old_name_results[_name] = f"{type(exc).__name__}: {exc}"
results["old_name_results"] = old_name_results

print(json.dumps(results))
"""


def test_stage_d_canonical_campaign_state_imports_resolve_and_old_paths_are_gone() -> None:
    """New `fleet/campaign_state/` imports resolve; every old `fleet/state*` path is gone."""
    results = _run_cold_import(_STAGE_D_SCRIPT)
    assert results["campaign_state_first_import"] == "autoskillit.fleet.campaign_state.state"
    assert results["gateway_identity_ok"] is True
    assert results["canonical_imports_ok"] is True
    old_name_results = results["old_name_results"]
    assert isinstance(old_name_results, dict)
    not_removed = {
        name: outcome
        for name, outcome in old_name_results.items()
        if outcome != "ModuleNotFoundError"
    }
    assert not not_removed, f"old campaign-state module path(s) still importable: {not_removed}"


# ---------------------------------------------------------------------------
# T2 -- Stage D: layout, documentation, and real ceilings
# ---------------------------------------------------------------------------

_STAGE_D_PACKAGES: tuple[tuple[str, int], ...] = (("fleet/campaign_state", 10),)


@pytest.mark.parametrize("rel_path,max_files", _STAGE_D_PACKAGES)
def test_stage_d_package_exists_within_file_limit_with_docs(rel_path: str, max_files: int) -> None:
    """The new Stage D package exists, stays within its nested-file ceiling, and is documented."""
    pkg_dir = SRC_ROOT / rel_path
    assert pkg_dir.is_dir(), f"{rel_path}/ does not exist"

    py_files = list(pkg_dir.glob("*.py"))
    assert len(py_files) <= max_files, (
        f"{rel_path}/ has {len(py_files)} direct Python files, max is {max_files}"
    )

    agents_md = pkg_dir / "AGENTS.md"
    assert agents_md.is_file(), f"{rel_path}/AGENTS.md is missing"
    agents_text = agents_md.read_text(encoding="utf-8")
    assert agents_text.strip(), f"{rel_path}/AGENTS.md is empty"

    claude_md = pkg_dir / "CLAUDE.md"
    assert claude_md.is_file(), f"{rel_path}/CLAUDE.md is missing"
    assert claude_md.read_text(encoding="utf-8") == "@AGENTS.md\n", (
        f"{rel_path}/CLAUDE.md must be the exact `@AGENTS.md` shim"
    )
