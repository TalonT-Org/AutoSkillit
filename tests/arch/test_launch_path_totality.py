"""Guard the intraprocedural half of launch-path retirement totality.

The guard prevents registered functions from originating new disallowed exceptions
and rejects syntactically discarded total results. It intentionally does not inspect
callees; the launch-path corruption matrix remains the interprocedural safety proof.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.check_launch_path_totality import (
    LAUNCH_TOTAL_FUNCTIONS,
    MUST_CONSUME_TOTAL_RESULTS,
    find_discarded_total_result_violations,
    find_missing_registered_functions,
    find_originated_exception_violations,
)

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

_SRC_ROOT = Path(__file__).resolve().parents[2] / "src"

_LAUNCH_STATE_CALLBACKS = (
    (
        ("autoskillit/core/runtime/session_registry.py", "bind_session_owner"),
        frozenset({"ValueError"}),
    ),
    (("autoskillit/core/_plugin_cache.py", "register_active_kitchen"), frozenset()),
    (("autoskillit/core/_plugin_cache.py", "unregister_active_kitchen"), frozenset()),
)


def _write_module(src_root: Path, module: str, source: str) -> None:
    path = src_root / module
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")


def test_registered_launch_functions_originate_no_disallowed_exception() -> None:
    assert not find_originated_exception_violations(_SRC_ROOT)


@pytest.mark.parametrize(("target", "allowed_exceptions"), _LAUNCH_STATE_CALLBACKS)
def test_launch_state_callbacks_are_total_and_must_be_consumed(
    target: tuple[str, str],
    allowed_exceptions: frozenset[str],
) -> None:
    assert LAUNCH_TOTAL_FUNCTIONS[target] == allowed_exceptions
    assert target in MUST_CONSUME_TOTAL_RESULTS


def test_guard_detects_an_injected_runtime_error(tmp_path: Path) -> None:
    module, qualname = next(iter(LAUNCH_TOTAL_FUNCTIONS))
    assert "." not in qualname
    _write_module(
        tmp_path,
        module,
        f'def {qualname}():\n    raise RuntimeError("retiring cache cannot mutate")\n',
    )

    violations = find_originated_exception_violations(tmp_path)

    assert len(violations) == 1
    assert "disallowed exception RuntimeError" in violations[0]


def test_total_result_calls_are_consumed() -> None:
    assert not find_discarded_total_result_violations(_SRC_ROOT)


def test_guard_detects_an_injected_ignored_total_result(tmp_path: Path) -> None:
    _write_module(
        tmp_path,
        "example.py",
        """\
def exercise(owner):
    owner.enqueue_retirement(identity)
    result = owner.enqueue_retirement(identity)
    return owner.enqueue_retirement(identity)
""",
    )

    violations = find_discarded_total_result_violations(tmp_path)

    assert len(violations) == 1
    assert "example.py:2: discarded total result from *.enqueue_retirement" in violations


def test_every_registered_function_exists() -> None:
    assert not find_missing_registered_functions(_SRC_ROOT)
