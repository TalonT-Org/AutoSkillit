"""Structural contract: every subprocess.run(["autoskillit", ...]) call in the CLI
must inject AUTOSKILLIT_SKIP_STALE_CHECK into the child's environment.

This test prevents infinite re-entry loops where the child process re-runs the
stale check before install completes. Analogous to test_interactive_subprocess_contracts.py
which enforces terminal_guard() wrapping for all non-capturing subprocess calls.

Contract: each call site must satisfy BOTH conditions:
  1. An ``env=`` keyword argument is present in the call.
  2. The string literal ``AUTOSKILLIT_SKIP_STALE_CHECK`` appears somewhere in the
     same source file, confirming the env dict is built from the guard (the variable
     pattern ``_skip_env = {**os.environ, "AUTOSKILLIT_SKIP_STALE_CHECK": "1"}``
     satisfies this without requiring the literal to appear inside the call expression).

Also contains ``test_no_raw_claude_env``: the sibling rule for claude-launching
subprocess calls. Every claude-launching site must route its env through
:func:`autoskillit.core.build_claude_env` (via ``spec.env`` from a
``Claude*Cmd`` dataclass, or via a direct builder call). The rule bans
``env={**os.environ, ...}``, ``env=os.environ``, ``env=None``, missing ``env=``,
and ``env=<literal dict>`` at every claude-launching site.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("cli"), pytest.mark.small]

CLI_ROOT = Path(__file__).parents[2] / "src" / "autoskillit" / "cli"
SRC_ROOT = Path(__file__).parents[2] / "src" / "autoskillit"
REQUIRED_GUARD = "AUTOSKILLIT_SKIP_STALE_CHECK"

_CLAUDE_BUILDER_NAMES = frozenset(
    {
        "build_interactive_cmd",
        "build_headless_cmd",
        "build_skill_session_cmd",
    }
)


def _collect_autoskillit_subprocess_calls(source: str) -> list[tuple[int, str, bool]]:
    """Return (lineno, source_fragment, has_env_kwarg) for every
    subprocess.run(['autoskillit',...]) call."""
    tree = ast.parse(source)
    results = []
    lines = source.splitlines()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        is_subprocess_call = (
            isinstance(func, ast.Attribute)
            and func.attr in ("run", "Popen")
            and isinstance(func.value, ast.Name)
            and func.value.id == "subprocess"
        )
        if not is_subprocess_call:
            continue
        if not node.args:
            continue
        first_arg = node.args[0]
        if not isinstance(first_arg, ast.List) or not first_arg.elts:
            continue
        first_elt = first_arg.elts[0]
        if not (isinstance(first_elt, ast.Constant) and first_elt.value == "autoskillit"):
            continue
        end = getattr(node, "end_lineno", node.lineno)
        fragment = "\n".join(lines[node.lineno - 1 : end])
        has_env_kwarg = any(kw.arg == "env" for kw in node.keywords)
        results.append((node.lineno, fragment, has_env_kwarg))
    return results


def test_autoskillit_subprocess_calls_inject_skip_stale_check_guard() -> None:
    """subprocess.run(['autoskillit', ...]) calls must inject the skip-stale-check guard."""
    if not CLI_ROOT.is_dir():
        pytest.skip("Source tree unavailable")
    violations: list[str] = []
    for py_file in sorted(CLI_ROOT.rglob("*.py")):
        source = py_file.read_text(encoding="utf-8")
        if "autoskillit" not in source:
            continue
        calls = _collect_autoskillit_subprocess_calls(source)
        non_comment_source = "\n".join(
            line for line in source.splitlines() if not line.lstrip().startswith("#")
        )
        guard_in_file = REQUIRED_GUARD in non_comment_source
        for lineno, fragment, has_env_kwarg in calls:
            if not has_env_kwarg or not guard_in_file:
                rel = py_file.relative_to(CLI_ROOT.parents[2])
                reason = []
                if not has_env_kwarg:
                    reason.append("missing env= kwarg")
                if not guard_in_file:
                    reason.append(f"'{REQUIRED_GUARD}' not found anywhere in file")
                violations.append(f"{rel}:{lineno} ({', '.join(reason)})\n  {fragment.strip()}")
    assert not violations, (
        f"Found {len(violations)} subprocess.run(['autoskillit', ...]) call(s) "
        f"not satisfying the env guard contract:\n\n" + "\n\n".join(violations)
    )


DRIFT_GUARD = "AUTOSKILLIT_SKIP_SOURCE_DRIFT_CHECK"


def test_update_checks_all_subprocess_calls_have_env_kwarg() -> None:
    """Every subprocess.run call in cli/_update_checks.py must carry env=.

    The unified update check invokes ['uv', ...] and ['autoskillit', ...]
    commands and must pass _skip_env to prevent re-entering the check.
    """
    update_checks = CLI_ROOT / "_update_checks.py"
    if not update_checks.exists():
        pytest.skip("Source tree unavailable")

    content = update_checks.read_text(encoding="utf-8")
    tree = ast.parse(content)

    violations: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (
            isinstance(func, ast.Attribute)
            and func.attr in ("run", "Popen")
            and isinstance(func.value, ast.Name)
            and func.value.id == "subprocess"
        ):
            continue
        has_env = any(kw.arg == "env" for kw in node.keywords)
        if not has_env:
            violations.append(f"Line {node.lineno}: subprocess.{func.attr}() missing env= kwarg")

    assert not violations, (
        "All subprocess calls in _update_checks.py must carry env=_skip_env.\n"
        + "\n".join(violations)
    )

    # File-level: must define both guard env vars
    assert REQUIRED_GUARD in content
    assert DRIFT_GUARD in content


def test_all_call_sites_set_autoskillit_skip_source_drift_check() -> None:
    """Every CLI file that defines AUTOSKILLIT_SKIP_STALE_CHECK must also define
    AUTOSKILLIT_SKIP_SOURCE_DRIFT_CHECK — both guards travel together.

    Rationale: a subprocess launched by the update-check path could re-enter the
    drift gate unless both skip vars are set.  This test enforces co-location.
    """
    if not CLI_ROOT.is_dir():
        pytest.skip("Source tree unavailable")

    violations: list[str] = []
    for py_file in sorted(CLI_ROOT.rglob("*.py")):
        content = py_file.read_text(encoding="utf-8")
        non_comment = "\n".join(
            line for line in content.splitlines() if not line.lstrip().startswith("#")
        )
        if REQUIRED_GUARD not in non_comment:
            continue
        if DRIFT_GUARD not in non_comment:
            rel = py_file.relative_to(CLI_ROOT.parents[2])
            violations.append(f"{rel}: defines '{REQUIRED_GUARD}' but not '{DRIFT_GUARD}'")

    assert not violations, (
        f"Found {len(violations)} file(s) with {REQUIRED_GUARD!r} but missing "
        f"{DRIFT_GUARD!r}:\n\n" + "\n".join(violations)
    )


def test_update_command_all_subprocess_calls_have_env_kwarg() -> None:
    """Every subprocess.run call in cli/_update.py must carry env=."""
    update_cmd = CLI_ROOT / "_update.py"
    if not update_cmd.exists():
        pytest.skip("Source tree unavailable")

    content = update_cmd.read_text(encoding="utf-8")
    tree = ast.parse(content)

    violations: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (
            isinstance(func, ast.Attribute)
            and func.attr in ("run", "Popen")
            and isinstance(func.value, ast.Name)
            and func.value.id == "subprocess"
        ):
            continue
        has_env = any(kw.arg == "env" for kw in node.keywords)
        if not has_env:
            violations.append(f"Line {node.lineno}: subprocess.{func.attr}() missing env= kwarg")

    assert not violations, (
        "All subprocess calls in _update.py must carry env=_skip_env.\n" + "\n".join(violations)
    )

    assert REQUIRED_GUARD in content
    assert DRIFT_GUARD in content


# ─────────────────────────────────────────────────────────────────────────────
# Sibling rule: _run_subprocess(env=...) calls in server/ must start from
# os.environ — enforced via per-function AST walk with single-assignment
# tracking.
# ─────────────────────────────────────────────────────────────────────────────

SERVER_ROOT = Path(__file__).parents[2] / "src" / "autoskillit" / "server"


def _dict_has_os_environ_unpack(dict_node: ast.Dict) -> bool:
    """True if the dict literal contains **os.environ."""
    for key, val in zip(dict_node.keys, dict_node.values):
        if key is None and isinstance(val, ast.Attribute):
            if isinstance(val.value, ast.Name) and val.value.id == "os" and val.attr == "environ":
                return True
    return False


def _check_env_value(
    env_val: ast.expr,
    bindings: dict[str, ast.expr],
    lineno: int,
    path: Path,
) -> str | None:
    """Return a violation string if env_val is unsafe, None if safe.

    Safe patterns:
    - env=None literal
    - env=<Dict> containing **os.environ
    - env=<Call> (builder function)
    - env=<Name> bound to a safe value (resolved one level via bindings)
    - env=<Name> unresolvable (parameter/outer scope — trusted)
    """
    if isinstance(env_val, ast.Constant) and env_val.value is None:
        return None
    if isinstance(env_val, ast.Dict):
        if _dict_has_os_environ_unpack(env_val):
            return None
        rel = path.relative_to(SERVER_ROOT.parents[2])
        return f"{rel}:{lineno}: _run_subprocess(env=<dict>) without **os.environ"
    if isinstance(env_val, ast.Call):
        return None
    if isinstance(env_val, ast.Name):
        bound = bindings.get(env_val.id)
        if bound is None:
            return None
        return _check_env_value(bound, {}, lineno, path)
    return None


def _find_run_subprocess_env_violations(source: str, path: Path) -> list[str]:
    """Find _run_subprocess() calls with unsafe env= kwargs, per-function."""
    tree = ast.parse(source)
    violations: list[str] = []
    for func_node in ast.walk(tree):
        if not isinstance(func_node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        bindings: dict[str, ast.expr] = {}
        for stmt in ast.walk(func_node):
            if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1:
                target = stmt.targets[0]
                if isinstance(target, ast.Name):
                    bindings[target.id] = stmt.value
            elif isinstance(stmt, ast.AnnAssign) and stmt.value is not None:
                if isinstance(stmt.target, ast.Name):
                    bindings[stmt.target.id] = stmt.value
        for node in ast.walk(func_node):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not (isinstance(func, ast.Name) and func.id == "_run_subprocess"):
                continue
            env_kw = None
            for kw in node.keywords:
                if kw.arg == "env":
                    env_kw = kw.value
                    break
            if env_kw is None:
                continue
            violation = _check_env_value(env_kw, bindings, node.lineno, path)
            if violation:
                violations.append(violation)
    return violations


def test_run_subprocess_callers_use_safe_env_pattern() -> None:
    """Every _run_subprocess(env=...) call in server/ must start from os.environ."""
    if not SERVER_ROOT.is_dir():
        pytest.skip("Source tree unavailable")
    violations: list[str] = []
    for py_file in sorted(SERVER_ROOT.rglob("*.py")):
        source = py_file.read_text(encoding="utf-8")
        if "_run_subprocess" not in source:
            continue
        violations.extend(_find_run_subprocess_env_violations(source, py_file))
    assert not violations, (
        "Found _run_subprocess() calls with unsafe env= patterns:\n"
        + "\n".join(f"  {v}" for v in violations)
    )


# ─────────────────────────────────────────────────────────────────────────────
# Sibling rule: claude-launching subprocess calls must route env through
# build_claude_env() — enforced via intra-function AST walk.
# ─────────────────────────────────────────────────────────────────────────────


def _call_func_name(func: ast.AST) -> str:
    """Return the simple/qualified name of a Call's .func expression (best-effort)."""
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def _is_claude_builder_call(node: ast.AST) -> bool:
    """True if *node* is a call to any claude command builder."""
    if not isinstance(node, ast.Call):
        return False
    return _call_func_name(node.func) in _CLAUDE_BUILDER_NAMES


def _is_literal_claude_list(node: ast.AST) -> bool:
    """True if *node* is a literal list whose first element is the string 'claude'."""
    if not isinstance(node, ast.List) or not node.elts:
        return False
    first = node.elts[0]
    return isinstance(first, ast.Constant) and first.value == "claude"


class _ClaudeLaunchWalker:
    """Per-function walker: tracks single-assignment symbol table for a FunctionDef.

    Detects claude-launching call patterns on the first positional or ``cmd=``
    kwarg and verifies the ``env=`` kwarg shape is one of:

    - ``Attribute(value=Name, attr="env")`` where Name was assigned from a
      ``build_interactive_cmd``/``build_headless_cmd``/``build_skill_session_cmd``
      call earlier in the same function body (spec.env pattern).
    - ``Call(func=Name("build_claude_env") | Attribute(..., "build_claude_env"))``
      (direct builder escape hatch).
    """

    def __init__(self, func_node: ast.AST, path: Path) -> None:
        self.func_node = func_node
        self.path = path
        # Name -> most recent assigned value (single-assignment tracker).
        self._bindings: dict[str, ast.expr] = {}
        self.violations: list[str] = []

    def walk(self) -> None:
        for stmt in ast.walk(self.func_node):
            # Track simple assignments first so calls later in the body can resolve.
            if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1:
                target = stmt.targets[0]
                if isinstance(target, ast.Name):
                    self._bindings[target.id] = stmt.value
            elif isinstance(stmt, ast.AnnAssign) and stmt.value is not None:
                if isinstance(stmt.target, ast.Name):
                    self._bindings[stmt.target.id] = stmt.value
            elif isinstance(stmt, ast.Call):
                self._check_call(stmt)

    def _resolve_name(self, node: ast.AST) -> ast.expr | None:
        """Follow a Name through one level of assignment; return the bound value or None."""
        if isinstance(node, ast.Name) and node.id in self._bindings:
            return self._bindings[node.id]
        return None

    def _cmd_is_claude_launching(self, cmd_arg: ast.AST) -> bool:
        """True if *cmd_arg* refers to a claude-launching argv (via spec or literal)."""
        # Direct literal: ["claude", ...]
        if _is_literal_claude_list(cmd_arg):
            return True

        # spec.cmd attribute access where spec was assigned from a builder.
        if isinstance(cmd_arg, ast.Attribute) and cmd_arg.attr == "cmd":
            bound = self._resolve_name(cmd_arg.value)
            if bound is not None and _is_claude_builder_call(bound):
                return True

        # Name: resolve one level.
        if isinstance(cmd_arg, ast.Name):
            bound = self._resolve_name(cmd_arg)
            if bound is None:
                return False
            # cmd = ["claude", ...]
            if _is_literal_claude_list(bound):
                return True
            # cmd = spec.cmd (+ [...])  or  cmd = spec.cmd
            if isinstance(bound, ast.Attribute) and bound.attr == "cmd":
                return self._cmd_is_claude_launching(bound)
            if isinstance(bound, ast.BinOp) and isinstance(bound.op, ast.Add):
                return self._cmd_is_claude_launching(bound.left) or self._cmd_is_claude_launching(
                    bound.right
                )
            # cmd = spec_result_of_builder  → treat attribute .cmd the same way
            if _is_claude_builder_call(bound):
                return True

        # cmd = spec.cmd + [...] passed inline as BinOp.
        if isinstance(cmd_arg, ast.BinOp) and isinstance(cmd_arg.op, ast.Add):
            return self._cmd_is_claude_launching(cmd_arg.left) or self._cmd_is_claude_launching(
                cmd_arg.right
            )

        return False

    @staticmethod
    def _cmd_arg_of(call: ast.Call) -> ast.AST | None:
        """Return the claude argv argument: first positional, or cmd= kwarg."""
        if call.args:
            return call.args[0]
        for kw in call.keywords:
            if kw.arg == "cmd":
                return kw.value
        return None

    @staticmethod
    def _env_kwarg(call: ast.Call) -> ast.expr | None:
        for kw in call.keywords:
            if kw.arg == "env":
                return kw.value
        return None

    def _env_shape_ok(self, env_val: ast.expr | None) -> tuple[bool, str]:
        if env_val is None:
            return False, "missing env= kwarg"
        if isinstance(env_val, ast.Constant) and env_val.value is None:
            return False, "env=None is not allowed"
        if isinstance(env_val, ast.Dict):
            # env={**os.environ, ...} or env={"K": "V"} — both are banned.
            return False, "env=<literal dict> is not allowed"
        if isinstance(env_val, ast.Attribute):
            # env=os.environ
            if (
                isinstance(env_val.value, ast.Name)
                and env_val.value.id == "os"
                and env_val.attr == "environ"
            ):
                return False, "env=os.environ is not allowed"
            # env=spec.env / env=foo.env
            if env_val.attr == "env":
                return True, ""
        if isinstance(env_val, ast.Call):
            fn = _call_func_name(env_val.func)
            if fn == "build_claude_env":
                return True, ""
        if isinstance(env_val, ast.Name):
            # Resolve one level of local binding to catch aliased os.environ.
            bound = self._resolve_name(env_val)
            if bound is not None:
                return self._env_shape_ok(bound)
            # Unresolvable name (parameter / outer scope): rely on the function
            # owning it to pass a scrubbed env.
            return True, ""
        return False, f"env= has unrecognised shape: {ast.dump(env_val)[:60]}"

    def _check_call(self, call: ast.Call) -> None:
        cmd_arg = self._cmd_arg_of(call)
        if cmd_arg is None:
            return
        if not self._cmd_is_claude_launching(cmd_arg):
            return
        env_val = self._env_kwarg(call)
        ok, reason = self._env_shape_ok(env_val)
        if not ok:
            rel = self.path.relative_to(SRC_ROOT.parents[1])
            self.violations.append(
                f"{rel}:{call.lineno}: claude-launching call at "
                f"{_call_func_name(call.func) or '<anonymous>'}(...) — {reason}"
            )


# Allowlist: administrative ``claude plugin ...`` subprocess calls that are
# plugin marketplace / registration operations, NOT skill sessions. They do
# not present a tool surface to the model, so IDE-channel attach is not a
# concern. Each entry is (filename, enclosing_function_name).
#
# Adding a new entry requires a comment justifying why the call cannot use
# build_claude_env — matching the convention in
# ``tests/arch/test_ast_rules.py::test_no_raw_claude_list_construction``.
_CLAUDE_ENV_RULE_ALLOWED: frozenset[tuple[str, str]] = frozenset(
    {
        # doctor check: `claude plugin list` — read-only registration probe.
        ("_doctor.py", "_check_mcp_server_registered"),
        # onboarding probe: `claude plugin list` — read-only install check.
        ("_init_helpers.py", "_is_plugin_installed"),
        # marketplace registration + install: `claude plugin marketplace add` /
        # `claude plugin install`. Administrative ops invoked once during setup.
        ("_marketplace.py", "install"),
        # run_headless_core and dispatch_food_truck pass `spec` (ClaudeHeadlessCmd)
        # to _execute_claude_headless, which internally uses `spec.env` at the
        # runner call site. The checker incorrectly treats _execute_claude_headless(spec, ...)
        # as a direct subprocess call because `spec` resolves to a builder return value.
        ("__init__.py", "run_headless_core"),
        ("__init__.py", "dispatch_food_truck"),
        # build_headless_resume_cmd constructs cmd = ["claude", ...] then calls
        # _apply_output_format(cmd, ...) — an in-place list mutation, not a subprocess
        # launch. The function returns ClaudeHeadlessCmd(cmd=cmd, env=build_claude_env(...)).
        ("commands.py", "build_headless_resume_cmd"),
    }
)


def test_no_raw_claude_env() -> None:
    """Every claude-launching subprocess call must route env through build_claude_env()."""
    if not SRC_ROOT.is_dir():
        pytest.skip("Source tree unavailable")

    violations: list[str] = []
    for py_file in sorted(SRC_ROOT.rglob("*.py")):
        try:
            tree = ast.parse(py_file.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if (py_file.name, node.name) in _CLAUDE_ENV_RULE_ALLOWED:
                continue
            walker = _ClaudeLaunchWalker(node, py_file)
            walker.walk()
            violations.extend(walker.violations)

    assert not violations, (
        "Found claude-launching subprocess calls that bypass build_claude_env():\n"
        + "\n".join(f"  {v}" for v in violations)
    )
