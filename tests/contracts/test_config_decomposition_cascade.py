"""REQ-CONFIG-FILTER-001..004: new modules fail-open into the L1 cascade.

The test filter cascade is fail-open: a config/ source file that is not in
``MODULE_CASCADE_CONFIG`` cascades to the full ``LAYER_CASCADE_CONSERVATIVE
['config']`` set. This contract ensures the new modules don't accidentally
narrow or break that behavior.
"""

from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest

from tests._test_filter import (  # type: ignore[import-not-found]
    FilterMode,
    build_test_scope,
)

pytestmark = [pytest.mark.layer("contracts"), pytest.mark.small]


def _make_tests_root(tmp_path: Path) -> Path:
    tests_root = tmp_path / "tests"
    for d in [
        "config",
        "execution",
        "fleet",
        "pipeline",
        "workspace",
        "recipe",
        "server",
        "cli",
        "arch",
        "contracts",
        "infra",
        "docs",
        "core",
        "migration",
        "hooks",
        "skills",
        "skills_extended",
    ]:
        (tests_root / d).mkdir(parents=True, exist_ok=True)
    return tests_root


def _cascade_dirs(changed_file: str, tmp_path: Path) -> set[str]:
    """Return the set of cascade target directory names for ``changed_file``."""
    tests_root = _make_tests_root(tmp_path)
    result = build_test_scope(
        changed_files={changed_file},
        mode=FilterMode.CONSERVATIVE,
        tests_root=tests_root,
    )
    assert result is not None, "build_test_scope must return a path set"
    # ``result`` is `set[Path] | FullRunReason`; cast to set[Path] since we
    # expect a concrete cascade for these non-empty changeset inputs.
    paths = cast("set[Path]", result)
    return {p.name for p in paths if p.is_dir()}


@pytest.mark.parametrize(
    "new_module",
    [
        "_automation_config.py",
        "_coercion.py",
        "_coherence.py",
        "_retired_keys.py",
        "_validation.py",
        "_writer.py",
        "_dataclasses_errors.py",
        "_dataclasses_test_gating.py",
        "_dataclasses_execution.py",
        "_dataclasses_workflow.py",
        "_dataclasses_diagnostics.py",
        "_dataclasses_github.py",
        "_dataclasses_surfaces.py",
        "_dataclasses_fleet.py",
        "_dataclasses_providers.py",
    ],
)
def test_new_module_falls_through_to_config_cascade(
    new_module: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Each new module fails open into LAYER_CASCADE_CONSERVATIVE['config'].

    NOTE: this test verifies the absence of accidental narrowing. It does not
    claim cascade coverage was added — none was, by design. If a future reviewer
    wants narrower cascade for a specific module, add it to MODULE_CASCADE_CONFIG
    AND add a contract test asserting the narrower scope.

    Uses ``monkeypatch.chdir`` so the CWD change is automatically reverted at
    teardown, keeping the test safe under pytest-xdist and avoiding any chance
    of a finally-block exception masking an assertion failure.
    """
    monkeypatch.chdir(tmp_path)
    dir_names = _cascade_dirs(f"src/autoskillit/config/{new_module}", tmp_path)
    assert "config" in dir_names, f"new module {new_module} must cascade to config"


@pytest.mark.parametrize("stem", ["settings.py", "_config_dataclasses.py"])
def test_existing_facades_still_fail_open(
    stem: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The renamed-thin settings.py and _config_dataclasses.py keep their fail-open behavior."""
    monkeypatch.chdir(tmp_path)
    dir_names = _cascade_dirs(f"src/autoskillit/config/{stem}", tmp_path)
    for pkg in ["config", "execution", "server", "cli"]:
        assert pkg in dir_names, f"{stem} must still cascade to {pkg}"
