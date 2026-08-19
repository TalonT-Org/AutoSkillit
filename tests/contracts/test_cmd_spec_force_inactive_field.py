"""Contract: CmdSpec carries force_inactive_agent_teams; every constructor passes it.

#4684 Fix B moved the interactive content-policy opt-in onto the CmdSpec
artifact itself. Two things must hold for that opt-in to be trustworthy:

1. CmdSpec declares the field (so a future refactor can't quietly drop it).
2. Every ``CmdSpec(...)`` construction site in src/autoskillit/ passes the
   kwarg explicitly — an omitted kwarg silently falls back to the dataclass
   default (False), which is a legitimate value but must be a deliberate
   choice at each site, not an accident of forgetting the parameter.
"""

from __future__ import annotations

import ast
import dataclasses
from pathlib import Path

import pytest

from autoskillit.core.types._type_backend import CmdSpec

pytestmark = [pytest.mark.layer("contracts"), pytest.mark.small]

_SRC_ROOT = Path(__file__).resolve().parents[2] / "src" / "autoskillit"


def test_cmd_spec_declares_force_inactive_agent_teams_field() -> None:
    fields = {f.name: f for f in dataclasses.fields(CmdSpec)}
    assert "force_inactive_agent_teams" in fields
    assert fields["force_inactive_agent_teams"].type == "bool"
    assert fields["force_inactive_agent_teams"].default is False


def _cmd_spec_call_sites() -> list[tuple[Path, ast.Call]]:
    sites: list[tuple[Path, ast.Call]] = []
    for py_file in sorted(_SRC_ROOT.rglob("*.py")):
        # _type_backend.py contains the class definition itself, not a construction.
        if py_file.name == "_type_backend.py":
            continue
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "CmdSpec"
            ):
                sites.append((py_file, node))
    return sites


def test_all_cmdspec_constructors_pass_force_inactive() -> None:
    sites = _cmd_spec_call_sites()
    assert sites, "no CmdSpec(...) construction sites found — scan predicate is broken"
    missing = [
        f"{py_file}:{node.lineno}"
        for py_file, node in sites
        if not any(kw.arg == "force_inactive_agent_teams" for kw in node.keywords)
    ]
    assert not missing, (
        "CmdSpec(...) constructed without an explicit force_inactive_agent_teams= "
        f"kwarg at: {missing}"
    )
