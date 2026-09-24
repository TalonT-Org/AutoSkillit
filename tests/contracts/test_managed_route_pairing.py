"""Contract coverage for the managed Codex route preparation boundary."""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

import pytest

from autoskillit.execution.backends import BACKEND_REGISTRY
from autoskillit.execution.backends._codex_hooks import managed_codex_route_digest
from autoskillit.hook_registry import HOOK_REGISTRY_HASH
from tests.contracts._ast_helpers import callers_by_function

pytestmark = [pytest.mark.layer("contracts"), pytest.mark.small]

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SRC_ROOT = _REPO_ROOT / "src" / "autoskillit"
_PREPARE_LAUNCH = "acquire_managed_join_evidence"
_PREPARE_CONTEXT = "prepare_managed_join_context"
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_EXPECTED_PREPARE_LAUNCH_CALLERS = Counter(
    {
        ("cli/session/_session_cook.py", "_acquire_cook_managed_join"): 1,
        ("cli/session/_session_order.py", "order"): 1,
        ("cli/fleet/_fleet_run.py", "_execute_fleet_run"): 1,
        ("cli/fleet/_fleet_session.py", "_launch_fleet_session"): 1,
        ("server/tools/tools_fleet_dispatch/_handlers.py", "_prepare_managed_join"): 1,
        (
            "server/tools/tools_execution/_run_skill_prepare.py",
            "_prepare_managed_parent_projection",
        ): 1,
    }
)


def test_managed_route_digests_are_sha256_for_every_capable_backend() -> None:
    capable_backends = [
        name
        for name, factory in BACKEND_REGISTRY.items()
        if factory().capabilities.managed_fixed_batch_route_capable
    ]

    assert capable_backends, "no backend declares managed_fixed_batch_route_capable"
    for backend_name in capable_backends:
        assert _SHA256.fullmatch(HOOK_REGISTRY_HASH), backend_name
        assert _SHA256.fullmatch(managed_codex_route_digest()), backend_name


def test_managed_route_preparation_covers_every_capable_launch_surface() -> None:
    actual = callers_by_function(_SRC_ROOT, symbol=_PREPARE_LAUNCH)
    assert actual == _EXPECTED_PREPARE_LAUNCH_CALLERS


def test_managed_join_authority_issues_production_contexts() -> None:
    callers = callers_by_function(_SRC_ROOT, symbol="issue")
    assert callers[("server/managed_join_prelaunch.py", _PREPARE_CONTEXT)] >= 1
