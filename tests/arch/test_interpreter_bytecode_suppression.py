"""Architectural guard: every hooks-side interpreter spawn suppresses bytecode writes.

Companion to ``tests/hooks/test_hook_command_suppression.py``, which exercises
specific renderers behaviorally. This guard is structural: it walks every
``.py`` file under ``src/autoskillit/hooks/`` plus ``hook_registry.py`` and
``execution/backends/_codex_hooks.py`` (the two IL-1/IL-3 modules that render
hook command strings outside the hooks package itself) looking for any site
that constructs a ``python3``/``sys.executable`` interpreter invocation, and
requires ``-B`` unconditionally. New spawn sites are caught automatically —
nothing needs to opt in.

Deliberately naming/structure-agnostic: detection keys off *what a site is
shaped like* (a subprocess argv list containing ``sys.executable``, or a
string/f-string whose own text begins with ``"python3 "``), not off where the
result is assigned, returned, or embedded, and not off the enclosing
function's name. A command string built into a local variable before being
placed in a dict or returned by name (e.g. ``_build_hook_command``'s
non-relocatable branch) is exactly as detectable as one written inline.

Modeled on ``tests/arch/test_xfail_bridge_policy.py``'s AST-walk-plus-registry
shape: a bounded, rationale-carrying exemption registry
(``_BYTECODE_SUPPRESSION_EXEMPT``) paired with a pincer meta-test that fails
if an exemption entry no longer corresponds to a real, still-non-suppressing
site (mirrors ``TestRetiredArtifactShapeRegistry``'s no-orphan half in
``tests/contracts/test_install_state_consistency.py``).
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SRC = _REPO_ROOT / "src" / "autoskillit"
_HOOKS_DIR = _SRC / "hooks"

_PYTHON3_COMMAND_RE = re.compile(r"^python3\s")

# Interpreter-spawn sites that legitimately cannot carry -B, keyed by
# (module-relative-path-under-src/autoskillit, line-number). Every reason
# must explain the exemption in at least 40 characters. Starts EMPTY:
# every spawn site as of issue #4480 already passes -B — an entry here
# should be rare and reviewed carefully, not a default escape hatch.
_BYTECODE_SUPPRESSION_EXEMPT: dict[tuple[str, int], str] = {}


def _target_files() -> list[Path]:
    files = sorted(_HOOKS_DIR.rglob("*.py"))
    files.append(_SRC / "hook_registry.py")
    files.append(_SRC / "execution" / "backends" / "_codex_hooks.py")
    return files


def _is_sys_executable(node: ast.expr) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "executable"
        and isinstance(node.value, ast.Name)
        and node.value.id == "sys"
    )


def _render_string_node(node: ast.Constant | ast.JoinedStr) -> str:
    if isinstance(node, ast.Constant):
        return node.value if isinstance(node.value, str) else ""
    parts: list[str] = []
    for value in node.values:
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            parts.append(value.value)
        elif isinstance(value, ast.FormattedValue):
            parts.append(f"{{{ast.unparse(value.value)}}}")
    return "".join(parts)


class _CommandStringVisitor(ast.NodeVisitor):
    """Finds python3-command-shaped string/f-string literals.

    Deliberately does not descend into a ``JoinedStr``'s own parts: those are
    either literal fragments already folded into the rendered text, or
    interpolated sub-expressions unrelated to command-string detection.
    Visiting them separately would double-report the same site and — worse —
    would flag mid-string mentions of "python3" (e.g. an instructional
    message telling an agent what shell command to run) as if the *message
    itself* were a command we constructed, which it is not: only a string
    whose own rendered text begins with the interpreter invocation is.
    """

    def __init__(self) -> None:
        self.sites: list[tuple[int, str]] = []

    def visit_JoinedStr(self, node: ast.JoinedStr) -> None:
        rendered = _render_string_node(node)
        if _PYTHON3_COMMAND_RE.match(rendered):
            self.sites.append((node.lineno, rendered))

    def visit_Constant(self, node: ast.Constant) -> None:
        if isinstance(node.value, str) and _PYTHON3_COMMAND_RE.match(node.value):
            self.sites.append((node.lineno, node.value))


def _interpreter_spawn_sites(tree: ast.Module) -> list[tuple[int, str]]:
    """Every interpreter-invocation-construction site in ``tree``.

    Two independent shapes are covered:
      - a literal ``list`` containing ``sys.executable`` as an element
        (the ``subprocess.run`` argv shape used by ``_dispatch.py`` and
        ``shell_capture_hook.py``)
      - a string/f-string whose rendered text begins with ``"python3 "``
        (the command-template shape used by ``hook_registry.py`` and
        ``_codex_hooks.py``)

    Returns ``(line, rendered_text)`` pairs.
    """
    sites: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.List) and any(_is_sys_executable(elt) for elt in node.elts):
            rendered = " ".join(
                elt.value
                if isinstance(elt, ast.Constant) and isinstance(elt.value, str)
                else ast.unparse(elt)
                for elt in node.elts
            )
            sites.append((node.lineno, rendered))
    visitor = _CommandStringVisitor()
    visitor.visit(tree)
    sites.extend(visitor.sites)
    return sites


def _has_bytecode_suppression_flag(rendered: str) -> bool:
    return "-B" in rendered.split()


def _unexempted_violations() -> list[str]:
    violations: list[str] = []
    for path in _target_files():
        rel = path.relative_to(_SRC).as_posix()
        tree = ast.parse(path.read_text())
        for lineno, rendered in _interpreter_spawn_sites(tree):
            if _has_bytecode_suppression_flag(rendered):
                continue
            if (rel, lineno) in _BYTECODE_SUPPRESSION_EXEMPT:
                continue
            violations.append(f"{rel}:{lineno} — missing -B: {rendered!r}")
    return violations


def test_every_interpreter_spawn_site_suppresses_bytecode() -> None:
    violations = _unexempted_violations()
    assert not violations, (
        "interpreter spawn site(s) missing bytecode suppression (-B) — either "
        "add -B to the invocation or register a rationale in "
        "_BYTECODE_SUPPRESSION_EXEMPT:\n" + "\n".join(f"  {v}" for v in violations)
    )


def test_exempt_entries_have_rationale() -> None:
    for key, reason in _BYTECODE_SUPPRESSION_EXEMPT.items():
        assert len(reason) >= 40, f"{key}: exemption reason too thin to act on"


def test_no_exempt_entry_is_orphaned() -> None:
    """Pincer meta-test: an allowlist entry whose site no longer needs it must go.

    Mirrors ``TestRetiredArtifactShapeRegistry``'s no-orphan half — the
    registry must track the codebase's actual state, not accumulate
    permanently. An entry is orphaned once its site either stops missing
    ``-B`` (fixed) or stops existing (removed/renamed).
    """
    still_missing = {
        (rel, lineno)
        for path in _target_files()
        for rel in (path.relative_to(_SRC).as_posix(),)
        for lineno, rendered in _interpreter_spawn_sites(ast.parse(path.read_text()))
        if not _has_bytecode_suppression_flag(rendered)
    }
    orphaned = sorted(k for k in _BYTECODE_SUPPRESSION_EXEMPT if k not in still_missing)
    assert not orphaned, (
        "_BYTECODE_SUPPRESSION_EXEMPT entries no longer correspond to a live, "
        f"still-non-suppressing spawn site — remove them: {orphaned}"
    )


def test_exemption_registry_starts_empty() -> None:
    """As of issue #4480, no site needs an exemption. Growth should be rare."""
    assert _BYTECODE_SUPPRESSION_EXEMPT == {}


def _has_env_bytecode_suppression(tree: ast.Module) -> bool:
    """Check if a module structurally sets PYTHONDONTWRITEBYTECODE.

    This is a file-level check because the env injection may happen in a
    different function than the argv construction (e.g. ``_runner_argv``
    builds the list, ``_render_harness`` adds the ``PYTHONDONTWRITEBYTECODE=1``
    prefix in the rendered bash command).
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Subscript)
            and isinstance(target.slice, ast.Constant)
            and target.slice.value == "PYTHONDONTWRITEBYTECODE"
            for target in node.targets
        ):
            return True
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "append"
            and node.args
            and isinstance(node.args[0], (ast.Constant, ast.JoinedStr))
            and _render_string_node(node.args[0]).startswith("PYTHONDONTWRITEBYTECODE=1 ")
        ):
            return True
    return False


def _subprocess_sites_missing_env_suppression() -> list[str]:
    """Find modules with subprocess spawn sites but no PYTHONDONTWRITEBYTECODE."""
    violations: list[str] = []
    for path in _target_files():
        rel = path.relative_to(_SRC).as_posix()
        source = path.read_text()
        tree = ast.parse(source)
        has_spawn = any(
            isinstance(node, ast.List) and any(_is_sys_executable(elt) for elt in node.elts)
            for node in ast.walk(tree)
        )
        if has_spawn and not _has_env_bytecode_suppression(tree):
            violations.append(
                f"{rel} — has subprocess spawn with sys.executable "
                "but no PYTHONDONTWRITEBYTECODE assignment in the module"
            )
    return violations


def test_subprocess_spawns_also_set_env_suppression() -> None:
    """Every subprocess.run([sys.executable, ...]) site must additionally set
    PYTHONDONTWRITEBYTECODE in the child env for ordinary-descendant coverage.

    ``-B`` alone does not propagate to grandchildren via ``subprocess``;
    ``-I`` (isolated mode) grandchildren ignore ``PYTHON*`` env entirely —
    hence ``-B`` is the unconditional requirement (REQ-T4-1) and env
    injection is the supplementary coverage requirement (REQ-T4-2).
    """
    violations = _subprocess_sites_missing_env_suppression()
    assert not violations, (
        "subprocess spawn site(s) missing PYTHONDONTWRITEBYTECODE in child env — "
        "add env['PYTHONDONTWRITEBYTECODE'] = '1' for ordinary-descendant coverage:\n"
        + "\n".join(f"  {v}" for v in violations)
    )


def test_env_suppression_detector_ignores_comments_and_docstrings() -> None:
    tree = ast.parse(
        '"""PYTHONDONTWRITEBYTECODE=1"""\n# env[\'PYTHONDONTWRITEBYTECODE\'] = \'1\'\n'
    )

    assert not _has_env_bytecode_suppression(tree)


def test_env_suppression_detector_accepts_child_env_assignment() -> None:
    tree = ast.parse("env['PYTHONDONTWRITEBYTECODE'] = '1'\n")

    assert _has_env_bytecode_suppression(tree)


# --- Detector self-tests (synthetic AST, no repository dependency) ---------


def test_detector_flags_missing_flag_in_list_site() -> None:
    tree = ast.parse("import sys\nsubprocess.run([sys.executable, str(target)])\n")
    sites = _interpreter_spawn_sites(tree)
    assert sites
    assert not any(_has_bytecode_suppression_flag(rendered) for _, rendered in sites)


def test_detector_passes_when_flag_present_in_list_site() -> None:
    tree = ast.parse('import sys\nsubprocess.run([sys.executable, "-B", str(target)])\n')
    sites = _interpreter_spawn_sites(tree)
    assert sites
    assert all(_has_bytecode_suppression_flag(rendered) for _, rendered in sites)


def test_detector_flags_missing_flag_in_command_string_site() -> None:
    tree = ast.parse('command = f"python3 {path} {name}"\n')
    sites = _interpreter_spawn_sites(tree)
    assert sites
    assert not any(_has_bytecode_suppression_flag(rendered) for _, rendered in sites)


def test_detector_passes_when_flag_present_in_command_string_site() -> None:
    tree = ast.parse('command = f"python3 -B {path} {name}"\n')
    sites = _interpreter_spawn_sites(tree)
    assert sites
    assert all(_has_bytecode_suppression_flag(rendered) for _, rendered in sites)


def test_detector_catches_command_built_into_a_local_before_being_returned() -> None:
    """Regression coverage for the real ``_build_hook_command`` shape: a
    command f-string assigned to a local variable, then only referenced by
    name in the dict/return — not written inline at the dict site itself.
    """
    tree = ast.parse(
        "def _build(hooks_dir, script):\n"
        "    logical_name = script\n"
        "    command = f'python3 {hooks_dir}/_dispatch.py {logical_name}'\n"
        "    cmd = {'type': 'command', 'command': command}\n"
        "    return cmd\n"
    )
    sites = _interpreter_spawn_sites(tree)
    assert sites
    assert not any(_has_bytecode_suppression_flag(rendered) for _, rendered in sites)


def test_detector_ignores_unrelated_python3_mentions() -> None:
    """A message that merely *mentions* python3 mid-string is not a spawn site.

    Regression coverage for the real shape in ``guards/quota_guard.py``: an
    instructional string telling an agent what shell command to run next,
    where "python3" appears after other text rather than at the string's
    own start.
    """
    tree = ast.parse('msg = f"Call run_cmd with: python3 -c \\"import time; time.sleep({n})\\""\n')
    assert _interpreter_spawn_sites(tree) == []


def test_detector_ignores_bare_interpreter_name_comparison() -> None:
    """A basename equality check like ``base == "python3"`` is not a spawn site."""
    tree = ast.parse('if base == "python3":\n    pass\n')
    assert _interpreter_spawn_sites(tree) == []


def test_detector_ignores_sys_executable_outside_a_list() -> None:
    """``Path(sys.executable)`` (locating a sibling binary) is not argv construction."""
    tree = ast.parse("import sys\ncandidate = Path(sys.executable).parent / 'ruff'\n")
    assert _interpreter_spawn_sites(tree) == []
