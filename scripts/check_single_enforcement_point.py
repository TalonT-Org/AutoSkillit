#!/usr/bin/env python3
"""Verify each registered policy function is invoked from exactly one caller.

AST-scans src/autoskillit/ for call sites of each name in POLICY_FUNCTIONS.
"Call site" means the distinct (file, enclosing function) pair that contains
the call — two calls in the same function count once; the same function
name called from two different functions (even in the same file, even one
of them being the policy's own definition module) counts as two. This is
the double-gate shape #4684 found: assert_interactive_ordering and
validate_interactive_invocation both independently called
_interactive_invocation_environment_policy, so a caller fixing one gate
left the other silently still firing.

A caller may itself be a thin wrapper — a function whose entire body is
`return <call to the policy>(...)`. Callers of that wrapper are transitively
callers of the policy too (the equivalence class), so wrapping the policy in
a second indirection doesn't evade this check.

Import-alias resolution handles `from X import Y as Z`, `import X` (then
`X.Y(...)`), and re-exports — a call is attributed to the policy function
by resolving the call's callee name through the file's own import map back
to (defining_module, function_name), not by literal spelling.

Exit 0 if every policy function has exactly one call site (module-qualified
exemptions in BACKEND_SPECIFIC_EXEMPT_FUNCTIONS may permit exactly two, iff
the second is a Codex no-op — see docstring on that constant). Exit 1 with
details on violations.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parent.parent / "src" / "autoskillit"

#: Policy functions this gate protects. Each entry is (function_name,
#: defining_module_relpath) — the relpath disambiguates same-named functions
#: defined in different modules (there are none today, but the AST scan is
#: name-based, so this keeps the registry honest about which definition a
#: name refers to).
POLICY_FUNCTIONS: tuple[tuple[str, str], ...] = (
    ("_interactive_invocation_environment_policy", "execution/backends/claude.py"),
)

#: Policy functions permitted exactly two call sites, iff the second site is
#: an explicit backend-specific no-op. _interactive_invocation_environment_policy
#: is Claude-only (Codex has no agent-teams concept — codex.py's
#: force_inactive_agent_teams parameters are all inert, each carrying the
#: literal trailing comment "# no-op: Codex has no team concept"). A second
#: call site is exempt only when it lives in execution/backends/codex.py AND
#: the enclosing function's source contains that exact comment marker.
BACKEND_SPECIFIC_EXEMPT_FUNCTIONS: frozenset[str] = frozenset()

_CODEX_NO_OP_MARKER = "# no-op: Codex has no team concept"
_CODEX_MODULE_RELPATH = "execution/backends/codex.py"


class _ImportMap:
    """Resolve a bare or attribute-qualified call name to (module, function_name)."""

    def __init__(self, tree: ast.Module) -> None:
        # local_name -> (source_module, original_name)
        self._from_imports: dict[str, tuple[str, str]] = {}
        # local_alias -> module_name (for `import X as alias` / `import X`)
        self._module_imports: dict[str, str] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module is not None:
                for alias in node.names:
                    local = alias.asname or alias.name
                    self._from_imports[local] = (node.module, alias.name)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    local = alias.asname or alias.name.split(".")[0]
                    self._module_imports[local] = alias.name

    def resolve_call(self, call: ast.Call) -> tuple[str, str] | None:
        """Return (file_relpath, function_name) the call's callee resolves to, if known.

        Import statements name a dotted Python module (``autoskillit.x.y``);
        the rest of this scanner keys everything by file path relative to
        SRC_ROOT (``x/y.py``) — _module_to_relpath bridges the two so a
        resolved import can be compared against the file-relpath-keyed
        equivalence class.
        """
        func = call.func
        if isinstance(func, ast.Name):
            if func.id in self._from_imports:
                module, name = self._from_imports[func.id]
                return (_module_to_relpath(module), name)
            return None
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
            module = self._module_imports.get(func.value.id)
            if module is not None:
                return (_module_to_relpath(module), func.attr)
        return None


def _module_to_relpath(module: str) -> str:
    """Convert a dotted import module name to a file path relative to SRC_ROOT.

    Strips the root package prefix (``SRC_ROOT.name`` — "autoskillit" in
    production; a synthetic test's SRC_ROOT has no such prefix in its own
    import names, so this is a no-op there).
    """
    prefix = SRC_ROOT.name + "."
    if module.startswith(prefix):
        module = module[len(prefix) :]
    elif module == SRC_ROOT.name:
        module = ""
    return module.replace(".", "/") + ".py"


def _enclosing_function(
    node: ast.AST, parents: dict[ast.AST, ast.AST]
) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    """Walk up the parent chain to the nearest FunctionDef/AsyncFunctionDef, or None."""
    current = parents.get(node)
    while current is not None:
        if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return current
        current = parents.get(current)
    return None


def _build_parent_map(tree: ast.Module) -> dict[ast.AST, ast.AST]:
    parents: dict[ast.AST, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[child] = parent
    return parents


def _is_pure_return_wrapper(func: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """True iff func's body is exactly one `return <call>(...)` statement."""
    body = [n for n in func.body if not isinstance(n, ast.Expr) or not _is_docstring(n)]
    if len(body) != 1 or not isinstance(body[0], ast.Return):
        return False
    return isinstance(body[0].value, ast.Call)


def _is_docstring(node: ast.Expr) -> bool:
    return isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)


def _site_key(file_relpath: str, enclosing: ast.AST | None, func_short_name: str) -> str:
    if enclosing is None:
        return f"{file_relpath}:<module>"
    name = getattr(enclosing, "name", func_short_name)
    return f"{file_relpath}:{name}"


def find_call_sites(func_name: str, defining_module_relpath: str) -> list[str]:
    """Return the equivalence-class-resolved (file:enclosing_function) call sites.

    Includes indirect callers of pure-return-wrapper functions around the
    named policy function.
    """
    py_files = sorted(SRC_ROOT.rglob("*.py"))
    parsed: dict[str, tuple[ast.Module, dict[ast.AST, ast.AST], _ImportMap]] = {}
    for path in py_files:
        relpath = str(path.relative_to(SRC_ROOT))
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        parsed[relpath] = (tree, _build_parent_map(tree), _ImportMap(tree))

    # Equivalence class of names that resolve to the policy: start with the
    # policy's own (module, name); grow by one BFS pass over pure-return
    # wrapper functions that call something already in the class.
    equivalence: set[tuple[str, str]] = {(defining_module_relpath, func_name)}
    wrapper_names: set[tuple[str, str]] = set()
    changed = True
    while changed:
        changed = False
        for relpath, (tree, parents, imports) in parsed.items():
            for node in ast.walk(tree):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                if not _is_pure_return_wrapper(node):
                    continue
                return_stmt = node.body[-1]
                assert isinstance(return_stmt, ast.Return)
                call = return_stmt.value
                assert isinstance(call, ast.Call)
                resolved = imports.resolve_call(call)
                if resolved is None and isinstance(call.func, ast.Name):
                    # Wrapper calling a sibling defined in the same module.
                    resolved = (relpath, call.func.id)
                if resolved is not None and resolved in equivalence:
                    key = (relpath, node.name)
                    if key not in equivalence:
                        equivalence.add(key)
                        wrapper_names.add(key)
                        changed = True

    sites: set[str] = set()
    for relpath, (tree, parents, imports) in parsed.items():
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            resolved = imports.resolve_call(node)
            if resolved is None and isinstance(node.func, ast.Name):
                resolved = (relpath, node.func.id)
            if resolved is None or resolved not in equivalence:
                continue
            enclosing = _enclosing_function(node, parents)
            if enclosing is not None and (relpath, enclosing.name) in wrapper_names:
                # The wrapper's own internal call to the policy is not itself
                # an external call site — it's what made the wrapper part of
                # the equivalence class in the first place.
                continue
            sites.add(_site_key(relpath, enclosing, func_name))
    return sorted(sites)


def _codex_site_is_exempt(site: str) -> bool:
    if not site.startswith(_CODEX_MODULE_RELPATH + ":"):
        return False
    func_name = site.split(":", 1)[1]
    path = SRC_ROOT / _CODEX_MODULE_RELPATH
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == func_name:
            segment = ast.get_source_segment(path.read_text(encoding="utf-8"), node) or ""
            return _CODEX_NO_OP_MARKER in segment
    return False


def check() -> list[str]:
    violations: list[str] = []
    for func_name, defining_module in POLICY_FUNCTIONS:
        sites = find_call_sites(func_name, defining_module)
        allowed = 2 if func_name in BACKEND_SPECIFIC_EXEMPT_FUNCTIONS else 1
        if len(sites) <= allowed:
            continue
        if (
            func_name in BACKEND_SPECIFIC_EXEMPT_FUNCTIONS
            and len(sites) == 2
            and any(_codex_site_is_exempt(s) for s in sites)
        ):
            continue
        violations.append(
            f"{func_name} (defined in {defining_module}) has {len(sites)} call sites "
            f"(expected {allowed}): {sites}"
        )
    return violations


def main() -> int:
    violations = check()
    if violations:
        print("Duplicate policy-function call sites found:\n")
        for v in violations:
            print(f"  {v}")
        print(
            "\nA policy function must be invoked from exactly one gate. "
            "Consolidate the duplicate callers into a single checkpoint."
        )
        return 1
    print("Every registered policy function has a single enforcement point.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
