"""Architectural guard: every ``--maintenance-update`` argv must come from
``MaintenanceInstallArgv.to_argv()``.

Issue #4485's root cause was three production sites hand-building argv
literals for ``autoskillit install --maintenance-update`` and bypassing
the typed contract that would have enforced ``--expected-version``. This
AST guard makes the structural invariant permanent: any list or tuple literal
containing the ``--maintenance-update`` string outside the canonical
builder module is a violation.

Pattern: ast.walk() over every .py file under src/autoskillit, skipping
the allowlist. Each list or tuple literal is inspected for any constant elt
equal to ``--maintenance-update``. Found literals are reported as
violations with file:line:violation-line-number.

Issue #4597 (A-6) followed up by introducing
``MaintenanceSubprocessInvocation`` (``core/types/_type_install.py``), whose
``for_version_probe()``/``for_install()`` factories bundle argv + env + a
validated cwd + an explicit stdio disposition so a spawn site cannot supply
one without the others. The module-level scan above only proves the absence
of hand-built argv literals; it has no notion of a "spawn site" and cannot
assert anything about env/cwd/stdio. The second scanner below (standalone,
following the ARCH-007/ARCH-011 precedent for rules that don't fit the
shared ``ArchitectureViolationVisitor``) closes that gap for the four known
self-invocation spawn sites by finding the factory call expressions
themselves and the ``runner(...)`` calls that consume them.

Residual gap (both scanners below): this is a purely syntactic AST scanner.
It reads literal call expressions and direct attribute reads on locals; it
does not perform data-flow analysis, does not recurse into helper functions
that might construct env/argv/cwd indirectly through another layer of
indirection, and cannot observe the sites' actual runtime behavior — only
their static shape. A spawn site that renamed its invocation variable
between the factory call and the ``runner(...)`` call, or that laundered an
attribute through an intermediate alias, would defeat this scan. That is an
accepted residual gap, not something these tests attempt to close.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

# The four production self-invocation spawn sites introduced by issue #4597
# (A-6): a --version probe and an install --maintenance-update child in each
# of the update transaction and obligation-repair modules. Every call site
# building a MaintenanceSubprocessInvocation, and every runner() call that
# consumes one, lives in one of these two files.
_SPAWN_SITE_FILES: tuple[Path, ...] = (
    Path("src/autoskillit/cli/update/_transaction.py"),
    Path("src/autoskillit/cli/update/_obligation_repair.py"),
)

_INVOCATION_FACTORY_METHODS = frozenset({"for_version_probe", "for_install"})
_REQUIRED_FACTORY_KWARGS = frozenset({"environment", "cwd"})


# Files that legitimately contain the literal (the canonical builder).
_ALLOWLIST: frozenset[Path] = frozenset(
    {
        Path("src/autoskillit/core/types/_type_install.py"),
    },
)

_SRC_ROOT = Path("src/autoskillit")


def _scan_for_maintenance_update_literals(tree: ast.AST) -> list[int]:
    """Return sequence-literal lines containing ``--maintenance-update``."""
    found: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.List, ast.Tuple)):
            for elt in node.elts:
                if isinstance(elt, ast.Constant) and elt.value == "--maintenance-update":
                    found.append(node.lineno)
                    break
    return found


def test_scan_detects_tuple_literals() -> None:
    tree = ast.parse("argv = ('install', '--maintenance-update')")

    assert _scan_for_maintenance_update_literals(tree) == [1]


def test_no_hand_built_maintenance_update_argv() -> None:
    """No production code may hand-build argv containing ``--maintenance-update``.

    Use ``MaintenanceInstallArgv.to_argv()`` instead. The single allowlist
    is the canonical builder itself; any other site is a regression of
    issue #4485.
    """
    src_root = Path(_SRC_ROOT)
    violations: list[str] = []
    for py_file in sorted(src_root.rglob("*.py")):
        if py_file in _ALLOWLIST:
            continue
        try:
            source = py_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        try:
            tree = ast.parse(source, filename=str(py_file))
        except SyntaxError:
            continue
        for lineno in _scan_for_maintenance_update_literals(tree):
            violations.append(f"{py_file}:{lineno}: hand-built argv with --maintenance-update")
    assert not violations, (
        "Use MaintenanceInstallArgv.to_argv() instead of hand-building argv:\n"
        + "\n".join(violations)
    )


# ── Spawn-site enumeration: MaintenanceSubprocessInvocation factory calls ──


def _is_invocation_factory_call(node: ast.expr) -> str | None:
    """Return the factory method name if `node` is a call to
    ``MaintenanceSubprocessInvocation.for_version_probe`` or ``.for_install``.
    """
    if not isinstance(node, ast.Call):
        return None
    func = node.func
    if (
        isinstance(func, ast.Attribute)
        and func.attr in _INVOCATION_FACTORY_METHODS
        and isinstance(func.value, ast.Name)
        and func.value.id == "MaintenanceSubprocessInvocation"
    ):
        return func.attr
    return None


def _scan_invocation_factory_calls(tree: ast.AST) -> list[str]:
    """Return violations for factory calls missing ``environment=``/``cwd=`` kwargs.

    Every ``for_version_probe()``/``for_install()`` call must pass
    ``environment`` and ``cwd`` by keyword — the factory internally routes
    ``environment`` through ``build_maintenance_env()`` and validates ``cwd``
    via ``is_git_worktree``/``is_git_main_checkout``, so a call site that
    supplies these positionally could still be caught by Python's normal
    argument binding, but requiring keywords here makes the call site's
    intent to route through those checks explicit and greppable.
    """
    violations: list[str] = []
    for node in ast.walk(tree):
        method = _is_invocation_factory_call(node)
        if method is None:
            continue
        assert isinstance(node, ast.Call)
        kwarg_names = {kw.arg for kw in node.keywords if kw.arg is not None}
        missing = _REQUIRED_FACTORY_KWARGS - kwarg_names
        if missing:
            violations.append(
                f"line {node.lineno}: MaintenanceSubprocessInvocation.{method}() "
                f"missing required keyword(s) {sorted(missing)}"
            )
    return violations


def _invocation_var_from_argv_arg(node: ast.expr) -> str | None:
    """Return the invocation variable name if `node` is ``<var>.argv`` or
    ``list(<var>.argv)`` — the two argv-passing shapes used at every known
    spawn site.
    """
    if (
        isinstance(node, ast.Attribute)
        and node.attr == "argv"
        and isinstance(node.value, ast.Name)
    ):
        return node.value.id
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "list"
        and len(node.args) == 1
    ):
        return _invocation_var_from_argv_arg(node.args[0])
    return None


def _is_attribute_of(node: ast.expr | None, var_name: str, attr: str) -> bool:
    """True if `node` is exactly ``<var_name>.<attr>``."""
    return (
        isinstance(node, ast.Attribute)
        and node.attr == attr
        and isinstance(node.value, ast.Name)
        and node.value.id == var_name
    )


def _invocation_var_names(tree: ast.AST) -> frozenset[str]:
    """Return every local name assigned directly from a
    ``MaintenanceSubprocessInvocation.for_version_probe()``/``.for_install()``
    call — i.e. names that actually carry the typed, env/cwd/stdio-bundled
    contract, as opposed to any other variable that merely happens to expose
    an ``.argv`` attribute (e.g. the unrelated ``UpgradeCommand`` returned by
    ``upgrade_command()``, issue #4597 Phase 3). Single-target ``Name =
    <factory call>`` assignments only — the residual gap docstring above
    already accepts that laundering through an alias or destructuring defeats
    this scan.
    """
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if isinstance(target, ast.Name) and _is_invocation_factory_call(node.value) is not None:
            names.add(target.id)
    return frozenset(names)


def _scan_runner_calls_for_invocation_attrs(tree: ast.AST) -> list[str]:
    """Return violations for runner() calls that spawn off an invocation's
    argv but source ``env=``/``cwd=`` from something other than that same
    invocation's ``.env``/``.cwd`` attributes.

    A call is scoped into this check only when its argv argument is already
    ``<var>.argv`` (or ``list(<var>.argv)``) *and* ``<var>`` was assigned
    directly from a ``MaintenanceSubprocessInvocation`` factory call (see
    ``_invocation_var_names``) — i.e. only calls that are already spawning a
    MaintenanceSubprocessInvocation-built child. This deliberately excludes
    subprocess calls unrelated to the typed contract, such as the
    ``UpgradeCommand``-driven package-upgrade command spawns in
    ``run_update_transaction``, which never claim to route through an
    invocation's argv in the first place — they carry their own, differently
    shaped contract (an additive ``env`` overlay merged into the caller's
    validated base env, no ``cwd`` field at all since the caller's
    ``working_dir`` is shared across every spawn in that function).
    """
    invocation_vars = _invocation_var_names(tree)
    violations: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
            continue
        # ``list(<var>.argv)`` itself is a Call node ast.walk() will also
        # visit; it is the argv-shaping wrapper, not the spawn call that
        # consumes env=/cwd=, so it must not be mistaken for one.
        if node.func.id in {"list", "tuple"}:
            continue
        argv_var: str | None = None
        for arg in node.args:
            argv_var = _invocation_var_from_argv_arg(arg)
            if argv_var is not None:
                break
        if argv_var is None or argv_var not in invocation_vars:
            continue
        kwargs = {kw.arg: kw.value for kw in node.keywords if kw.arg is not None}
        if not _is_attribute_of(kwargs.get("env"), argv_var, "env"):
            violations.append(
                f"line {node.lineno}: {node.func.id}() spawns {argv_var}.argv but "
                f"env= does not reference {argv_var}.env"
            )
        if not _is_attribute_of(kwargs.get("cwd"), argv_var, "cwd"):
            violations.append(
                f"line {node.lineno}: {node.func.id}() spawns {argv_var}.argv but "
                f"cwd= does not reference {argv_var}.cwd"
            )
    return violations


def test_scan_detects_invocation_factory_call_missing_kwargs() -> None:
    """Sanity-check the factory-call scanner against synthetic ASTs."""
    complete = ast.parse(
        "MaintenanceSubprocessInvocation.for_install("
        "entrypoint, version, environment=env, cwd=cwd)"
    )
    assert _scan_invocation_factory_calls(complete) == []

    missing_cwd = ast.parse(
        "MaintenanceSubprocessInvocation.for_version_probe(entrypoint, environment=env)"
    )
    violations = _scan_invocation_factory_calls(missing_cwd)
    assert len(violations) == 1
    assert "cwd" in violations[0]


_INVOCATION_ASSIGN = (
    "invocation = MaintenanceSubprocessInvocation.for_install(entrypoint, version, "
    "environment=env, cwd=cwd)\n"
)


def test_scan_detects_runner_call_bypassing_invocation_attrs() -> None:
    """Sanity-check the runner-call scanner against synthetic ASTs."""
    compliant = ast.parse(
        _INVOCATION_ASSIGN
        + "runner(list(invocation.argv), env=invocation.env, cwd=invocation.cwd)"
    )
    assert _scan_runner_calls_for_invocation_attrs(compliant) == []

    raw_env = ast.parse(
        _INVOCATION_ASSIGN
        + "runner(list(invocation.argv), env=dict(os.environ), cwd=invocation.cwd)"
    )
    violations = _scan_runner_calls_for_invocation_attrs(raw_env)
    assert len(violations) == 1
    assert "env=" in violations[0]

    unrelated_call = ast.parse("runner(command, env=maintenance_env, cwd=working_dir)")
    assert _scan_runner_calls_for_invocation_attrs(unrelated_call) == []

    # issue #4597 Phase 3: a variable exposing its own unrelated `.argv`
    # attribute (e.g. `UpgradeCommand`, never built via
    # `MaintenanceSubprocessInvocation.for_*()`) must not be mistaken for an
    # invocation spawn just because the syntax happens to match.
    unrelated_argv_attr = ast.parse(
        "command = upgrade_command(info, install_root_destination=dest)\n"
        "runner(command.argv, env={**maintenance_env, **command.env}, cwd=working_dir)"
    )
    assert _scan_runner_calls_for_invocation_attrs(unrelated_argv_attr) == []


def test_every_maintenance_install_spawn_binds_env_and_stdio() -> None:
    """Every ``MaintenanceSubprocessInvocation`` spawn site binds env + cwd.

    Issue #4597 (A-6) consolidated all four self-invocation spawn sites
    (a --version probe and an install --maintenance-update child, each in
    both the update-transaction and obligation-repair modules) onto
    ``MaintenanceSubprocessInvocation.for_version_probe()``/``.for_install()``.
    Both factories internally call ``build_maintenance_env()`` and validate
    ``cwd`` via ``is_git_worktree``/``is_git_main_checkout``, and both set
    ``capture_output=True`` — so a call site that reaches the factory with
    ``environment=``/``cwd=`` keywords, and then spawns using that same
    invocation's ``.argv``/``.env``/``.cwd`` attributes, cannot skip any of
    those checks. This test asserts both halves of that chain hold at every
    known spawn site — see the module docstring for the scan's residual gap.
    """
    factory_violations: list[str] = []
    attr_violations: list[str] = []
    files_scanned = 0
    for py_file in _SPAWN_SITE_FILES:
        assert py_file.is_file(), f"expected spawn-site file to exist: {py_file}"
        source = py_file.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(py_file))
        factory_violations.extend(f"{py_file}:{v}" for v in _scan_invocation_factory_calls(tree))
        attr_violations.extend(
            f"{py_file}:{v}" for v in _scan_runner_calls_for_invocation_attrs(tree)
        )
        files_scanned += 1

    assert files_scanned == len(_SPAWN_SITE_FILES)
    assert not factory_violations, (
        "MaintenanceSubprocessInvocation factory calls must pass environment= "
        "and cwd= by keyword:\n" + "\n".join(factory_violations)
    )
    assert not attr_violations, (
        "runner() calls spawning a MaintenanceSubprocessInvocation's argv must "
        "source env= and cwd= from that same invocation's .env/.cwd attributes, "
        "not a raw dict/os.environ construction:\n" + "\n".join(attr_violations)
    )

    # Coverage floor: fail loudly if a refactor removes the factory calls or
    # the runner() calls entirely, rather than passing vacuously on an empty
    # scan. Four spawn sites means four factory calls and four runner() calls
    # that source argv from one of them.
    factory_call_count = 0
    invocation_backed_runner_call_count = 0
    for py_file in _SPAWN_SITE_FILES:
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        invocation_vars = _invocation_var_names(tree)
        for node in ast.walk(tree):
            if _is_invocation_factory_call(node) is not None:
                factory_call_count += 1
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id not in {"list", "tuple"}
                and any(_invocation_var_from_argv_arg(arg) in invocation_vars for arg in node.args)
            ):
                invocation_backed_runner_call_count += 1
    assert factory_call_count == 4, (
        f"expected 4 MaintenanceSubprocessInvocation factory calls across "
        f"{[str(p) for p in _SPAWN_SITE_FILES]}, found {factory_call_count}"
    )
    assert invocation_backed_runner_call_count == 4, (
        f"expected 4 runner() calls spawning off a MaintenanceSubprocessInvocation "
        f"across {[str(p) for p in _SPAWN_SITE_FILES]}, found "
        f"{invocation_backed_runner_call_count}"
    )
