"""REQ-CONFIG-SIZE-001..014: every config/ source module ≤ 750 lines.

Lock-in for the #4859 config decomposition: after the split, no module under
``src/autoskillit/config/`` may exceed the project's 750-line budget. The
guard is fail-open for modules that don't exist yet (skipped), but fail-loud
the moment one does — a new module beyond budget fails immediately.
"""

from __future__ import annotations

from pathlib import Path

import pytest

CONFIG_DIR = Path("src/autoskillit/config")
LINE_BUDGET = 750


@pytest.mark.parametrize(
    "module_name",
    [
        "__init__.py",
        "settings.py",
        "_config_dataclasses.py",
        "_config_loader.py",
        "ingredient_defaults.py",
        "_automation_config.py",
        "_coercion.py",
        "_coherence.py",
        "_retired_keys.py",
        "_validation.py",
        "_writer.py",
        "_dataclasses_shared.py",
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
def test_module_within_line_budget(module_name: str) -> None:
    path = CONFIG_DIR / module_name
    if not path.exists():
        pytest.skip(f"{module_name} not present yet")
    line_count = sum(1 for _ in path.open())
    assert line_count <= LINE_BUDGET, (
        f"{module_name} is {line_count} lines, exceeds budget of {LINE_BUDGET}"
    )
