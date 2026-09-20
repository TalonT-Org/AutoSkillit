"""Contract coverage for the managed Codex route preparation boundary."""

from __future__ import annotations

import ast
import re
from collections import Counter
from pathlib import Path

import pytest

from autoskillit.execution.backends import BACKEND_REGISTRY
from autoskillit.execution.backends._codex_hooks import managed_codex_route_digest
from autoskillit.hook_registry import HOOK_REGISTRY_HASH

pytestmark = [pytest.mark.layer("contracts"), pytest.mark.small]

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SRC_ROOT = _REPO_ROOT / "src" / "autoskillit"
_PREPARE = "prepare_managed_join_context"
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_EXPECTED_PREPARE_CALLERS = Counter(
    {
        ("cli/session/_session_cook.py", "cook"): 1,
        ("cli/session/_session_order.py", "order"): 1,
        ("cli/fleet/_fleet_run.py", "_execute_fleet_run"): 1,
        ("cli/fleet/_fleet_session.py", "_launch_fleet_session"): 1,
        ("server/tools/tools_fleet_dispatch/_handlers.py", "dispatch_food_truck"): 1,
        ("server/tools/tools_execution/_run_skill_prepare.py", "_prepare_dispatch_backend"): 1,
    }
)


def _call_name(node: ast.Call) -> str | None:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None


def _production_callers(symbol: str) -> Counter[tuple[str, str]]:
    inventory: Counter[tuple[str, str]] = Counter()
    for path in _SRC_ROOT.rglob("*.py"):
        relative_path = str(path.relative_to(_SRC_ROOT))
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=relative_path)
        parents = {
            child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)
        }
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or _call_name(node) != symbol:
                continue
            parent = parents.get(node)
            while parent is not None and not isinstance(
                parent, (ast.FunctionDef, ast.AsyncFunctionDef)
            ):
                parent = parents.get(parent)
            if parent is not None:
                inventory[(relative_path, parent.name)] += 1
    return inventory


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
    assert _production_callers(_PREPARE) == _EXPECTED_PREPARE_CALLERS


def test_managed_join_authority_issues_production_contexts() -> None:
    callers = _production_callers("issue")
    assert callers[("server/_managed_join_prelaunch.py", _PREPARE)] >= 1
