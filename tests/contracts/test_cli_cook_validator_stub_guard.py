"""Contract: no CLI test stubs ClaudeCodeBackend.validate_interactive_invocation
while also invoking cli.cook (#4684 Fix B).

Before #4684, 11+ tests/cli/ locations stubbed validate_interactive_invocation
to a no-op — but every one of them either (a) stubs the *real* CodexBackend
(a distinct, unrelated implementation: Codex has no agent-teams concept, per
codex.py's ``_BACKEND_NO_TEAMS_CONCEPT_DOC`` and Plan Step 6's out-of-scope
note), or (b) defines a standalone fake/test-double backend class with its
own hand-rolled ``validate_interactive_invocation`` — never the real
``ClaudeCodeBackend``. Composition coverage of the actual regression (#4613:
an unconditional Claude-specific policy call inside
``assert_interactive_ordering``) was zero, because nothing exercised the
real ``ClaudeCodeBackend.validate_interactive_invocation`` against a real
``cli.cook()`` call — see tests/cli/test_cook_settings_local_agent_teams.py,
the test this plan adds to close that gap.

This guard is deliberately narrower than "any validate_interactive_invocation
stub": it flags only a monkeypatch of the *real* ``ClaudeCodeBackend`` class
(by name or by dotted import path) combined with a ``cli.cook(...)`` call in
the same test function. Stubbing CodexBackend (a different, legitimately
no-op-for-this-policy implementation) or defining a fake backend class is not
flagged — those are not the composition gap #4684 identifies, and a fully
generic rule would immediately false-positive on today's Codex-focused tests.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("contracts"), pytest.mark.small]

_CLI_TESTS_DIR = Path(__file__).resolve().parents[2] / "tests" / "cli"
_TARGET_CLASS_NAME = "ClaudeCodeBackend"
_TARGET_METHOD_NAME = "validate_interactive_invocation"


def _is_monkeypatch_setattr_call(node: ast.AST) -> ast.Call | None:
    if not isinstance(node, ast.Call):
        return None
    func = node.func
    if (
        isinstance(func, ast.Attribute)
        and func.attr == "setattr"
        and isinstance(func.value, ast.Name)
        and func.value.id == "monkeypatch"
    ):
        return node
    return None


def _stubs_real_claude_backend_validator(call: ast.Call) -> bool:
    """True iff `call` is monkeypatch.setattr(...) targeting the real
    ClaudeCodeBackend.validate_interactive_invocation, in either the
    3-arg (obj, "attr", value) or 2-arg ("dotted.path", value) form."""
    args = call.args
    if len(args) >= 2 and isinstance(args[1], ast.Constant) and isinstance(args[1].value, str):
        # 3-arg form: monkeypatch.setattr(ClaudeCodeBackend, "validate_interactive_invocation", fn)
        target, attr_name = args[0], args[1].value
        if attr_name == _TARGET_METHOD_NAME:
            if isinstance(target, ast.Name) and target.id == _TARGET_CLASS_NAME:
                return True
            if isinstance(target, ast.Attribute) and target.attr == _TARGET_CLASS_NAME:
                return True
    if len(args) >= 1 and isinstance(args[0], ast.Constant) and isinstance(args[0].value, str):
        # 2-arg form: monkeypatch.setattr(
        #     "...ClaudeCodeBackend.validate_interactive_invocation", fn
        # )
        dotted = args[0].value
        if _TARGET_CLASS_NAME in dotted and dotted.endswith(f".{_TARGET_METHOD_NAME}"):
            return True
    return False


def _calls_cli_cook(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "cook"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "cli"
    )


def _violations_in_file(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    violations: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        stubs_real_backend = any(
            call is not None and _stubs_real_claude_backend_validator(call)
            for call in (_is_monkeypatch_setattr_call(n) for n in ast.walk(node))
        )
        if not stubs_real_backend:
            continue
        if any(_calls_cli_cook(n) for n in ast.walk(node)):
            try:
                display_path = path.relative_to(_CLI_TESTS_DIR.parents[1])
            except ValueError:
                # Not under the repo root (e.g. a synthetic fixture under tmp_path
                # in this guard's own self-tests) — fall back to the raw path.
                display_path = path
            violations.append(
                f"{display_path}:{node.lineno} "
                f"({node.name}) stubs the real {_TARGET_CLASS_NAME}.{_TARGET_METHOD_NAME} "
                "and also calls cli.cook(...) — this hides the exact composition gap "
                "#4684 identifies. Use a fake backend double, or stub only "
                "CodexBackend/an unrelated method, or drop the stub and exercise the "
                "real policy (see test_cook_settings_local_agent_teams.py)."
            )
    return violations


def test_no_cli_cook_test_stubs_the_real_claude_backend_validator() -> None:
    violations: list[str] = []
    for path in sorted(_CLI_TESTS_DIR.glob("*.py")):
        violations.extend(_violations_in_file(path))
    assert not violations, "\n".join(violations)


def test_guard_detects_synthetic_three_arg_violation(tmp_path: Path) -> None:
    fixture = tmp_path / "test_synthetic.py"
    fixture.write_text(
        "from autoskillit import cli\n"
        "from autoskillit.execution.backends.claude import ClaudeCodeBackend\n\n"
        "def test_bad(monkeypatch):\n"
        "    monkeypatch.setattr(\n"
        '        ClaudeCodeBackend, "validate_interactive_invocation", lambda *_: []\n'
        "    )\n"
        "    cli.cook(backend=ClaudeCodeBackend())\n"
    )
    violations = _violations_in_file(fixture)
    assert len(violations) == 1
    assert "test_bad" in violations[0]


def test_guard_detects_synthetic_two_arg_dotted_violation(tmp_path: Path) -> None:
    fixture = tmp_path / "test_synthetic_dotted.py"
    dotted_target = (
        "autoskillit.execution.backends.claude.ClaudeCodeBackend.validate_interactive_invocation"
    )
    fixture.write_text(
        "from autoskillit import cli\n\n"
        "def test_bad(monkeypatch):\n"
        "    monkeypatch.setattr(\n"
        f'        "{dotted_target}",\n'
        "        lambda *_: [],\n"
        "    )\n"
        "    cli.cook()\n"
    )
    violations = _violations_in_file(fixture)
    assert len(violations) == 1


def test_guard_allows_codex_backend_stub(tmp_path: Path) -> None:
    """Stubbing CodexBackend (unrelated, out-of-scope per Plan Step 6) must not flag."""
    fixture = tmp_path / "test_ok_codex.py"
    fixture.write_text(
        "from autoskillit import cli\n"
        "from autoskillit.execution.backends.codex import CodexBackend\n\n"
        "def test_ok(monkeypatch):\n"
        '    monkeypatch.setattr(CodexBackend, "validate_interactive_invocation", lambda *_: [])\n'
        "    cli.cook(backend=CodexBackend())\n"
    )
    assert _violations_in_file(fixture) == []


def test_guard_allows_fake_backend_double(tmp_path: Path) -> None:
    """A standalone fake backend class defining its own validate_interactive_invocation
    is not a stub of the real ClaudeCodeBackend and must not flag."""
    fixture = tmp_path / "test_ok_fake.py"
    fixture.write_text(
        "from autoskillit import cli\n\n"
        "class _FakeBackend:\n"
        "    def validate_interactive_invocation(self, spec):\n"
        "        return []\n\n"
        "def test_ok(monkeypatch):\n"
        "    cli.cook(backend=_FakeBackend())\n"
    )
    assert _violations_in_file(fixture) == []


def test_guard_allows_stub_without_cli_cook_call(tmp_path: Path) -> None:
    """Stubbing the real backend in isolation (e.g. a unit test of the stub
    machinery itself) with no cli.cook(...) call is not the composition gap."""
    fixture = tmp_path / "test_ok_no_cook.py"
    fixture.write_text(
        "from autoskillit.execution.backends.claude import ClaudeCodeBackend\n\n"
        "def test_ok(monkeypatch):\n"
        "    monkeypatch.setattr(\n"
        '        ClaudeCodeBackend, "validate_interactive_invocation", lambda *_: []\n'
        "    )\n"
    )
    assert _violations_in_file(fixture) == []
