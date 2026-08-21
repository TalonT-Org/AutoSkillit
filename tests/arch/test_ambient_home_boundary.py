"""Architectural guard for injected-home and ambient-home consistency."""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.core import _plugin_cache as plugin_cache
from autoskillit.core import managed_home_for
from autoskillit.hooks.guards import mcp_health_advisor
from scripts.check_ambient_home_boundary import (
    AMBIENT_HOME_MODULES,
    find_ambient_home_violations,
    find_missing_registered_modules,
)

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

_SRC_ROOT = Path(__file__).resolve().parents[2] / "src" / "autoskillit"


def test_no_raw_path_home_in_registered_modules() -> None:
    assert not find_ambient_home_violations(_SRC_ROOT)


def test_guard_detects_an_injected_ambient_read(tmp_path: Path) -> None:
    module = "workspace/_projected_artifact/authority.py"
    path = tmp_path / module
    path.parent.mkdir(parents=True)
    path.write_text(
        "from pathlib import Path\n\ndef publish():\n    return Path.home()\n",
        encoding="utf-8",
    )

    assert find_ambient_home_violations(tmp_path) == [
        f"{module}:4: Path.home() in publish; pass the managed home explicitly"
    ]


def test_every_registered_module_exists() -> None:
    assert not find_missing_registered_modules(_SRC_ROOT)
    assert AMBIENT_HOME_MODULES == {
        "core/_plugin_cache.py": frozenset(),
        "cli/install/_plugin_artifact.py": frozenset(),
        "workspace/_install_state.py": frozenset({"_home"}),
        "workspace/_projected_artifact/authority.py": frozenset(),
        "workspace/_projected_artifact/_generation_publication.py": frozenset(),
        "workspace/_projected_artifact/_hook_repair.py": frozenset(
            {"repair_broken_projection_hooks"}
        ),
    }


def test_hook_side_active_kitchens_path_matches_core(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))

    hook_relative = mcp_health_advisor._active_kitchens_path().relative_to(home)
    core_relative = plugin_cache._active_kitchens_path(managed_home_for(home)).relative_to(home)

    assert hook_relative == core_relative
