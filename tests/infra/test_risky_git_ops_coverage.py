"""Structural test: every risky git operation must have PreToolUse guard coverage.

Imports RISKY_GIT_OPERATIONS from hook_registry and verifies that for each
tuple, at least one Bash|run_cmd-matching guard contains detection logic for
all tokens in that tuple.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest

from autoskillit.hook_registry import HOOK_REGISTRY, RISKY_GIT_OPERATIONS
from tests._evaluation_shape_matrix import EVALUATION_SHAPE_MATRIX, wrap_git_op

pytestmark = [pytest.mark.layer("infra"), pytest.mark.small]

_GUARDS_DIR = Path(__file__).parent.parent.parent / "src" / "autoskillit" / "hooks" / "guards"
# _git_command_classification.py uses bare-name imports (`from
# _command_classification import ...`) that resolve only when the hooks
# directory is on sys.path; the sys.path bootstrap for this is centralized
# in tests/conftest.py (it must run before collection, which a fixture
# cannot do).


def _command_inspecting_guard_scripts() -> list[tuple[str, Path]]:
    """Return (logical_name, path) for guards registered under Bash|run_cmd matchers."""
    found: list[tuple[str, Path]] = []
    seen: set[str] = set()
    for hook_def in HOOK_REGISTRY:
        if hook_def.event_type != "PreToolUse":
            continue
        if not re.fullmatch(hook_def.matcher, "Bash"):
            continue
        for script in hook_def.scripts:
            if not script.startswith("guards/"):
                continue
            guard_name = Path(script).stem
            if guard_name in seen:
                continue
            seen.add(guard_name)
            script_path = _GUARDS_DIR / f"{guard_name}.py"
            if script_path.exists():
                found.append((guard_name, script_path))
    return found


def _load_guard_detection_frozensets(guard_name: str) -> list[frozenset]:
    """Import a guard module and collect all frozenset-of-tuples module-level attributes."""
    spec = importlib.util.spec_from_file_location(
        f"_guard_{guard_name}",
        _GUARDS_DIR / f"{guard_name}.py",
    )
    if spec is None or spec.loader is None:
        return []
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)  # type: ignore[attr-defined]
    except Exception:
        return []
    sets: list[frozenset] = []
    for attr_name in dir(mod):
        val = getattr(mod, attr_name, None)
        if isinstance(val, frozenset) and val:
            sample = next(iter(val))
            if isinstance(sample, tuple):
                sets.append(val)
    return sets


def test_risky_git_ops_covered_by_guard_detection_sets() -> None:
    """Guards that expose detection frozensets must include all risky git ops.

    Collects all frozenset-of-tuples attributes from command-inspecting guards via
    importlib and asserts that every tuple from RISKY_GIT_OPERATIONS appears in
    the union. This enforces that git_ops_guard exports its detection set as an
    importable frozenset rather than embedding it as anonymous inline comparisons.
    """
    guard_scripts = _command_inspecting_guard_scripts()

    union_of_detected_ops: set[tuple[str, ...]] = set()
    for guard_name, _ in guard_scripts:
        for fs in _load_guard_detection_frozensets(guard_name):
            for item in fs:
                if isinstance(item, tuple):
                    union_of_detected_ops.add(item)

    missing = set(RISKY_GIT_OPERATIONS) - union_of_detected_ops

    assert not missing, (
        f"Risky git operation tuples not found in any guard's detection frozenset: "
        f"{sorted(missing)}. "
        f"Add the tuples to git_ops_guard._BLOCKED_GIT_OPS or equivalent."
    )


def test_legacy_risky_git_tuples_match_git_ops_guard_exactly() -> None:
    """The tuple registry covers simple legacy forms, not operand-bearing ref writes."""
    spec = importlib.util.spec_from_file_location(
        "_git_ops_guard_legacy_sync", _GUARDS_DIR / "git_ops_guard.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[attr-defined]
    assert module._BLOCKED_GIT_OPS == RISKY_GIT_OPERATIONS


def test_risky_git_operations_authority_is_hook_constants() -> None:
    """The guard-local _BLOCKED_GIT_OPS must equal the canonical RISKY_GIT_OPERATIONS.

    The guard imports the frozenset from the shared authority module under
    the local alias ``_BLOCKED_GIT_OPS``; the registry re-exports it from
    ``autoskillit.hook_registry``. The two paths load the module under
    different ``sys.modules`` keys (the guard uses the sys.path-bootstrap
    bare name; the registry uses the package-qualified name), so we assert
    on value equality rather than identity — they must be the same frozenset.
    """
    from autoskillit.hooks._runtime._hook_constants import RISKY_GIT_OPERATIONS
    from autoskillit.hooks.guards.git_ops_guard import _BLOCKED_GIT_OPS  # noqa: PLC0415

    assert _BLOCKED_GIT_OPS == RISKY_GIT_OPERATIONS


_EXECUTING_SHAPES = [shape for shape in EVALUATION_SHAPE_MATRIX if shape.executes]
_INERT_SHAPES = [shape for shape in EVALUATION_SHAPE_MATRIX if not shape.executes]


@pytest.mark.parametrize("op", sorted(RISKY_GIT_OPERATIONS), ids=lambda op: "-".join(op))
@pytest.mark.parametrize("shape", _EXECUTING_SHAPES, ids=lambda s: s.id)
def test_every_risky_git_op_is_detected_through_every_evaluation_shape(shape, op) -> None:
    """rectify #4941 Part A: every RISKY_GIT_OPERATIONS tuple, delivered through
    every executing evaluation shape (direct, -c, eval, heredoc, herestring,
    pipe, substitution, Python subprocess), must still be denied.
    """
    from autoskillit.hooks.guards._git_command_classification import (  # noqa: PLC0415
        _contains_blocked_git_op,
    )

    cmd = wrap_git_op(shape, op)
    result = _contains_blocked_git_op(cmd, RISKY_GIT_OPERATIONS)
    # Some RISKY_GIT_OPERATIONS tuples structurally overlap once rendered
    # with a trailing "origin main" (e.g. ("checkout", ".") also matches
    # wherever ("checkout", "--", ".") does), so any detected tuple --
    # not necessarily the exact one parametrized -- proves this shape was
    # correctly denied.
    assert result is not None, f"{shape.id}: {op} not detected through {cmd!r}"


@pytest.mark.parametrize("op", sorted(RISKY_GIT_OPERATIONS), ids=lambda op: "-".join(op))
@pytest.mark.parametrize("shape", _INERT_SHAPES, ids=lambda s: s.id)
def test_inert_shapes_never_flag_a_risky_git_op(shape, op) -> None:
    """The inert half of the matrix: a heredoc/herestring body whose consumer
    never executes it must never trip the blocklist, however the delivery
    shape happens to render the operation's text.
    """
    from autoskillit.hooks.guards._git_command_classification import (  # noqa: PLC0415
        _contains_blocked_git_op,
    )

    cmd = wrap_git_op(shape, op)
    result = _contains_blocked_git_op(cmd, RISKY_GIT_OPERATIONS)
    assert result is None, f"{shape.id}: {op} incorrectly denied through {cmd!r} (got {result!r})"
