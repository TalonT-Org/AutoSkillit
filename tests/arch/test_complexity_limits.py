"""The live complexity policy registry stays valid, and its surfaces stay registered."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.arch._acceptance_policy_surfaces import POLICY_SURFACES
from tests.infra._complexity_helpers import load_check_script

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

REPO_ROOT = Path(__file__).resolve().parents[2]
_CHECK_SCRIPT = REPO_ROOT / "scripts" / "check_complexity.py"
_CHECK_MODULE_NAME = "_autoskillit_check_complexity_limits"

check = load_check_script(_CHECK_MODULE_NAME, _CHECK_SCRIPT)


def test_live_registry_is_valid():
    reader = check._working_tree_reader(REPO_ROOT)
    problems = check.validate_exemptions(check.load_policy(reader), reader)
    assert problems == []


def test_limits_are_policy_surfaces():
    required = {
        ("tests/arch/_complexity_limits.py", "MAX_COMPLEXITY", "int_scalar"),
        ("tests/arch/_complexity_limits.py", "COMPLEXITY_EXEMPTIONS", "exemption_map"),
    }
    registered = {(surface.path, surface.symbol, surface.kind) for surface in POLICY_SURFACES}
    assert required <= registered
