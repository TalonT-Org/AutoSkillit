"""AST guard: verify all SkillResult() constructions include kill_reason keyword arg.

This guard prevents the class of bug where a new early-return branch constructs
SkillResult without passing kill_reason=result.kill_reason, silently defaulting to
KillReason.NATURAL_EXIT instead of preserving the subprocess's actual kill reason.

Scans _headless_result.py for direct SkillResult() calls and asserts each call
includes kill_reason as a keyword argument.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]


def _find_skill_result_calls(src: str) -> list[ast.Call]:
    """Walk AST and return all direct SkillResult(...) calls."""
    tree = ast.parse(src)
    calls: list[ast.Call] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            # Direct SkillResult(...) call (not module.SkillResult or attribute)
            if isinstance(func, ast.Name) and func.id == "SkillResult":
                calls.append(node)
    return calls


def _has_kill_reason_kwarg(call: ast.Call) -> bool:
    """Return True if the call has kill_reason as a keyword argument."""
    return any(kw.arg == "kill_reason" for kw in call.keywords)


HEADLESS_RESULT_PATH = (
    Path(__file__).parents[2]
    / "src"
    / "autoskillit"
    / "execution"
    / "headless"
    / "_headless_result.py"
)


class TestSkillResultConstructionGuard:
    def test_all_skill_result_calls_include_kill_reason(self):
        """Every direct SkillResult() call must pass kill_reason as a keyword arg.

        This prevents new construction sites from accidentally omitting kill_reason,
        which silently defaults to NATURAL_EXIT instead of propagating the actual
        subprocess kill reason (INFRA_KILL for stale/idle/exception terminations).
        """
        src = HEADLESS_RESULT_PATH.read_text()
        calls = _find_skill_result_calls(src)

        assert len(calls) > 0, "Expected to find at least one SkillResult() call"

        missing_kill_reason: list[int] = []
        for call in calls:
            if not _has_kill_reason_kwarg(call):
                missing_kill_reason.append(call.lineno)

        assert not missing_kill_reason, (
            f"SkillResult() calls missing kill_reason kwarg at lines: {missing_kill_reason}. "
            "All SkillResult() constructions must pass kill_reason=result.kill_reason "
            "to avoid silently defaulting to KillReason.NATURAL_EXIT."
        )
