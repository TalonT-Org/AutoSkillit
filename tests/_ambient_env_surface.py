"""AST inventory of every ambient-environment-variable touch point in production code.

Scans ``src/autoskillit/**/*.py`` for every syntactic shape that reads, writes,
or wholesale-forwards ``os.environ`` (R1-R7 below) and resolves each site back
to a literal variable name wherever the argument is a string literal or a
module-level constant/collection (imported or locally declared). The result is
the ground truth a test-ambient-env scrub fixture must be checked against: any
name the scanner finds that isn't classified in ``AMBIENT_ENV_DISPOSITIONS``
is a production dependency the fixture doesn't know about yet.

Resolution runs in two conceptual passes. Pass 1 builds two symbol tables by
walking every module's top-level statements: scalar string constants
(``NAME: str = "LITERAL"`` / ``NAME = "LITERAL"``) and collection bindings
(frozenset/set/tuple/list/dict literals, including ``frozenset(OTHER_NAME)``
wrappers and ``*OTHER_NAME`` splats, resolved against constants and
collections already seen earlier in the same file). Both tables carry a
module-local layer and a flat cross-module fallback layer, because production
code imports these constants by name rather than re-declaring them — a read
site in module B routinely names a constant defined in module A. Pass 2 walks
every module's full AST applying the R1-R7 rules against those tables.

Rules:

- R1 direct literal reads: ``os.environ.get/getenv/[]/pop/setdefault``, and
  ``in os.environ`` membership, where the key argument is a string literal.
- R2 same call shapes where the key argument is a ``Name``/``Attribute``
  resolvable through the Pass 1 tables.
- R3 keyword arguments and annotated assignments whose target name matches
  ``env_var``/``env`` (case-insensitive, optionally suffixed by ``_``), with
  a resolvable non-empty string value — e.g.
  ``BackendCapabilities(..., explicit_path_env_var="CLAUDE_CODE_EXECPATH")``.
- R4 env-name collections: module-level frozenset/set/tuple/list bindings
  whose name contains "ENV" or whose members are uniformly UPPER_SNAKE_CASE.
- R5 prefix-denylist collections (name ends in ``ENV_PREFIX_DENYLIST``) feed
  ``surface.prefixes`` instead of ``surface.names``.
- R6 unresolvable R1/R2-shaped arguments (f-strings, subscripts, function
  parameters, comprehension variables) are recorded for manual audit rather
  than silently dropped.
- R7 wholesale-forwarding sites: copies/comprehensions/unions of the entire
  ``os.environ`` mapping, and bare ``os.environ`` passed as an ``*env``
  keyword argument — these bypass name-level resolution entirely and are
  tracked separately so they can be reviewed as a class.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

_ENV_VAR_TARGET_RE = re.compile(r"(?i)(^|_)env_var$")
_ENV_TARGET_RE = re.compile(r"(?i)(^|_)env$")
_ENV_PREFIX_DENYLIST_NAME_RE = re.compile(r"ENV_PREFIX_DENYLIST$")
_UPPER_SNAKE_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
_FORWARDING_KEYWORD_RE = re.compile(r"(?i)env$|^child_env$|_env$")

_ENVIRON_ATTR_NAMES = frozenset({"environ", "environb"})
_ENVIRON_READ_METHODS = frozenset({"get", "pop", "setdefault"})
_COLLECTION_WRAPPER_FUNCS = frozenset({"frozenset", "set", "tuple", "list"})


@dataclass(frozen=True, slots=True)
class EnvRead:
    var: str
    file: str  # repo-relative
    line: int
    rule: str  # which scanner rule matched — R1..R7


@dataclass(frozen=True, slots=True)
class UnresolvedRead:
    file: str
    line: int
    expression: str  # ast.unparse of the unresolved argument


@dataclass(frozen=True, slots=True)
class ForwardingSite:
    file: str
    line: int
    exclusion_set: str  # identifier of the denylist subtracted, or "" if none


@dataclass(frozen=True, slots=True)
class ProductionEnvSurface:
    names: frozenset[str]
    prefixes: frozenset[str]
    reads: tuple[EnvRead, ...]
    unresolved: tuple[UnresolvedRead, ...]
    forwarding_sites: tuple[ForwardingSite, ...]
    unparseable_files: tuple[str, ...]


def _module_level_binding(stmt: ast.stmt) -> tuple[str | None, ast.expr | None]:
    if (
        isinstance(stmt, ast.AnnAssign)
        and isinstance(stmt.target, ast.Name)
        and stmt.value is not None
    ):
        return stmt.target.id, stmt.value
    if (
        isinstance(stmt, ast.Assign)
        and len(stmt.targets) == 1
        and isinstance(stmt.targets[0], ast.Name)
    ):
        return stmt.targets[0].id, stmt.value
    return None, None


def _collect_scalars(tree: ast.Module) -> dict[str, str]:
    scalars: dict[str, str] = {}
    for stmt in tree.body:
        target, value = _module_level_binding(stmt)
        if target is None:
            continue
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            scalars[target] = value.value
    return scalars


def _resolve_elts(
    elts: list[ast.expr],
    flat_scalars: dict[str, str],
    local_collections: dict[str, frozenset[str]],
    flat_collections: dict[str, frozenset[str]],
) -> frozenset[str]:
    members: set[str] = set()
    for el in elts:
        if isinstance(el, ast.Starred):
            ref = el.value
            if isinstance(ref, ast.Name):
                resolved = local_collections.get(ref.id) or flat_collections.get(ref.id)
                if resolved:
                    members.update(resolved)
            continue
        if isinstance(el, ast.Constant) and isinstance(el.value, str):
            members.add(el.value)
            continue
        if isinstance(el, ast.Name):
            val = flat_scalars.get(el.id)
            if val is not None:
                members.add(val)
    return frozenset(members)


def _resolve_collection_members(
    node: ast.expr,
    flat_scalars: dict[str, str],
    local_collections: dict[str, frozenset[str]],
    flat_collections: dict[str, frozenset[str]],
) -> frozenset[str] | None:
    if isinstance(node, (ast.Set, ast.List, ast.Tuple)):
        return _resolve_elts(node.elts, flat_scalars, local_collections, flat_collections)
    if isinstance(node, ast.Dict):
        return frozenset(
            k.value for k in node.keys if isinstance(k, ast.Constant) and isinstance(k.value, str)
        )
    if isinstance(node, ast.Call):
        func = node.func
        if (
            isinstance(func, ast.Name)
            and func.id in _COLLECTION_WRAPPER_FUNCS
            and len(node.args) == 1
            and not node.keywords
        ):
            arg = node.args[0]
            if isinstance(arg, ast.Name):
                return local_collections.get(arg.id, flat_collections.get(arg.id))
            return _resolve_collection_members(
                arg, flat_scalars, local_collections, flat_collections
            )
    return None


def _resolve_key_expr(
    node: ast.expr, module_scalars: dict[str, str], flat_scalars: dict[str, str]
) -> tuple[str, str] | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value, "R1"
    if isinstance(node, ast.Name):
        if node.id in module_scalars:
            return module_scalars[node.id], "R2"
        if node.id in flat_scalars:
            return flat_scalars[node.id], "R2"
        return None
    if isinstance(node, ast.Attribute):
        key = node.attr
        if key in module_scalars:
            return module_scalars[key], "R2"
        if key in flat_scalars:
            return flat_scalars[key], "R2"
        return None
    return None


def _is_environ_attr(node: ast.expr) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and node.attr in _ENVIRON_ATTR_NAMES
        and isinstance(node.value, ast.Name)
        and node.value.id == "os"
    )


def _get_call_key_arg(node: ast.Call) -> ast.expr | None:
    for kw in node.keywords:
        if kw.arg == "key":
            return kw.value
        if kw.arg is None and isinstance(kw.value, ast.Dict):
            for k, v in zip(kw.value.keys, kw.value.values, strict=True):
                if isinstance(k, ast.Constant) and k.value == "key":
                    return v
    if node.args:
        return node.args[0]
    return None


def _unwrap_namedexpr(node: ast.expr) -> ast.expr:
    return node.value if isinstance(node, ast.NamedExpr) else node


def _record_key_arg(
    site: ast.expr,
    arg: ast.expr | None,
    rel: str,
    module_scalars: dict[str, str],
    flat_scalars: dict[str, str],
    reads: list[EnvRead],
    unresolved: list[UnresolvedRead],
) -> None:
    if arg is None:
        return
    arg = _unwrap_namedexpr(arg)
    resolved = _resolve_key_expr(arg, module_scalars, flat_scalars)
    if resolved is not None:
        value, rule = resolved
        reads.append(EnvRead(var=value, file=rel, line=site.lineno, rule=rule))
        return
    unresolved.append(UnresolvedRead(file=rel, line=site.lineno, expression=ast.unparse(arg)))


def _handle_call(
    node: ast.Call,
    rel: str,
    module_scalars: dict[str, str],
    flat_scalars: dict[str, str],
    reads: list[EnvRead],
    unresolved: list[UnresolvedRead],
) -> None:
    func = node.func
    if not isinstance(func, ast.Attribute):
        return
    if func.attr == "getenv" and isinstance(func.value, ast.Name) and func.value.id == "os":
        _record_key_arg(
            node, _get_call_key_arg(node), rel, module_scalars, flat_scalars, reads, unresolved
        )
        return
    if func.attr in _ENVIRON_READ_METHODS and _is_environ_attr(func.value):
        # _get_call_key_arg resolves the `key=`/positional-first-arg form uniformly for
        # get/pop/setdefault (and getenv above) -- os.environ's mixin methods and
        # os.getenv all name their first parameter `key`, so an all-keyword call
        # (e.g. os.environ.pop(key=...)) is not silently dropped.
        _record_key_arg(
            node, _get_call_key_arg(node), rel, module_scalars, flat_scalars, reads, unresolved
        )


def _handle_subscript(
    node: ast.Subscript,
    rel: str,
    module_scalars: dict[str, str],
    flat_scalars: dict[str, str],
    reads: list[EnvRead],
    unresolved: list[UnresolvedRead],
) -> None:
    if not _is_environ_attr(node.value):
        return
    _record_key_arg(node, node.slice, rel, module_scalars, flat_scalars, reads, unresolved)


def _handle_membership(
    node: ast.Compare,
    rel: str,
    module_scalars: dict[str, str],
    flat_scalars: dict[str, str],
    reads: list[EnvRead],
    unresolved: list[UnresolvedRead],
) -> None:
    if len(node.ops) != 1 or not isinstance(node.ops[0], (ast.In, ast.NotIn)):
        return
    if not _is_environ_attr(node.comparators[0]):
        return
    _record_key_arg(node, node.left, rel, module_scalars, flat_scalars, reads, unresolved)


def _handle_env_named_site(
    name: str,
    value_node: ast.expr,
    site: ast.expr | ast.stmt | ast.keyword,
    rel: str,
    module_scalars: dict[str, str],
    flat_scalars: dict[str, str],
    reads: list[EnvRead],
) -> None:
    if not (_ENV_VAR_TARGET_RE.search(name) or _ENV_TARGET_RE.search(name)):
        return
    value_node = _unwrap_namedexpr(value_node)
    resolved = _resolve_key_expr(value_node, module_scalars, flat_scalars)
    if resolved is None:
        return
    value, rule = resolved
    if not value:
        return
    reads.append(EnvRead(var=value, file=rel, line=site.lineno, rule="R3"))


_FORWARDING_WRAPPER_FUNCS = frozenset({"dict", "MappingProxyType"})


def _wrapper_arg_resolves_to_environ(node: ast.expr) -> bool:
    """True if a dict()/MappingProxyType()/BinOp operand is (or unwraps via an
    ``X if cond else os.environ``-style ternary to) bare ``os.environ``.

    Scoped to unwrapping *inside* a recognized wrapper call — a bare
    ``env = os.environ if cond else other`` assignment with no such wrapper is
    a distinct fallback-default pattern this scanner does not classify as
    forwarding (unlike Shape 2's bare-keyword-argument case, its risk profile
    depends on call-site control flow this AST-only scanner cannot resolve).
    """
    if _is_environ_attr(node):
        return True
    if isinstance(node, ast.IfExp):
        return _wrapper_arg_resolves_to_environ(node.body) or _wrapper_arg_resolves_to_environ(
            node.orelse
        )
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in _FORWARDING_WRAPPER_FUNCS
        and len(node.args) == 1
        and not node.keywords
    ):
        return _wrapper_arg_resolves_to_environ(node.args[0])
    return False


def _environ_forwarding_source(node: ast.expr) -> bool:
    if isinstance(node, ast.Call):
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr in ("copy", "items"):
            return _is_environ_attr(func.value)
        if (
            isinstance(func, ast.Name)
            and func.id in _FORWARDING_WRAPPER_FUNCS
            and len(node.args) == 1
            and not node.keywords
        ):
            return _wrapper_arg_resolves_to_environ(node.args[0])
        return False
    if isinstance(node, ast.Dict):
        return any(
            k is None and _is_environ_attr(v) for k, v in zip(node.keys, node.values, strict=True)
        )
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        return _wrapper_arg_resolves_to_environ(node.left) or _wrapper_arg_resolves_to_environ(
            node.right
        )
    return False


def _exclusion_identifier(comparator: ast.expr) -> str:
    if isinstance(comparator, ast.Name):
        return comparator.id
    if isinstance(comparator, ast.Attribute):
        return comparator.attr
    return ""


def _comprehension_bound_names(target: ast.expr) -> set[str]:
    if isinstance(target, ast.Name):
        return {target.id}
    if isinstance(target, (ast.Tuple, ast.List)):
        names: set[str] = set()
        for el in target.elts:
            names.update(_comprehension_bound_names(el))
        return names
    return set()


def _comprehension_exclusion_set(gen: ast.comprehension) -> str:
    bound = _comprehension_bound_names(gen.target)
    if not bound:
        return ""
    for cond in gen.ifs:
        if not isinstance(cond, ast.Compare) or len(cond.ops) != 1:
            continue
        if not isinstance(cond.ops[0], ast.NotIn):
            continue
        if not isinstance(cond.left, ast.Name) or cond.left.id not in bound:
            continue
        found = _exclusion_identifier(cond.comparators[0])
        if found:
            return found
    return ""


def production_env_read_surface(src_root: Path) -> ProductionEnvSurface:
    files = sorted(src_root.rglob("*.py"))
    trees: dict[Path, ast.Module] = {}
    unparseable: list[str] = []
    for f in files:
        try:
            trees[f] = ast.parse(f.read_text(encoding="utf-8"), filename=str(f))
        except SyntaxError:
            unparseable.append(f.relative_to(src_root).as_posix())
            continue

    module_scalars: dict[Path, dict[str, str]] = {}
    flat_scalars: dict[str, str] = {}
    for f, tree in trees.items():
        scalars = _collect_scalars(tree)
        module_scalars[f] = scalars
        flat_scalars.update(scalars)

    module_collections: dict[Path, dict[str, frozenset[str]]] = {}
    flat_collections: dict[str, frozenset[str]] = {}
    for f, tree in trees.items():
        local: dict[str, frozenset[str]] = {}
        for stmt in tree.body:
            target, value = _module_level_binding(stmt)
            if target is None or value is None:
                continue
            members = _resolve_collection_members(value, flat_scalars, local, flat_collections)
            if members is not None:
                local[target] = members
        module_collections[f] = local
        flat_collections.update(local)

    reads: list[EnvRead] = []
    unresolved: list[UnresolvedRead] = []
    forwarding: list[ForwardingSite] = []
    prefixes: set[str] = set()

    for f, tree in trees.items():
        rel = f.relative_to(src_root).as_posix()
        scalars = module_scalars[f]
        collections = module_collections[f]

        for stmt in tree.body:
            target, value = _module_level_binding(stmt)
            if target is None or value is None:
                continue
            members = collections.get(target)
            if members is None:
                continue
            if _ENV_PREFIX_DENYLIST_NAME_RE.search(target):
                prefixes.update(members)
                continue
            name_matches = "env" in target.lower()
            members_upper = bool(members) and all(_UPPER_SNAKE_RE.match(m) for m in members)
            if name_matches or members_upper:
                for member in members:
                    reads.append(EnvRead(var=member, file=rel, line=value.lineno, rule="R4"))

        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                _handle_call(node, rel, scalars, flat_scalars, reads, unresolved)
            elif isinstance(node, ast.Subscript):
                _handle_subscript(node, rel, scalars, flat_scalars, reads, unresolved)
            elif isinstance(node, ast.Compare):
                _handle_membership(node, rel, scalars, flat_scalars, reads, unresolved)
            elif isinstance(node, ast.keyword):
                if node.arg is not None:
                    _handle_env_named_site(
                        node.arg, node.value, node, rel, scalars, flat_scalars, reads
                    )
                    if _FORWARDING_KEYWORD_RE.search(node.arg) and _is_environ_attr(node.value):
                        forwarding.append(
                            ForwardingSite(file=rel, line=node.lineno, exclusion_set="")
                        )
            elif isinstance(node, ast.AnnAssign):
                if isinstance(node.target, ast.Name) and node.value is not None:
                    _handle_env_named_site(
                        node.target.id, node.value, node, rel, scalars, flat_scalars, reads
                    )
                if node.value is not None and _environ_forwarding_source(node.value):
                    forwarding.append(
                        ForwardingSite(file=rel, line=node.value.lineno, exclusion_set="")
                    )
            elif isinstance(node, ast.Assign):
                if _environ_forwarding_source(node.value):
                    forwarding.append(
                        ForwardingSite(file=rel, line=node.value.lineno, exclusion_set="")
                    )
            elif isinstance(node, ast.Return):
                if node.value is not None and _environ_forwarding_source(node.value):
                    forwarding.append(
                        ForwardingSite(file=rel, line=node.value.lineno, exclusion_set="")
                    )
            elif isinstance(node, (ast.DictComp, ast.SetComp, ast.ListComp, ast.GeneratorExp)):
                for gen in node.generators:
                    if not _environ_forwarding_source(gen.iter):
                        continue
                    forwarding.append(
                        ForwardingSite(
                            file=rel,
                            line=gen.iter.lineno,
                            exclusion_set=_comprehension_exclusion_set(gen),
                        )
                    )

    names = frozenset(r.var for r in reads)
    return ProductionEnvSurface(
        names=names,
        prefixes=frozenset(prefixes),
        reads=tuple(reads),
        unresolved=tuple(unresolved),
        forwarding_sites=tuple(forwarding),
        unparseable_files=tuple(unparseable),
    )


@dataclass(frozen=True, slots=True)
class AmbientEnvDisposition:
    var: str
    disposition: Literal["scrub", "preserve"]
    owner: str  # "autoskillit" | "claude-code" | "codex" | "anthropic" | "posix" | "harness"
    justification: str  # >= 40 chars


DYNAMIC_READ_EXEMPTIONS: dict[str, str] = {
    "execution/evidence_reader.py:138": (
        "Dict/generator-comprehension key bound by `for name in _PROVIDER_ENV`; this scanner does"
        "not trace comprehension-bound names back through their iterable's members."
    ),
    "hooks/_hook_settings.py:315": (
        "The `env_var` function parameter of _resolve_int() is supplied dynamically per caller;"
        "not a module-level literal or resolvable constant."
    ),
    "server/_guards.py:361": (
        "`profile.api_key_env` is a per-provider-profile instance attribute resolved at runtime"
        "from config, not a module-level constant this AST scanner can resolve."
    ),
    "server/_lifespan/_session_boots.py:531": (
        "Dict-comprehension key bound by `for name in EVIDENCE_READER_ENV_FORWARD_VARS`; the three"
        "forwarded names are already captured directly via that collection's own R4 scan."
    ),
    "server/_session_type.py:49": (
        "Set-comprehension key bound by `for key in EVIDENCE_READER_ENV_FORWARD_VARS`; the"
        "forwarded names are already captured directly via that collection's own R4 scan."
    ),
    "server/_session_type.py:54": (
        "Generator-expression key bound by `for key in EVIDENCE_READER_ENV_FORWARD_VARS`; the"
        "forwarded names are already captured directly via that collection's own R4 scan."
    ),
    "server/_session_type.py:61": (
        "Dict-comprehension key bound by `for key in _EXPLORER_BINDING_ENV_KEYS`; the four"
        "explorer-binding names are already captured directly via that collection's own R4 scan."
    ),
    "server/tools/tools_evidence_reader.py:174": (
        "Dict-comprehension key bound by `for name in EVIDENCE_READER_ENV_FORWARD_VARS`; the three"
        "forwarded names are already captured directly via that collection's own R4 scan."
    ),
}

FORWARDING_SITES: dict[str, str] = {
    "cli/_marketplace.py:260": (
        "Unfiltered dict(os.environ) snapshot (`ambient_env`) used as the base for an"
        "install/publish subprocess env; intentional wholesale forward for a maintenance-style"
        "operation."
    ),
    "cli/app.py:308": (
        "Bare os.environ passed as `child_env` to the maintenance installer, which itself applies"
        "an explicit allowlist (build_maintenance_env) before spawning; unfiltered by design here."
    ),
    "cli/session/_session_launch.py:99": (
        "Unfiltered dict(os.environ) used only to probe an exact executable path before the real"
        "session env is sealed by build_agent_env elsewhere; not the launched child's env."
    ),
    "cli/update/_transaction.py:350": (
        "Unfiltered dict(os.environ if base_env is None else base_env) snapshot captured for"
        "update-transaction diagnostics/rollback comparison, not for a spawned child process."
    ),
    "cli/update/_update_checks_source.py:130": (
        "Bare os.environ passed as `env=` to a read-only `git ls-remote` probe subprocess;"
        "unfiltered wholesale forward for a local diagnostic command."
    ),
    "execution/backends/_codex_probes.py:345": (
        "Unfiltered dict(os.environ) base for the global-Codex-home MCP-inventory validation"
        "probe subprocess, with CODEX_COOK_RESERVED_ENV_VARS overridden to the source home."
    ),
    "execution/backends/claude.py:650": (
        "Excludes _INTERACTIVE_ENV_EXCLUSIONS (TERM/NO_COLOR headless-hardening keys) when"
        "building the interactive Claude Code base env."
    ),
    "execution/backends/claude.py:867": (
        "Excludes _HEADLESS_EXCLUSIVE_VARS before build_agent_env layers extras back in for a"
        "headless Claude Code skill-session launch."
    ),
    "execution/backends/claude.py:979": (
        "Excludes _HEADLESS_EXCLUSIVE_VARS before build_agent_env layers extras back in for a"
        "headless Claude Code food-truck orchestrator-session launch."
    ),
    "execution/backends/codex.py:375": (
        "Excludes _HEADLESS_EXCLUSIVE_VARS before build_env layers extras back in for a headless"
        "Codex generic-prompt launch."
    ),
    "execution/backends/codex.py:517": (
        "Excludes _HEADLESS_EXCLUSIVE_VARS before build_env layers extras back in for a headless"
        "Codex skill-session launch."
    ),
    "execution/backends/codex.py:652": (
        "Excludes _HEADLESS_EXCLUSIVE_VARS before build_env layers extras back in for a headless"
        "Codex food-truck orchestrator-session launch."
    ),
    "execution/backends/codex.py:772": (
        "Excludes _HEADLESS_EXCLUSIVE_VARS before extras merge for a Codex interactive-session"
        "launch base env."
    ),
    "execution/backends/codex.py:845": (
        "Excludes _HEADLESS_EXCLUSIVE_VARS before build_env layers extras back in for a headless"
        "Codex resume-session launch."
    ),
    "execution/merge_queue/_merge_queue_group_ci.py:109": (
        "Unfiltered {**os.environ} base for a local `gh run list` CI-status probe subprocess;"
        "GH_TOKEN is the only key ever added on top."
    ),
    "execution/testing.py:37": (
        "Excludes AUTOSKILLIT_PRIVATE_ENV_VARS before handing the sanitized env to a user-code"
        "`pytest` subprocess spawned by test_check."
    ),
    "exploration/snapshot.py:200": (
        "Unfiltered dict(os.environ) base for a local read-only `git` subprocess (snapshot"
        "diffing); only GIT_OPTIONAL_LOCKS is added on top."
    ),
    "hooks/_capture_process.py:435": (
        "dict(os.environ) followed by an explicit per-key .pop() loop over"
        "PROTECTED_CAPTURE_ENV_VARS; the exclusion happens outside the single expression this"
        "scanner inspects."
    ),
    "hooks/_dispatch.py:53": (
        "Unfiltered dict(os.environ) base for a same-host hook-script subprocess; only"
        "PYTHONDONTWRITEBYTECODE is added on top."
    ),
    "smoke_utils/_cross_interpreter_upgrade.py:95": (
        "Unfiltered dict(os.environ) base for a smoke-test cross-interpreter upgrade subprocess"
        "sandboxed to a scratch HOME/XDG tree overridden immediately after."
    ),
}


_R4_COREUTILS_ENV_FLAG_JUSTIFICATION = (
    "R4 false positive: a coreutils env(1) command-line flag captured only because its"
    "defining collection name contains the substring ENV (the `env` shell command, not"
    "os.environ)."
)

AMBIENT_ENV_DISPOSITIONS: dict[str, AmbientEnvDisposition] = {
    **{
        flag: AmbientEnvDisposition(
            var=flag,
            disposition="scrub",
            owner="autoskillit",
            justification=_R4_COREUTILS_ENV_FLAG_JUSTIFICATION,
        )
        for flag in (
            "--argv0",
            "--block-signal",
            "--chdir",
            "--debug",
            "--default-signal",
            "--help",
            "--ignore-environment",
            "--ignore-signal",
            "--null",
            "--split-string",
            "--unset",
            "--version",
            "-0",
            "-C",
            "-S",
            "-V",
            "-i",
            "-u",
            "-v",
        )
    },
    "AGENT_BACKEND_CLAUDE_CODE": AmbientEnvDisposition(
        var="AGENT_BACKEND_CLAUDE_CODE",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "R4 predicate-(b) false positive: an all-uppercase enum/status/regex-name/label member"
            "of an unrelated lookup collection; never set as a real OS environment variable."
        ),
    ),
    "AGENT_BACKEND_CODEX": AmbientEnvDisposition(
        var="AGENT_BACKEND_CODEX",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "R4 predicate-(b) false positive: an all-uppercase enum/status/regex-name/label member"
            "of an unrelated lookup collection; never set as a real OS environment variable."
        ),
    ),
    "AGENT_BACKEND_DYNACONF_ENV_VAR": AmbientEnvDisposition(
        var="AGENT_BACKEND_DYNACONF_ENV_VAR",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "R4 predicate-(b) false positive: an all-uppercase enum/status/regex-name/label member"
            "of an unrelated lookup collection; never set as a real OS environment variable."
        ),
    ),
    "AGENT_BACKEND_ENV_VAR": AmbientEnvDisposition(
        var="AGENT_BACKEND_ENV_VAR",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "R4 predicate-(b) false positive: an all-uppercase enum/status/regex-name/label member"
            "of an unrelated lookup collection; never set as a real OS environment variable."
        ),
    ),
    "ALL_PROXY": AmbientEnvDisposition(
        var="ALL_PROXY",
        disposition="preserve",
        owner="posix",
        justification=(
            "Generic POSIX/system environment configuration (process"
            "locate/locale/proxy/certificate config) required for basic subprocess correctness;"
            "not AutoSkillit-private state."
        ),
    ),
    "ANTHROPIC_API_KEY": AmbientEnvDisposition(
        var="ANTHROPIC_API_KEY",
        disposition="scrub",
        owner="anthropic",
        justification=(
            "Anthropic provider credential or endpoint override in the _HEADLESS_EXCLUSIVE_VARS"
            "baseline; scrubbed to prevent cross-test credential leakage."
        ),
    ),
    "ANTHROPIC_AUTH_TOKEN": AmbientEnvDisposition(
        var="ANTHROPIC_AUTH_TOKEN",
        disposition="scrub",
        owner="anthropic",
        justification=(
            "Anthropic provider credential or endpoint override in the _HEADLESS_EXCLUSIVE_VARS"
            "baseline; scrubbed to prevent cross-test credential leakage."
        ),
    ),
    "ANTHROPIC_BASE_URL": AmbientEnvDisposition(
        var="ANTHROPIC_BASE_URL",
        disposition="scrub",
        owner="anthropic",
        justification=(
            "Anthropic provider credential or endpoint override in the _HEADLESS_EXCLUSIVE_VARS"
            "baseline; scrubbed to prevent cross-test credential leakage."
        ),
    ),
    "APPROVE": AmbientEnvDisposition(
        var="APPROVE",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "R4 predicate-(b) false positive: an all-uppercase enum/status/regex-name/label member"
            "of an unrelated lookup collection; never set as a real OS environment variable."
        ),
    ),
    "APPROVED": AmbientEnvDisposition(
        var="APPROVED",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "R4 predicate-(b) false positive: an all-uppercase enum/status/regex-name/label member"
            "of an unrelated lookup collection; never set as a real OS environment variable."
        ),
    ),
    "AUDIT_ADMISSION_AUTHORITY_PATH_ENV_VAR": AmbientEnvDisposition(
        var="AUDIT_ADMISSION_AUTHORITY_PATH_ENV_VAR",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "R4 predicate-(b) false positive: an all-uppercase enum/status/regex-name/label member"
            "of an unrelated lookup collection; never set as a real OS environment variable."
        ),
    ),
    "AUTOSKILLIT_AGENT_BACKEND": AmbientEnvDisposition(
        var="AUTOSKILLIT_AGENT_BACKEND",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "AutoSkillit-private session/orchestration variable in the"
            "AUTOSKILLIT_PRIVATE_ENV_VARS / _HEADLESS_EXCLUSIVE_VARS baseline; must not leak"
            "between sibling or nested sessions."
        ),
    ),
    "AUTOSKILLIT_AGENT_BACKEND__BACKEND": AmbientEnvDisposition(
        var="AUTOSKILLIT_AGENT_BACKEND__BACKEND",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "AutoSkillit-private session/orchestration variable in the"
            "AUTOSKILLIT_PRIVATE_ENV_VARS / _HEADLESS_EXCLUSIVE_VARS baseline; must not leak"
            "between sibling or nested sessions."
        ),
    ),
    "AUTOSKILLIT_ALLOWED_WRITE_PREFIX": AmbientEnvDisposition(
        var="AUTOSKILLIT_ALLOWED_WRITE_PREFIX",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "AutoSkillit-private session/orchestration variable in the"
            "AUTOSKILLIT_PRIVATE_ENV_VARS / _HEADLESS_EXCLUSIVE_VARS baseline; must not leak"
            "between sibling or nested sessions."
        ),
    ),
    "AUTOSKILLIT_ALLOWED_WRITE_PREFIXES": AmbientEnvDisposition(
        var="AUTOSKILLIT_ALLOWED_WRITE_PREFIXES",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "AutoSkillit-private session/orchestration variable in the"
            "AUTOSKILLIT_PRIVATE_ENV_VARS / _HEADLESS_EXCLUSIVE_VARS baseline; must not leak"
            "between sibling or nested sessions."
        ),
    ),
    "AUTOSKILLIT_APPLICABLE_GUARDS": AmbientEnvDisposition(
        var="AUTOSKILLIT_APPLICABLE_GUARDS",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "AutoSkillit-private session/orchestration variable in the"
            "AUTOSKILLIT_PRIVATE_ENV_VARS / _HEADLESS_EXCLUSIVE_VARS baseline; must not leak"
            "between sibling or nested sessions."
        ),
    ),
    "AUTOSKILLIT_ATTESTED_CLIENT_GATE_TOKENS": AmbientEnvDisposition(
        var="AUTOSKILLIT_ATTESTED_CLIENT_GATE_TOKENS",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "AutoSkillit-private session/orchestration variable in the"
            "AUTOSKILLIT_PRIVATE_ENV_VARS / _HEADLESS_EXCLUSIVE_VARS baseline; must not leak"
            "between sibling or nested sessions."
        ),
    ),
    "AUTOSKILLIT_ATTESTED_META_SUPPORT": AmbientEnvDisposition(
        var="AUTOSKILLIT_ATTESTED_META_SUPPORT",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "AutoSkillit-private session/orchestration variable in the"
            "AUTOSKILLIT_PRIVATE_ENV_VARS / _HEADLESS_EXCLUSIVE_VARS baseline; must not leak"
            "between sibling or nested sessions."
        ),
    ),
    "AUTOSKILLIT_AUDIT_ADMISSION_AUTHORITY_PATH": AmbientEnvDisposition(
        var="AUTOSKILLIT_AUDIT_ADMISSION_AUTHORITY_PATH",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "AutoSkillit-private session/orchestration variable in the"
            "AUTOSKILLIT_PRIVATE_ENV_VARS / _HEADLESS_EXCLUSIVE_VARS baseline; must not leak"
            "between sibling or nested sessions."
        ),
    ),
    "AUTOSKILLIT_CAMPAIGN_ID": AmbientEnvDisposition(
        var="AUTOSKILLIT_CAMPAIGN_ID",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "AutoSkillit-private session/orchestration variable in the"
            "AUTOSKILLIT_PRIVATE_ENV_VARS / _HEADLESS_EXCLUSIVE_VARS baseline; must not leak"
            "between sibling or nested sessions."
        ),
    ),
    "AUTOSKILLIT_CAMPAIGN_STATE_PATH": AmbientEnvDisposition(
        var="AUTOSKILLIT_CAMPAIGN_STATE_PATH",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "AutoSkillit-private session/orchestration variable in the"
            "AUTOSKILLIT_PRIVATE_ENV_VARS / _HEADLESS_EXCLUSIVE_VARS baseline; must not leak"
            "between sibling or nested sessions."
        ),
    ),
    "AUTOSKILLIT_CODEX_STARTUP_TRACE": AmbientEnvDisposition(
        var="AUTOSKILLIT_CODEX_STARTUP_TRACE",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "AutoSkillit-private session/orchestration variable in the"
            "AUTOSKILLIT_PRIVATE_ENV_VARS / _HEADLESS_EXCLUSIVE_VARS baseline; must not leak"
            "between sibling or nested sessions."
        ),
    ),
    "AUTOSKILLIT_COMPLETION_MARKER": AmbientEnvDisposition(
        var="AUTOSKILLIT_COMPLETION_MARKER",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "AutoSkillit-private session/orchestration variable in the"
            "AUTOSKILLIT_PRIVATE_ENV_VARS / _HEADLESS_EXCLUSIVE_VARS baseline; must not leak"
            "between sibling or nested sessions."
        ),
    ),
    "AUTOSKILLIT_CONTINUE_ON_FAILURE": AmbientEnvDisposition(
        var="AUTOSKILLIT_CONTINUE_ON_FAILURE",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "Real AutoSkillit orchestration/session-control environment variable read by"
            "production code; scrubbed as internal state that must not leak across test"
            "boundaries."
        ),
    ),
    "AUTOSKILLIT_CWD": AmbientEnvDisposition(
        var="AUTOSKILLIT_CWD",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "AutoSkillit-private session/orchestration variable in the"
            "AUTOSKILLIT_PRIVATE_ENV_VARS / _HEADLESS_EXCLUSIVE_VARS baseline; must not leak"
            "between sibling or nested sessions."
        ),
    ),
    "AUTOSKILLIT_DISPATCH_ID": AmbientEnvDisposition(
        var="AUTOSKILLIT_DISPATCH_ID",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "AutoSkillit-private session/orchestration variable in the"
            "AUTOSKILLIT_PRIVATE_ENV_VARS / _HEADLESS_EXCLUSIVE_VARS baseline; must not leak"
            "between sibling or nested sessions."
        ),
    ),
    "AUTOSKILLIT_EVIDENCE_READER_AUTHORITY": AmbientEnvDisposition(
        var="AUTOSKILLIT_EVIDENCE_READER_AUTHORITY",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "AutoSkillit-private session/orchestration variable in the"
            "AUTOSKILLIT_PRIVATE_ENV_VARS / _HEADLESS_EXCLUSIVE_VARS baseline; must not leak"
            "between sibling or nested sessions."
        ),
    ),
    "AUTOSKILLIT_EVIDENCE_READER_AUTHORITY_PATH": AmbientEnvDisposition(
        var="AUTOSKILLIT_EVIDENCE_READER_AUTHORITY_PATH",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "AutoSkillit-private session/orchestration variable in the"
            "AUTOSKILLIT_PRIVATE_ENV_VARS / _HEADLESS_EXCLUSIVE_VARS baseline; must not leak"
            "between sibling or nested sessions."
        ),
    ),
    "AUTOSKILLIT_EVIDENCE_READER_CAPABILITY": AmbientEnvDisposition(
        var="AUTOSKILLIT_EVIDENCE_READER_CAPABILITY",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "AutoSkillit-private session/orchestration variable in the"
            "AUTOSKILLIT_PRIVATE_ENV_VARS / _HEADLESS_EXCLUSIVE_VARS baseline; must not leak"
            "between sibling or nested sessions."
        ),
    ),
    "AUTOSKILLIT_EXPLORATION_AUTHORITY_PATH": AmbientEnvDisposition(
        var="AUTOSKILLIT_EXPLORATION_AUTHORITY_PATH",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "Real AutoSkillit orchestration/session-control environment variable read by"
            "production code; scrubbed as internal state that must not leak across test"
            "boundaries."
        ),
    ),
    "AUTOSKILLIT_EXPLORATION_CAPABILITY": AmbientEnvDisposition(
        var="AUTOSKILLIT_EXPLORATION_CAPABILITY",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "Real AutoSkillit orchestration/session-control environment variable read by"
            "production code; scrubbed as internal state that must not leak across test"
            "boundaries."
        ),
    ),
    "AUTOSKILLIT_EXPLORATION_ROLE": AmbientEnvDisposition(
        var="AUTOSKILLIT_EXPLORATION_ROLE",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "Real AutoSkillit orchestration/session-control environment variable read by"
            "production code; scrubbed as internal state that must not leak across test"
            "boundaries."
        ),
    ),
    "AUTOSKILLIT_EXPLORATION_SESSION_ID": AmbientEnvDisposition(
        var="AUTOSKILLIT_EXPLORATION_SESSION_ID",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "Real AutoSkillit orchestration/session-control environment variable read by"
            "production code; scrubbed as internal state that must not leak across test"
            "boundaries."
        ),
    ),
    "AUTOSKILLIT_FETCH_CACHE_TTL_SECONDS": AmbientEnvDisposition(
        var="AUTOSKILLIT_FETCH_CACHE_TTL_SECONDS",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "Real AutoSkillit orchestration/session-control environment variable read by"
            "production code; scrubbed as internal state that must not leak across test"
            "boundaries."
        ),
    ),
    "AUTOSKILLIT_FLEET_INSPECTOR_MODEL": AmbientEnvDisposition(
        var="AUTOSKILLIT_FLEET_INSPECTOR_MODEL",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "AutoSkillit-private session/orchestration variable in the"
            "AUTOSKILLIT_PRIVATE_ENV_VARS / _HEADLESS_EXCLUSIVE_VARS baseline; must not leak"
            "between sibling or nested sessions."
        ),
    ),
    "AUTOSKILLIT_FLEET_MODE": AmbientEnvDisposition(
        var="AUTOSKILLIT_FLEET_MODE",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "AutoSkillit-private session/orchestration variable in the"
            "AUTOSKILLIT_PRIVATE_ENV_VARS / _HEADLESS_EXCLUSIVE_VARS baseline; must not leak"
            "between sibling or nested sessions."
        ),
    ),
    "AUTOSKILLIT_FOOD_TRUCK_TOOL_TAGS": AmbientEnvDisposition(
        var="AUTOSKILLIT_FOOD_TRUCK_TOOL_TAGS",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "AutoSkillit-private session/orchestration variable in the"
            "AUTOSKILLIT_PRIVATE_ENV_VARS / _HEADLESS_EXCLUSIVE_VARS baseline; must not leak"
            "between sibling or nested sessions."
        ),
    ),
    "AUTOSKILLIT_FORCE_UPDATE_CHECK": AmbientEnvDisposition(
        var="AUTOSKILLIT_FORCE_UPDATE_CHECK",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "AutoSkillit-private session/orchestration variable in the"
            "AUTOSKILLIT_PRIVATE_ENV_VARS / _HEADLESS_EXCLUSIVE_VARS baseline; must not leak"
            "between sibling or nested sessions."
        ),
    ),
    "AUTOSKILLIT_HEADLESS": AmbientEnvDisposition(
        var="AUTOSKILLIT_HEADLESS",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "AutoSkillit-private session/orchestration variable in the"
            "AUTOSKILLIT_PRIVATE_ENV_VARS / _HEADLESS_EXCLUSIVE_VARS baseline; must not leak"
            "between sibling or nested sessions."
        ),
    ),
    "AUTOSKILLIT_HEADLESS_AUTO_GATE": AmbientEnvDisposition(
        var="AUTOSKILLIT_HEADLESS_AUTO_GATE",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "AutoSkillit-private session/orchestration variable in the"
            "AUTOSKILLIT_PRIVATE_ENV_VARS / _HEADLESS_EXCLUSIVE_VARS baseline; must not leak"
            "between sibling or nested sessions."
        ),
    ),
    "AUTOSKILLIT_HOOK_EVENT": AmbientEnvDisposition(
        var="AUTOSKILLIT_HOOK_EVENT",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "Real AutoSkillit orchestration/session-control environment variable read by"
            "production code; scrubbed as internal state that must not leak across test"
            "boundaries."
        ),
    ),
    "AUTOSKILLIT_IDLE_OUTPUT_TIMEOUT": AmbientEnvDisposition(
        var="AUTOSKILLIT_IDLE_OUTPUT_TIMEOUT",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "Real AutoSkillit orchestration/session-control environment variable read by"
            "production code; scrubbed as internal state that must not leak across test"
            "boundaries."
        ),
    ),
    "AUTOSKILLIT_INSTALLED_VERSION": AmbientEnvDisposition(
        var="AUTOSKILLIT_INSTALLED_VERSION",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "R4 predicate-(b) false positive: an all-uppercase enum/status/regex-name/label member"
            "of an unrelated lookup collection; never set as a real OS environment variable."
        ),
    ),
    "AUTOSKILLIT_JOIN_FLAG_PATH": AmbientEnvDisposition(
        var="AUTOSKILLIT_JOIN_FLAG_PATH",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "Real AutoSkillit orchestration/session-control environment variable read by"
            "production code; scrubbed as internal state that must not leak across test"
            "boundaries."
        ),
    ),
    "AUTOSKILLIT_JOIN_PARENT": AmbientEnvDisposition(
        var="AUTOSKILLIT_JOIN_PARENT",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "Real AutoSkillit orchestration/session-control environment variable read by"
            "production code; scrubbed as internal state that must not leak across test"
            "boundaries."
        ),
    ),
    "AUTOSKILLIT_JOIN_REQUIRED": AmbientEnvDisposition(
        var="AUTOSKILLIT_JOIN_REQUIRED",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "Real AutoSkillit orchestration/session-control environment variable read by"
            "production code; scrubbed as internal state that must not leak across test"
            "boundaries."
        ),
    ),
    "AUTOSKILLIT_JOIN_SESSION_ID": AmbientEnvDisposition(
        var="AUTOSKILLIT_JOIN_SESSION_ID",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "Real AutoSkillit orchestration/session-control environment variable read by"
            "production code; scrubbed as internal state that must not leak across test"
            "boundaries."
        ),
    ),
    "AUTOSKILLIT_KITCHEN_SESSION_ID": AmbientEnvDisposition(
        var="AUTOSKILLIT_KITCHEN_SESSION_ID",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "AutoSkillit-private session/orchestration variable in the"
            "AUTOSKILLIT_PRIVATE_ENV_VARS / _HEADLESS_EXCLUSIVE_VARS baseline; must not leak"
            "between sibling or nested sessions."
        ),
    ),
    "AUTOSKILLIT_LAUNCH_ID": AmbientEnvDisposition(
        var="AUTOSKILLIT_LAUNCH_ID",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "AutoSkillit-private session/orchestration variable in the"
            "AUTOSKILLIT_PRIVATE_ENV_VARS / _HEADLESS_EXCLUSIVE_VARS baseline; must not leak"
            "between sibling or nested sessions."
        ),
    ),
    "AUTOSKILLIT_LOG_DIR": AmbientEnvDisposition(
        var="AUTOSKILLIT_LOG_DIR",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "Real AutoSkillit orchestration/session-control environment variable read by"
            "production code; scrubbed as internal state that must not leak across test"
            "boundaries."
        ),
    ),
    "AUTOSKILLIT_MANAGED_ATTEMPT_ID": AmbientEnvDisposition(
        var="AUTOSKILLIT_MANAGED_ATTEMPT_ID",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "AutoSkillit-private session/orchestration variable in the"
            "AUTOSKILLIT_PRIVATE_ENV_VARS / _HEADLESS_EXCLUSIVE_VARS baseline; must not leak"
            "between sibling or nested sessions."
        ),
    ),
    "AUTOSKILLIT_MANAGED_LAUNCH_ID": AmbientEnvDisposition(
        var="AUTOSKILLIT_MANAGED_LAUNCH_ID",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "AutoSkillit-private session/orchestration variable in the"
            "AUTOSKILLIT_PRIVATE_ENV_VARS / _HEADLESS_EXCLUSIVE_VARS baseline; must not leak"
            "between sibling or nested sessions."
        ),
    ),
    "AUTOSKILLIT_MANAGED_LINEAGE_DIGEST": AmbientEnvDisposition(
        var="AUTOSKILLIT_MANAGED_LINEAGE_DIGEST",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "AutoSkillit-private session/orchestration variable in the"
            "AUTOSKILLIT_PRIVATE_ENV_VARS / _HEADLESS_EXCLUSIVE_VARS baseline; must not leak"
            "between sibling or nested sessions."
        ),
    ),
    "AUTOSKILLIT_MANAGED_LINEAGE_REF": AmbientEnvDisposition(
        var="AUTOSKILLIT_MANAGED_LINEAGE_REF",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "AutoSkillit-private session/orchestration variable in the"
            "AUTOSKILLIT_PRIVATE_ENV_VARS / _HEADLESS_EXCLUSIVE_VARS baseline; must not leak"
            "between sibling or nested sessions."
        ),
    ),
    "AUTOSKILLIT_MCP_CLIENT_BACKEND": AmbientEnvDisposition(
        var="AUTOSKILLIT_MCP_CLIENT_BACKEND",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "AutoSkillit-private session/orchestration variable in the"
            "AUTOSKILLIT_PRIVATE_ENV_VARS / _HEADLESS_EXCLUSIVE_VARS baseline; must not leak"
            "between sibling or nested sessions."
        ),
    ),
    "AUTOSKILLIT_NATIVE_SHELL_CAPTURE_MODE": AmbientEnvDisposition(
        var="AUTOSKILLIT_NATIVE_SHELL_CAPTURE_MODE",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "AutoSkillit-private session/orchestration variable in the"
            "AUTOSKILLIT_PRIVATE_ENV_VARS / _HEADLESS_EXCLUSIVE_VARS baseline; must not leak"
            "between sibling or nested sessions."
        ),
    ),
    "AUTOSKILLIT_PRIVATE_ENV_VARS": AmbientEnvDisposition(
        var="AUTOSKILLIT_PRIVATE_ENV_VARS",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "R4 predicate-(b) false positive: an all-uppercase enum/status/regex-name/label member"
            "of an unrelated lookup collection; never set as a real OS environment variable."
        ),
    ),
    "AUTOSKILLIT_PROJECTION_MANIFEST_PATH": AmbientEnvDisposition(
        var="AUTOSKILLIT_PROJECTION_MANIFEST_PATH",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "Real AutoSkillit orchestration/session-control environment variable read by"
            "production code; scrubbed as internal state that must not leak across test"
            "boundaries."
        ),
    ),
    "AUTOSKILLIT_PROJECT_DIR": AmbientEnvDisposition(
        var="AUTOSKILLIT_PROJECT_DIR",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "AutoSkillit-private session/orchestration variable in the"
            "AUTOSKILLIT_PRIVATE_ENV_VARS / _HEADLESS_EXCLUSIVE_VARS baseline; must not leak"
            "between sibling or nested sessions."
        ),
    ),
    "AUTOSKILLIT_PROTECTED_BRANCHES": AmbientEnvDisposition(
        var="AUTOSKILLIT_PROTECTED_BRANCHES",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "Real AutoSkillit orchestration/session-control environment variable read by"
            "production code; scrubbed as internal state that must not leak across test"
            "boundaries."
        ),
    ),
    "AUTOSKILLIT_PROVIDER_PROFILE": AmbientEnvDisposition(
        var="AUTOSKILLIT_PROVIDER_PROFILE",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "AutoSkillit-private session/orchestration variable in the"
            "AUTOSKILLIT_PRIVATE_ENV_VARS / _HEADLESS_EXCLUSIVE_VARS baseline; must not leak"
            "between sibling or nested sessions."
        ),
    ),
    "AUTOSKILLIT_QUOTA_GUARD__CACHE_PATH": AmbientEnvDisposition(
        var="AUTOSKILLIT_QUOTA_GUARD__CACHE_PATH",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "Real AutoSkillit orchestration/session-control environment variable read by"
            "production code; scrubbed as internal state that must not leak across test"
            "boundaries."
        ),
    ),
    "AUTOSKILLIT_QUOTA_GUARD__DISABLED": AmbientEnvDisposition(
        var="AUTOSKILLIT_QUOTA_GUARD__DISABLED",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "Real AutoSkillit orchestration/session-control environment variable read by"
            "production code; scrubbed as internal state that must not leak across test"
            "boundaries."
        ),
    ),
    "AUTOSKILLIT_SESSION_DEADLINE": AmbientEnvDisposition(
        var="AUTOSKILLIT_SESSION_DEADLINE",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "AutoSkillit-private session/orchestration variable in the"
            "AUTOSKILLIT_PRIVATE_ENV_VARS / _HEADLESS_EXCLUSIVE_VARS baseline; must not leak"
            "between sibling or nested sessions."
        ),
    ),
    "AUTOSKILLIT_SESSION_ID": AmbientEnvDisposition(
        var="AUTOSKILLIT_SESSION_ID",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "Real AutoSkillit orchestration/session-control environment variable read by"
            "production code; scrubbed as internal state that must not leak across test"
            "boundaries."
        ),
    ),
    "AUTOSKILLIT_SESSION_TYPE": AmbientEnvDisposition(
        var="AUTOSKILLIT_SESSION_TYPE",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "AutoSkillit-private session/orchestration variable in the"
            "AUTOSKILLIT_PRIVATE_ENV_VARS / _HEADLESS_EXCLUSIVE_VARS baseline; must not leak"
            "between sibling or nested sessions."
        ),
    ),
    "AUTOSKILLIT_SKILL_NAME": AmbientEnvDisposition(
        var="AUTOSKILLIT_SKILL_NAME",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "AutoSkillit-private session/orchestration variable in the"
            "AUTOSKILLIT_PRIVATE_ENV_VARS / _HEADLESS_EXCLUSIVE_VARS baseline; must not leak"
            "between sibling or nested sessions."
        ),
    ),
    "AUTOSKILLIT_SKIP_STALE_CHECK": AmbientEnvDisposition(
        var="AUTOSKILLIT_SKIP_STALE_CHECK",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "AutoSkillit-private session/orchestration variable in the"
            "AUTOSKILLIT_PRIVATE_ENV_VARS / _HEADLESS_EXCLUSIVE_VARS baseline; must not leak"
            "between sibling or nested sessions."
        ),
    ),
    "AUTOSKILLIT_SKIP_UPDATE_CHECK": AmbientEnvDisposition(
        var="AUTOSKILLIT_SKIP_UPDATE_CHECK",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "AutoSkillit-private session/orchestration variable in the"
            "AUTOSKILLIT_PRIVATE_ENV_VARS / _HEADLESS_EXCLUSIVE_VARS baseline; must not leak"
            "between sibling or nested sessions."
        ),
    ),
    "AUTOSKILLIT_SOURCE_REPO": AmbientEnvDisposition(
        var="AUTOSKILLIT_SOURCE_REPO",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "Real AutoSkillit orchestration/session-control environment variable read by"
            "production code; scrubbed as internal state that must not leak across test"
            "boundaries."
        ),
    ),
    "AUTOSKILLIT_STATE_DIR": AmbientEnvDisposition(
        var="AUTOSKILLIT_STATE_DIR",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "Real AutoSkillit orchestration/session-control environment variable read by"
            "production code; scrubbed as internal state that must not leak across test"
            "boundaries."
        ),
    ),
    "AUTOSKILLIT_STATE_ROOT": AmbientEnvDisposition(
        var="AUTOSKILLIT_STATE_ROOT",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "AutoSkillit-private session/orchestration variable in the"
            "AUTOSKILLIT_PRIVATE_ENV_VARS / _HEADLESS_EXCLUSIVE_VARS baseline; must not leak"
            "between sibling or nested sessions."
        ),
    ),
    "AUTOSKILLIT_STATE_ROOT_ENV_VAR": AmbientEnvDisposition(
        var="AUTOSKILLIT_STATE_ROOT_ENV_VAR",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "R4 predicate-(b) false positive: an all-uppercase enum/status/regex-name/label member"
            "of an unrelated lookup collection; never set as a real OS environment variable."
        ),
    ),
    "AUTOSKILLIT_WRITE_GUARD_TOOL_NAMES": AmbientEnvDisposition(
        var="AUTOSKILLIT_WRITE_GUARD_TOOL_NAMES",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "AutoSkillit-private session/orchestration variable in the"
            "AUTOSKILLIT_PRIVATE_ENV_VARS / _HEADLESS_EXCLUSIVE_VARS baseline; must not leak"
            "between sibling or nested sessions."
        ),
    ),
    "BEHIND": AmbientEnvDisposition(
        var="BEHIND",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "R4 predicate-(b) false positive: an all-uppercase enum/status/regex-name/label member"
            "of an unrelated lookup collection; never set as a real OS environment variable."
        ),
    ),
    "BLOCKED": AmbientEnvDisposition(
        var="BLOCKED",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "R4 predicate-(b) false positive: an all-uppercase enum/status/regex-name/label member"
            "of an unrelated lookup collection; never set as a real OS environment variable."
        ),
    ),
    "CACHE_RD": AmbientEnvDisposition(
        var="CACHE_RD",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "R4 predicate-(b) false positive: an all-uppercase enum/status/regex-name/label member"
            "of an unrelated lookup collection; never set as a real OS environment variable."
        ),
    ),
    "CACHE_WR": AmbientEnvDisposition(
        var="CACHE_WR",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "R4 predicate-(b) false positive: an all-uppercase enum/status/regex-name/label member"
            "of an unrelated lookup collection; never set as a real OS environment variable."
        ),
    ),
    "CAMPAIGN_ID_ENV_VAR": AmbientEnvDisposition(
        var="CAMPAIGN_ID_ENV_VAR",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "R4 predicate-(b) false positive: an all-uppercase enum/status/regex-name/label member"
            "of an unrelated lookup collection; never set as a real OS environment variable."
        ),
    ),
    "CAPTURE_ID_RE": AmbientEnvDisposition(
        var="CAPTURE_ID_RE",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "R4 predicate-(b) false positive: an all-uppercase enum/status/regex-name/label member"
            "of an unrelated lookup collection; never set as a real OS environment variable."
        ),
    ),
    "CHANGES_REQUESTED": AmbientEnvDisposition(
        var="CHANGES_REQUESTED",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "R4 predicate-(b) false positive: an all-uppercase enum/status/regex-name/label member"
            "of an unrelated lookup collection; never set as a real OS environment variable."
        ),
    ),
    "CI": AmbientEnvDisposition(
        var="CI",
        disposition="preserve",
        owner="harness",
        justification=(
            "CI-mode signal many libraries branch on for non-interactive/non-TTY behavior;"
            "scrubbing it mid-test-run risks flipping tools into interactive code paths."
        ),
    ),
    "CLAUDECODE": AmbientEnvDisposition(
        var="CLAUDECODE",
        disposition="scrub",
        owner="claude-code",
        justification=(
            "Claude Code host-process signal (explicit executable path override / IDE-launch"
            "marker); must not leak into an isolated headless test session."
        ),
    ),
    "CLAUDE_CODE_DISABLE_BACKGROUND_TASKS": AmbientEnvDisposition(
        var="CLAUDE_CODE_DISABLE_BACKGROUND_TASKS",
        disposition="scrub",
        owner="claude-code",
        justification=(
            "Real Claude Code environment variable (documented CLI toggle or session-hardening"
            "override) read by production code; scrubbed to isolate test sessions."
        ),
    ),
    "CLAUDE_CODE_DISABLE_CRON": AmbientEnvDisposition(
        var="CLAUDE_CODE_DISABLE_CRON",
        disposition="scrub",
        owner="claude-code",
        justification=(
            "Real Claude Code environment variable (documented CLI toggle or session-hardening"
            "override) read by production code; scrubbed to isolate test sessions."
        ),
    ),
    "CLAUDE_CODE_EXECPATH": AmbientEnvDisposition(
        var="CLAUDE_CODE_EXECPATH",
        disposition="scrub",
        owner="claude-code",
        justification=(
            "Claude Code host-process signal (explicit executable path override / IDE-launch"
            "marker); must not leak into an isolated headless test session."
        ),
    ),
    "CLAUDE_CODE_EXIT_AFTER_STOP_DELAY": AmbientEnvDisposition(
        var="CLAUDE_CODE_EXIT_AFTER_STOP_DELAY",
        disposition="scrub",
        owner="claude-code",
        justification=(
            "Claude Code session-lifetime control variable in the _HEADLESS_EXCLUSIVE_VARS"
            "baseline; scrubbed so a parent session cannot leak settings into a child."
        ),
    ),
    "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS": AmbientEnvDisposition(
        var="CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS",
        disposition="scrub",
        owner="claude-code",
        justification=(
            "Real Claude Code environment variable (documented CLI toggle or session-hardening"
            "override) read by production code; scrubbed to isolate test sessions."
        ),
    ),
    "CLAUDE_CODE_MCP_TOOL_IDLE_TIMEOUT": AmbientEnvDisposition(
        var="CLAUDE_CODE_MCP_TOOL_IDLE_TIMEOUT",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "R4 predicate-(b) false positive: an all-uppercase enum/status/regex-name/label member"
            "of an unrelated lookup collection; never set as a real OS environment variable."
        ),
    ),
    "CLAUDE_CODE_MCP_TOOL_IDLE_TIMEOUT_ENV_VAR": AmbientEnvDisposition(
        var="CLAUDE_CODE_MCP_TOOL_IDLE_TIMEOUT_ENV_VAR",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "R4 predicate-(b) false positive: an all-uppercase enum/status/regex-name/label member"
            "of an unrelated lookup collection; never set as a real OS environment variable."
        ),
    ),
    "CLAUDE_CODE_SSE_PORT": AmbientEnvDisposition(
        var="CLAUDE_CODE_SSE_PORT",
        disposition="scrub",
        owner="claude-code",
        justification=(
            "IDE discovery/bridge variable in IDE_ENV_DENYLIST that lets a host IDE (VS"
            "Code/Cursor/Zed) attach across the trust boundary; scrubbed to prevent test leakage."
        ),
    ),
    "CLAUDE_CODE_SUBAGENT_MODEL": AmbientEnvDisposition(
        var="CLAUDE_CODE_SUBAGENT_MODEL",
        disposition="scrub",
        owner="claude-code",
        justification=(
            "Claude Code session-lifetime control variable in the _HEADLESS_EXCLUSIVE_VARS"
            "baseline; scrubbed so a parent session cannot leak settings into a child."
        ),
    ),
    "CLAUDE_CODE_WEBSOCKET_AUTH_FILE_DESCRIPTOR": AmbientEnvDisposition(
        var="CLAUDE_CODE_WEBSOCKET_AUTH_FILE_DESCRIPTOR",
        disposition="scrub",
        owner="claude-code",
        justification=(
            "IDE discovery/bridge variable in IDE_ENV_DENYLIST that lets a host IDE (VS"
            "Code/Cursor/Zed) attach across the trust boundary; scrubbed to prevent test leakage."
        ),
    ),
    "CLAUDE_EXPLORATION_DISPATCH_RENDERER": AmbientEnvDisposition(
        var="CLAUDE_EXPLORATION_DISPATCH_RENDERER",
        disposition="scrub",
        owner="claude-code",
        justification=(
            "Real Claude Code environment variable (documented CLI toggle or session-hardening"
            "override) read by production code; scrubbed to isolate test sessions."
        ),
    ),
    "CLAUDE_MCP_CONNECTION_NONBLOCKING": AmbientEnvDisposition(
        var="CLAUDE_MCP_CONNECTION_NONBLOCKING",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "R4 predicate-(b) false positive: an all-uppercase enum/status/regex-name/label member"
            "of an unrelated lookup collection; never set as a real OS environment variable."
        ),
    ),
    "CLAUDE_MCP_CONNECT_TIMEOUT_ENV_VAR": AmbientEnvDisposition(
        var="CLAUDE_MCP_CONNECT_TIMEOUT_ENV_VAR",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "R4 predicate-(b) false positive: an all-uppercase enum/status/regex-name/label member"
            "of an unrelated lookup collection; never set as a real OS environment variable."
        ),
    ),
    "CLAUDE_MCP_CONNECT_TIMEOUT_MS": AmbientEnvDisposition(
        var="CLAUDE_MCP_CONNECT_TIMEOUT_MS",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "R4 predicate-(b) false positive: an all-uppercase enum/status/regex-name/label member"
            "of an unrelated lookup collection; never set as a real OS environment variable."
        ),
    ),
    "CLAUDE_STREAM_IDLE_TIMEOUT_MS": AmbientEnvDisposition(
        var="CLAUDE_STREAM_IDLE_TIMEOUT_MS",
        disposition="scrub",
        owner="claude-code",
        justification=(
            "Claude Code session-lifetime control variable in the _HEADLESS_EXCLUSIVE_VARS"
            "baseline; scrubbed so a parent session cannot leak settings into a child."
        ),
    ),
    "CLEAN": AmbientEnvDisposition(
        var="CLEAN",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "R4 predicate-(b) false positive: an all-uppercase enum/status/regex-name/label member"
            "of an unrelated lookup collection; never set as a real OS environment variable."
        ),
    ),
    "CODEX_API_KEY": AmbientEnvDisposition(
        var="CODEX_API_KEY",
        disposition="scrub",
        owner="codex",
        justification=(
            "Third-party provider API credential forwarded to evidence-reader/codex subprocesses;"
            "scrubbed like ANTHROPIC_API_KEY to prevent cross-test credential leakage."
        ),
    ),
    "CODEX_CONTEXT_EXHAUSTION_MARKER": AmbientEnvDisposition(
        var="CODEX_CONTEXT_EXHAUSTION_MARKER",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "R4 predicate-(b) false positive: an all-uppercase enum/status/regex-name/label member"
            "of an unrelated lookup collection; never set as a real OS environment variable."
        ),
    ),
    "CODEX_COOK_RESERVED_ENV_VARS": AmbientEnvDisposition(
        var="CODEX_COOK_RESERVED_ENV_VARS",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "R4 predicate-(b) false positive: an all-uppercase enum/status/regex-name/label member"
            "of an unrelated lookup collection; never set as a real OS environment variable."
        ),
    ),
    "CODEX_EXPLORATION_DISPATCH_RENDERER": AmbientEnvDisposition(
        var="CODEX_EXPLORATION_DISPATCH_RENDERER",
        disposition="scrub",
        owner="codex",
        justification=(
            "Real Codex-backend environment variable read by production code to configure or"
            "dispatch Codex sessions; scrubbed to isolate test sessions."
        ),
    ),
    "CODEX_HOME": AmbientEnvDisposition(
        var="CODEX_HOME",
        disposition="scrub",
        owner="codex",
        justification=(
            "Codex CLI recursion-guard variable in the AUTOSKILLIT_PRIVATE_ENV_VARS baseline"
            "(CODEX_COOK_RESERVED_ENV_VARS); must not leak between sessions."
        ),
    ),
    "CODEX_INTAKE_DISCIPLINE_DIGEST": AmbientEnvDisposition(
        var="CODEX_INTAKE_DISCIPLINE_DIGEST",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "R4 predicate-(b) false positive: an all-uppercase enum/status/regex-name/label member"
            "of an unrelated lookup collection; never set as a real OS environment variable."
        ),
    ),
    "CODEX_INTERACTIVE_REQUIRED_ENV": AmbientEnvDisposition(
        var="CODEX_INTERACTIVE_REQUIRED_ENV",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "R4 predicate-(b) false positive: an all-uppercase enum/status/regex-name/label member"
            "of an unrelated lookup collection; never set as a real OS environment variable."
        ),
    ),
    "CODEX_MCP_ENV_FORWARD_VARS": AmbientEnvDisposition(
        var="CODEX_MCP_ENV_FORWARD_VARS",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "R4 predicate-(b) false positive: an all-uppercase enum/status/regex-name/label member"
            "of an unrelated lookup collection; never set as a real OS environment variable."
        ),
    ),
    "CODEX_RECIPE_DELIVERY_CALLING_CONTRACT": AmbientEnvDisposition(
        var="CODEX_RECIPE_DELIVERY_CALLING_CONTRACT",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "R4 predicate-(b) false positive: an all-uppercase enum/status/regex-name/label member"
            "of an unrelated lookup collection; never set as a real OS environment variable."
        ),
    ),
    "CODEX_SCHEMA_VERSION": AmbientEnvDisposition(
        var="CODEX_SCHEMA_VERSION",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "R4 predicate-(b) false positive: an all-uppercase enum/status/regex-name/label member"
            "of an unrelated lookup collection; never set as a real OS environment variable."
        ),
    ),
    "CODEX_SCOPE_DISCIPLINE_DIGEST": AmbientEnvDisposition(
        var="CODEX_SCOPE_DISCIPLINE_DIGEST",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "R4 predicate-(b) false positive: an all-uppercase enum/status/regex-name/label member"
            "of an unrelated lookup collection; never set as a real OS environment variable."
        ),
    ),
    "CODEX_SQLITE_HOME": AmbientEnvDisposition(
        var="CODEX_SQLITE_HOME",
        disposition="scrub",
        owner="codex",
        justification=(
            "Codex CLI recursion-guard variable in the AUTOSKILLIT_PRIVATE_ENV_VARS baseline"
            "(CODEX_COOK_RESERVED_ENV_VARS); must not leak between sessions."
        ),
    ),
    "CODEX_STARTUP_TRACE_ENV_VAR": AmbientEnvDisposition(
        var="CODEX_STARTUP_TRACE_ENV_VAR",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "R4 predicate-(b) false positive: an all-uppercase enum/status/regex-name/label member"
            "of an unrelated lookup collection; never set as a real OS environment variable."
        ),
    ),
    "COMMENT": AmbientEnvDisposition(
        var="COMMENT",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "R4 predicate-(b) false positive: an all-uppercase enum/status/regex-name/label member"
            "of an unrelated lookup collection; never set as a real OS environment variable."
        ),
    ),
    "COMMENTED": AmbientEnvDisposition(
        var="COMMENTED",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "R4 predicate-(b) false positive: an all-uppercase enum/status/regex-name/label member"
            "of an unrelated lookup collection; never set as a real OS environment variable."
        ),
    ),
    "COMSPEC": AmbientEnvDisposition(
        var="COMSPEC",
        disposition="preserve",
        owner="posix",
        justification=(
            "Generic POSIX/system environment configuration (process"
            "locate/locale/proxy/certificate config) required for basic subprocess correctness;"
            "not AutoSkillit-private state."
        ),
    ),
    "CONFLICT": AmbientEnvDisposition(
        var="CONFLICT",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "R4 predicate-(b) false positive: an all-uppercase enum/status/regex-name/label member"
            "of an unrelated lookup collection; never set as a real OS environment variable."
        ),
    ),
    "CONTEXT_EXHAUSTION_MARKER": AmbientEnvDisposition(
        var="CONTEXT_EXHAUSTION_MARKER",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "R4 predicate-(b) false positive: an all-uppercase enum/status/regex-name/label member"
            "of an unrelated lookup collection; never set as a real OS environment variable."
        ),
    ),
    "COUNT": AmbientEnvDisposition(
        var="COUNT",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "R4 predicate-(b) false positive: an all-uppercase enum/status/regex-name/label member"
            "of an unrelated lookup collection; never set as a real OS environment variable."
        ),
    ),
    "COVERED": AmbientEnvDisposition(
        var="COVERED",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "R4 predicate-(b) false positive: an all-uppercase enum/status/regex-name/label member"
            "of an unrelated lookup collection; never set as a real OS environment variable."
        ),
    ),
    "CURL_CA_BUNDLE": AmbientEnvDisposition(
        var="CURL_CA_BUNDLE",
        disposition="preserve",
        owner="posix",
        justification=(
            "Generic POSIX/system environment configuration (process"
            "locate/locale/proxy/certificate config) required for basic subprocess correctness;"
            "not AutoSkillit-private state."
        ),
    ),
    "CURSOR_TRACE_ID": AmbientEnvDisposition(
        var="CURSOR_TRACE_ID",
        disposition="scrub",
        owner="claude-code",
        justification=(
            "IDE discovery/bridge variable in IDE_ENV_DENYLIST that lets a host IDE (VS"
            "Code/Cursor/Zed) attach across the trust boundary; scrubbed to prevent test leakage."
        ),
    ),
    "DELETE": AmbientEnvDisposition(
        var="DELETE",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "R4 predicate-(b) false positive: an all-uppercase enum/status/regex-name/label member"
            "of an unrelated lookup collection; never set as a real OS environment variable."
        ),
    ),
    "DIRTY": AmbientEnvDisposition(
        var="DIRTY",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "R4 predicate-(b) false positive: an all-uppercase enum/status/regex-name/label member"
            "of an unrelated lookup collection; never set as a real OS environment variable."
        ),
    ),
    "DISMISSED": AmbientEnvDisposition(
        var="DISMISSED",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "R4 predicate-(b) false positive: an all-uppercase enum/status/regex-name/label member"
            "of an unrelated lookup collection; never set as a real OS environment variable."
        ),
    ),
    "DISPATCH_ID_ENV_VAR": AmbientEnvDisposition(
        var="DISPATCH_ID_ENV_VAR",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "R4 predicate-(b) false positive: an all-uppercase enum/status/regex-name/label member"
            "of an unrelated lookup collection; never set as a real OS environment variable."
        ),
    ),
    "DURATION": AmbientEnvDisposition(
        var="DURATION",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "R4 predicate-(b) false positive: an all-uppercase enum/status/regex-name/label member"
            "of an unrelated lookup collection; never set as a real OS environment variable."
        ),
    ),
    "ENABLE_IDE_INTEGRATION": AmbientEnvDisposition(
        var="ENABLE_IDE_INTEGRATION",
        disposition="scrub",
        owner="claude-code",
        justification=(
            "IDE discovery/bridge variable in IDE_ENV_DENYLIST that lets a host IDE (VS"
            "Code/Cursor/Zed) attach across the trust boundary; scrubbed to prevent test leakage."
        ),
    ),
    "EVIDENCE_READER_AUTHORITY_ENV_VAR": AmbientEnvDisposition(
        var="EVIDENCE_READER_AUTHORITY_ENV_VAR",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "R4 predicate-(b) false positive: an all-uppercase enum/status/regex-name/label member"
            "of an unrelated lookup collection; never set as a real OS environment variable."
        ),
    ),
    "EVIDENCE_READER_AUTHORITY_PATH_ENV_VAR": AmbientEnvDisposition(
        var="EVIDENCE_READER_AUTHORITY_PATH_ENV_VAR",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "R4 predicate-(b) false positive: an all-uppercase enum/status/regex-name/label member"
            "of an unrelated lookup collection; never set as a real OS environment variable."
        ),
    ),
    "EVIDENCE_READER_CAPABILITY_ENV_VAR": AmbientEnvDisposition(
        var="EVIDENCE_READER_CAPABILITY_ENV_VAR",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "R4 predicate-(b) false positive: an all-uppercase enum/status/regex-name/label member"
            "of an unrelated lookup collection; never set as a real OS environment variable."
        ),
    ),
    "EVIDENCE_READER_ENV_FORWARD_VARS": AmbientEnvDisposition(
        var="EVIDENCE_READER_ENV_FORWARD_VARS",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "R4 predicate-(b) false positive: an all-uppercase enum/status/regex-name/label member"
            "of an unrelated lookup collection; never set as a real OS environment variable."
        ),
    ),
    "EXACT_REPLAY": AmbientEnvDisposition(
        var="EXACT_REPLAY",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "R4 predicate-(b) false positive: an all-uppercase enum/status/regex-name/label member"
            "of an unrelated lookup collection; never set as a real OS environment variable."
        ),
    ),
    "FLEET_DISPATCH_MODE": AmbientEnvDisposition(
        var="FLEET_DISPATCH_MODE",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "R4 predicate-(b) false positive: an all-uppercase enum/status/regex-name/label member"
            "of an unrelated lookup collection; never set as a real OS environment variable."
        ),
    ),
    "FLEET_INSPECTOR_MODEL_ENV_VAR": AmbientEnvDisposition(
        var="FLEET_INSPECTOR_MODEL_ENV_VAR",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "R4 predicate-(b) false positive: an all-uppercase enum/status/regex-name/label member"
            "of an unrelated lookup collection; never set as a real OS environment variable."
        ),
    ),
    "FLEET_MODE_ENV_VAR": AmbientEnvDisposition(
        var="FLEET_MODE_ENV_VAR",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "R4 predicate-(b) false positive: an all-uppercase enum/status/regex-name/label member"
            "of an unrelated lookup collection; never set as a real OS environment variable."
        ),
    ),
    "FLEET_SESSION_REQUIRED_ENV": AmbientEnvDisposition(
        var="FLEET_SESSION_REQUIRED_ENV",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "R4 predicate-(b) false positive: an all-uppercase enum/status/regex-name/label member"
            "of an unrelated lookup collection; never set as a real OS environment variable."
        ),
    ),
    "FOOD_TRUCK_TOOL_TAGS_ENV_VAR": AmbientEnvDisposition(
        var="FOOD_TRUCK_TOOL_TAGS_ENV_VAR",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "R4 predicate-(b) false positive: an all-uppercase enum/status/regex-name/label member"
            "of an unrelated lookup collection; never set as a real OS environment variable."
        ),
    ),
    "GITHUB_REPOSITORY": AmbientEnvDisposition(
        var="GITHUB_REPOSITORY",
        disposition="scrub",
        owner="harness",
        justification=(
            "GitHub CLI/API identity or credential ambient signal; not required by the pytest"
            "harness itself and scrubbed to avoid inadvertent live API behavior in tests."
        ),
    ),
    "GITHUB_TOKEN": AmbientEnvDisposition(
        var="GITHUB_TOKEN",
        disposition="scrub",
        owner="harness",
        justification=(
            "GitHub CLI/API identity or credential ambient signal; not required by the pytest"
            "harness itself and scrubbed to avoid inadvertent live API behavior in tests."
        ),
    ),
    "H": AmbientEnvDisposition(
        var="H",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "R4 predicate-(b) false positive: an all-uppercase enum/status/regex-name/label member"
            "of an unrelated lookup collection; never set as a real OS environment variable."
        ),
    ),
    "HAS_HOOKS": AmbientEnvDisposition(
        var="HAS_HOOKS",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "R4 predicate-(b) false positive: an all-uppercase enum/status/regex-name/label member"
            "of an unrelated lookup collection; never set as a real OS environment variable."
        ),
    ),
    "HEADLESS_AUTO_GATE_ENV_VAR": AmbientEnvDisposition(
        var="HEADLESS_AUTO_GATE_ENV_VAR",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "R4 predicate-(b) false positive: an all-uppercase enum/status/regex-name/label member"
            "of an unrelated lookup collection; never set as a real OS environment variable."
        ),
    ),
    "HEADLESS_ENV_VAR": AmbientEnvDisposition(
        var="HEADLESS_ENV_VAR",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "R4 predicate-(b) false positive: an all-uppercase enum/status/regex-name/label member"
            "of an unrelated lookup collection; never set as a real OS environment variable."
        ),
    ),
    "HOME": AmbientEnvDisposition(
        var="HOME",
        disposition="preserve",
        owner="posix",
        justification=(
            "Generic POSIX/system environment configuration (process"
            "locate/locale/proxy/certificate config) required for basic subprocess correctness;"
            "not AutoSkillit-private state."
        ),
    ),
    "HTTPS_PROXY": AmbientEnvDisposition(
        var="HTTPS_PROXY",
        disposition="preserve",
        owner="posix",
        justification=(
            "Generic POSIX/system environment configuration (process"
            "locate/locale/proxy/certificate config) required for basic subprocess correctness;"
            "not AutoSkillit-private state."
        ),
    ),
    "HTTP_PROXY": AmbientEnvDisposition(
        var="HTTP_PROXY",
        disposition="preserve",
        owner="posix",
        justification=(
            "Generic POSIX/system environment configuration (process"
            "locate/locale/proxy/certificate config) required for basic subprocess correctness;"
            "not AutoSkillit-private state."
        ),
    ),
    "INCARNATION_RE": AmbientEnvDisposition(
        var="INCARNATION_RE",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "R4 predicate-(b) false positive: an all-uppercase enum/status/regex-name/label member"
            "of an unrelated lookup collection; never set as a real OS environment variable."
        ),
    ),
    "INPUT": AmbientEnvDisposition(
        var="INPUT",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "R4 predicate-(b) false positive: an all-uppercase enum/status/regex-name/label member"
            "of an unrelated lookup collection; never set as a real OS environment variable."
        ),
    ),
    "INVOCATIONS": AmbientEnvDisposition(
        var="INVOCATIONS",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "R4 predicate-(b) false positive: an all-uppercase enum/status/regex-name/label member"
            "of an unrelated lookup collection; never set as a real OS environment variable."
        ),
    ),
    "KITCHEN_SESSION_ID_ENV_VAR": AmbientEnvDisposition(
        var="KITCHEN_SESSION_ID_ENV_VAR",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "R4 predicate-(b) false positive: an all-uppercase enum/status/regex-name/label member"
            "of an unrelated lookup collection; never set as a real OS environment variable."
        ),
    ),
    "KITTY_WINDOW_ID": AmbientEnvDisposition(
        var="KITTY_WINDOW_ID",
        disposition="preserve",
        owner="posix",
        justification=(
            "Generic POSIX/system environment configuration (process"
            "locate/locale/proxy/certificate config) required for basic subprocess correctness;"
            "not AutoSkillit-private state."
        ),
    ),
    "KNOWN_BACKEND_NAMES": AmbientEnvDisposition(
        var="KNOWN_BACKEND_NAMES",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "R4 predicate-(b) false positive: an all-uppercase enum/status/regex-name/label member"
            "of an unrelated lookup collection; never set as a real OS environment variable."
        ),
    ),
    "L": AmbientEnvDisposition(
        var="L",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "R4 predicate-(b) false positive: an all-uppercase enum/status/regex-name/label member"
            "of an unrelated lookup collection; never set as a real OS environment variable."
        ),
    ),
    "LANG": AmbientEnvDisposition(
        var="LANG",
        disposition="preserve",
        owner="posix",
        justification=(
            "Generic POSIX/system environment configuration (process"
            "locate/locale/proxy/certificate config) required for basic subprocess correctness;"
            "not AutoSkillit-private state."
        ),
    ),
    "LAUNCH_ID_ENV_VAR": AmbientEnvDisposition(
        var="LAUNCH_ID_ENV_VAR",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "R4 predicate-(b) false positive: an all-uppercase enum/status/regex-name/label member"
            "of an unrelated lookup collection; never set as a real OS environment variable."
        ),
    ),
    "LC_ALL": AmbientEnvDisposition(
        var="LC_ALL",
        disposition="preserve",
        owner="posix",
        justification=(
            "Generic POSIX/system environment configuration (process"
            "locate/locale/proxy/certificate config) required for basic subprocess correctness;"
            "not AutoSkillit-private state."
        ),
    ),
    "LEFT": AmbientEnvDisposition(
        var="LEFT",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "R4 predicate-(b) false positive: an all-uppercase enum/status/regex-name/label member"
            "of an unrelated lookup collection; never set as a real OS environment variable."
        ),
    ),
    "LOGNAME": AmbientEnvDisposition(
        var="LOGNAME",
        disposition="preserve",
        owner="posix",
        justification=(
            "Generic POSIX/system environment configuration (process"
            "locate/locale/proxy/certificate config) required for basic subprocess correctness;"
            "not AutoSkillit-private state."
        ),
    ),
    "M": AmbientEnvDisposition(
        var="M",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "R4 predicate-(b) false positive: an all-uppercase enum/status/regex-name/label member"
            "of an unrelated lookup collection; never set as a real OS environment variable."
        ),
    ),
    "MANAGED_ATTEMPT_ID_ENV_VAR": AmbientEnvDisposition(
        var="MANAGED_ATTEMPT_ID_ENV_VAR",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "R4 predicate-(b) false positive: an all-uppercase enum/status/regex-name/label member"
            "of an unrelated lookup collection; never set as a real OS environment variable."
        ),
    ),
    "MANAGED_LAUNCH_ID_ENV_VAR": AmbientEnvDisposition(
        var="MANAGED_LAUNCH_ID_ENV_VAR",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "R4 predicate-(b) false positive: an all-uppercase enum/status/regex-name/label member"
            "of an unrelated lookup collection; never set as a real OS environment variable."
        ),
    ),
    "MANAGED_LINEAGE_DIGEST_ENV_VAR": AmbientEnvDisposition(
        var="MANAGED_LINEAGE_DIGEST_ENV_VAR",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "R4 predicate-(b) false positive: an all-uppercase enum/status/regex-name/label member"
            "of an unrelated lookup collection; never set as a real OS environment variable."
        ),
    ),
    "MANAGED_LINEAGE_REF_ENV_VAR": AmbientEnvDisposition(
        var="MANAGED_LINEAGE_REF_ENV_VAR",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "R4 predicate-(b) false positive: an all-uppercase enum/status/regex-name/label member"
            "of an unrelated lookup collection; never set as a real OS environment variable."
        ),
    ),
    "MAX_MCP_OUTPUT_TOKENS": AmbientEnvDisposition(
        var="MAX_MCP_OUTPUT_TOKENS",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "AutoSkillit-private session/orchestration variable in the"
            "AUTOSKILLIT_PRIVATE_ENV_VARS / _HEADLESS_EXCLUSIVE_VARS baseline; must not leak"
            "between sibling or nested sessions."
        ),
    ),
    "MCP_CLIENT_BACKEND_ENV_VAR": AmbientEnvDisposition(
        var="MCP_CLIENT_BACKEND_ENV_VAR",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "R4 predicate-(b) false positive: an all-uppercase enum/status/regex-name/label member"
            "of an unrelated lookup collection; never set as a real OS environment variable."
        ),
    ),
    "MCP_CONNECTION_NONBLOCKING": AmbientEnvDisposition(
        var="MCP_CONNECTION_NONBLOCKING",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "R4 predicate-(b) false positive: an all-uppercase enum/status/regex-name/label member"
            "of an unrelated lookup collection; never set as a real OS environment variable."
        ),
    ),
    "MCP_CONNECT_TIMEOUT_MS": AmbientEnvDisposition(
        var="MCP_CONNECT_TIMEOUT_MS",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "R4 predicate-(b) false positive: an all-uppercase enum/status/regex-name/label member"
            "of an unrelated lookup collection; never set as a real OS environment variable."
        ),
    ),
    "MISSING": AmbientEnvDisposition(
        var="MISSING",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "R4 predicate-(b) false positive: an all-uppercase enum/status/regex-name/label member"
            "of an unrelated lookup collection; never set as a real OS environment variable."
        ),
    ),
    "MODEL": AmbientEnvDisposition(
        var="MODEL",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "R4 predicate-(b) false positive: an all-uppercase enum/status/regex-name/label member"
            "of an unrelated lookup collection; never set as a real OS environment variable."
        ),
    ),
    "NAMED_DEVIATION": AmbientEnvDisposition(
        var="NAMED_DEVIATION",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "R4 predicate-(b) false positive: an all-uppercase enum/status/regex-name/label member"
            "of an unrelated lookup collection; never set as a real OS environment variable."
        ),
    ),
    "NATIVE_SHELL_CAPTURE_MODE_ENV_VAR": AmbientEnvDisposition(
        var="NATIVE_SHELL_CAPTURE_MODE_ENV_VAR",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "R4 predicate-(b) false positive: an all-uppercase enum/status/regex-name/label member"
            "of an unrelated lookup collection; never set as a real OS environment variable."
        ),
    ),
    "NON_PUBLISHED_STANDALONE": AmbientEnvDisposition(
        var="NON_PUBLISHED_STANDALONE",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "R4 predicate-(b) false positive: an all-uppercase enum/status/regex-name/label member"
            "of an unrelated lookup collection; never set as a real OS environment variable."
        ),
    ),
    "NO_COLOR": AmbientEnvDisposition(
        var="NO_COLOR",
        disposition="preserve",
        owner="posix",
        justification=(
            "Generic POSIX/system environment configuration (process"
            "locate/locale/proxy/certificate config) required for basic subprocess correctness;"
            "not AutoSkillit-private state."
        ),
    ),
    "NO_PROXY": AmbientEnvDisposition(
        var="NO_PROXY",
        disposition="preserve",
        owner="posix",
        justification=(
            "Generic POSIX/system environment configuration (process"
            "locate/locale/proxy/certificate config) required for basic subprocess correctness;"
            "not AutoSkillit-private state."
        ),
    ),
    "ODD": AmbientEnvDisposition(
        var="ODD",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "R4 predicate-(b) false positive: an all-uppercase enum/status/regex-name/label member"
            "of an unrelated lookup collection; never set as a real OS environment variable."
        ),
    ),
    "OPENAI_API_KEY": AmbientEnvDisposition(
        var="OPENAI_API_KEY",
        disposition="scrub",
        owner="codex",
        justification=(
            "Third-party provider API credential forwarded to evidence-reader/codex subprocesses;"
            "scrubbed like ANTHROPIC_API_KEY to prevent cross-test credential leakage."
        ),
    ),
    "ORCHESTRATOR_SESSION_REQUIRED_ENV": AmbientEnvDisposition(
        var="ORCHESTRATOR_SESSION_REQUIRED_ENV",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "R4 predicate-(b) false positive: an all-uppercase enum/status/regex-name/label member"
            "of an unrelated lookup collection; never set as a real OS environment variable."
        ),
    ),
    "ORDER_INTERACTIVE_REQUIRED_ENV": AmbientEnvDisposition(
        var="ORDER_INTERACTIVE_REQUIRED_ENV",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "R4 predicate-(b) false positive: an all-uppercase enum/status/regex-name/label member"
            "of an unrelated lookup collection; never set as a real OS environment variable."
        ),
    ),
    "OUTPUT": AmbientEnvDisposition(
        var="OUTPUT",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "R4 predicate-(b) false positive: an all-uppercase enum/status/regex-name/label member"
            "of an unrelated lookup collection; never set as a real OS environment variable."
        ),
    ),
    "OUTPUT_DISCIPLINE_DIGEST": AmbientEnvDisposition(
        var="OUTPUT_DISCIPLINE_DIGEST",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "R4 predicate-(b) false positive: an all-uppercase enum/status/regex-name/label member"
            "of an unrelated lookup collection; never set as a real OS environment variable."
        ),
    ),
    "PATCH": AmbientEnvDisposition(
        var="PATCH",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "R4 predicate-(b) false positive: an all-uppercase enum/status/regex-name/label member"
            "of an unrelated lookup collection; never set as a real OS environment variable."
        ),
    ),
    "PATH": AmbientEnvDisposition(
        var="PATH",
        disposition="preserve",
        owner="posix",
        justification=(
            "Generic POSIX/system environment configuration (process"
            "locate/locale/proxy/certificate config) required for basic subprocess correctness;"
            "not AutoSkillit-private state."
        ),
    ),
    "PATHEXT": AmbientEnvDisposition(
        var="PATHEXT",
        disposition="preserve",
        owner="posix",
        justification=(
            "Generic POSIX/system environment configuration (process"
            "locate/locale/proxy/certificate config) required for basic subprocess correctness;"
            "not AutoSkillit-private state."
        ),
    ),
    "PEAK_CTX": AmbientEnvDisposition(
        var="PEAK_CTX",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "R4 predicate-(b) false positive: an all-uppercase enum/status/regex-name/label member"
            "of an unrelated lookup collection; never set as a real OS environment variable."
        ),
    ),
    "PENDING": AmbientEnvDisposition(
        var="PENDING",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "R4 predicate-(b) false positive: an all-uppercase enum/status/regex-name/label member"
            "of an unrelated lookup collection; never set as a real OS environment variable."
        ),
    ),
    "PIP_CERT": AmbientEnvDisposition(
        var="PIP_CERT",
        disposition="preserve",
        owner="posix",
        justification=(
            "Generic POSIX/system environment configuration (process"
            "locate/locale/proxy/certificate config) required for basic subprocess correctness;"
            "not AutoSkillit-private state."
        ),
    ),
    "PIP_EXTRA_INDEX_URL": AmbientEnvDisposition(
        var="PIP_EXTRA_INDEX_URL",
        disposition="preserve",
        owner="posix",
        justification=(
            "Generic POSIX/system environment configuration (process"
            "locate/locale/proxy/certificate config) required for basic subprocess correctness;"
            "not AutoSkillit-private state."
        ),
    ),
    "PIP_INDEX_URL": AmbientEnvDisposition(
        var="PIP_INDEX_URL",
        disposition="preserve",
        owner="posix",
        justification=(
            "Generic POSIX/system environment configuration (process"
            "locate/locale/proxy/certificate config) required for basic subprocess correctness;"
            "not AutoSkillit-private state."
        ),
    ),
    "PIP_TRUSTED_HOST": AmbientEnvDisposition(
        var="PIP_TRUSTED_HOST",
        disposition="preserve",
        owner="posix",
        justification=(
            "Generic POSIX/system environment configuration (process"
            "locate/locale/proxy/certificate config) required for basic subprocess correctness;"
            "not AutoSkillit-private state."
        ),
    ),
    "POST": AmbientEnvDisposition(
        var="POST",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "R4 predicate-(b) false positive: an all-uppercase enum/status/regex-name/label member"
            "of an unrelated lookup collection; never set as a real OS environment variable."
        ),
    ),
    "PROVIDER_PROFILE_ENV_VAR": AmbientEnvDisposition(
        var="PROVIDER_PROFILE_ENV_VAR",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "R4 predicate-(b) false positive: an all-uppercase enum/status/regex-name/label member"
            "of an unrelated lookup collection; never set as a real OS environment variable."
        ),
    ),
    "PUBLIC_NAME_PREFIX": AmbientEnvDisposition(
        var="PUBLIC_NAME_PREFIX",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "R4 predicate-(b) false positive: an all-uppercase enum/status/regex-name/label member"
            "of an unrelated lookup collection; never set as a real OS environment variable."
        ),
    ),
    "PUBLIC_NAME_RE": AmbientEnvDisposition(
        var="PUBLIC_NAME_RE",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "R4 predicate-(b) false positive: an all-uppercase enum/status/regex-name/label member"
            "of an unrelated lookup collection; never set as a real OS environment variable."
        ),
    ),
    "PUBLIC_NAME_SUFFIX": AmbientEnvDisposition(
        var="PUBLIC_NAME_SUFFIX",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "R4 predicate-(b) false positive: an all-uppercase enum/status/regex-name/label member"
            "of an unrelated lookup collection; never set as a real OS environment variable."
        ),
    ),
    "PUBLISHED": AmbientEnvDisposition(
        var="PUBLISHED",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "R4 predicate-(b) false positive: an all-uppercase enum/status/regex-name/label member"
            "of an unrelated lookup collection; never set as a real OS environment variable."
        ),
    ),
    "PUT": AmbientEnvDisposition(
        var="PUT",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "R4 predicate-(b) false positive: an all-uppercase enum/status/regex-name/label member"
            "of an unrelated lookup collection; never set as a real OS environment variable."
        ),
    ),
    "PYTEST_CURRENT_TEST": AmbientEnvDisposition(
        var="PYTEST_CURRENT_TEST",
        disposition="preserve",
        owner="harness",
        justification=(
            "pytest-xdist runner contract variable; deleting PYTEST_CURRENT_TEST or its siblings"
            "mid-run breaks the harness executing the scrub itself."
        ),
    ),
    "QUARANTINED": AmbientEnvDisposition(
        var="QUARANTINED",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "R4 predicate-(b) false positive: an all-uppercase enum/status/regex-name/label member"
            "of an unrelated lookup collection; never set as a real OS environment variable."
        ),
    ),
    "QUARANTINE_NAME_RE": AmbientEnvDisposition(
        var="QUARANTINE_NAME_RE",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "R4 predicate-(b) false positive: an all-uppercase enum/status/regex-name/label member"
            "of an unrelated lookup collection; never set as a real OS environment variable."
        ),
    ),
    "RECORD_SCENARIO": AmbientEnvDisposition(
        var="RECORD_SCENARIO",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "Real AutoSkillit orchestration/session-control environment variable read by"
            "production code; scrubbed as internal state that must not leak across test"
            "boundaries."
        ),
    ),
    "RECORD_SCENARIO_DIR": AmbientEnvDisposition(
        var="RECORD_SCENARIO_DIR",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "Real AutoSkillit orchestration/session-control environment variable read by"
            "production code; scrubbed as internal state that must not leak across test"
            "boundaries."
        ),
    ),
    "RECORD_SCENARIO_RECIPE": AmbientEnvDisposition(
        var="RECORD_SCENARIO_RECIPE",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "Real AutoSkillit orchestration/session-control environment variable read by"
            "production code; scrubbed as internal state that must not leak across test"
            "boundaries."
        ),
    ),
    "REFERENCE_RE": AmbientEnvDisposition(
        var="REFERENCE_RE",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "R4 predicate-(b) false positive: an all-uppercase enum/status/regex-name/label member"
            "of an unrelated lookup collection; never set as a real OS environment variable."
        ),
    ),
    "REPLAY_SCENARIO": AmbientEnvDisposition(
        var="REPLAY_SCENARIO",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "Real AutoSkillit orchestration/session-control environment variable read by"
            "production code; scrubbed as internal state that must not leak across test"
            "boundaries."
        ),
    ),
    "REPLAY_SCENARIO_DIR": AmbientEnvDisposition(
        var="REPLAY_SCENARIO_DIR",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "Real AutoSkillit orchestration/session-control environment variable read by"
            "production code; scrubbed as internal state that must not leak across test"
            "boundaries."
        ),
    ),
    "REQUESTS_CA_BUNDLE": AmbientEnvDisposition(
        var="REQUESTS_CA_BUNDLE",
        disposition="preserve",
        owner="posix",
        justification=(
            "Generic POSIX/system environment configuration (process"
            "locate/locale/proxy/certificate config) required for basic subprocess correctness;"
            "not AutoSkillit-private state."
        ),
    ),
    "REQUEST_CHANGES": AmbientEnvDisposition(
        var="REQUEST_CHANGES",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "R4 predicate-(b) false positive: an all-uppercase enum/status/regex-name/label member"
            "of an unrelated lookup collection; never set as a real OS environment variable."
        ),
    ),
    "RESERVED_LOG_RECORD_KEYS": AmbientEnvDisposition(
        var="RESERVED_LOG_RECORD_KEYS",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "R4 predicate-(b) false positive: an all-uppercase enum/status/regex-name/label member"
            "of an unrelated lookup collection; never set as a real OS environment variable."
        ),
    ),
    "RESUME_SESSION_BASELINE_KEYS": AmbientEnvDisposition(
        var="RESUME_SESSION_BASELINE_KEYS",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "R4 predicate-(b) false positive: an all-uppercase enum/status/regex-name/label member"
            "of an unrelated lookup collection; never set as a real OS environment variable."
        ),
    ),
    "RIGHT": AmbientEnvDisposition(
        var="RIGHT",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "R4 predicate-(b) false positive: an all-uppercase enum/status/regex-name/label member"
            "of an unrelated lookup collection; never set as a real OS environment variable."
        ),
    ),
    "SCENARIO_STEP_NAME": AmbientEnvDisposition(
        var="SCENARIO_STEP_NAME",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "AutoSkillit-private session/orchestration variable in the"
            "AUTOSKILLIT_PRIVATE_ENV_VARS / _HEADLESS_EXCLUSIVE_VARS baseline; must not leak"
            "between sibling or nested sessions."
        ),
    ),
    "SESSION_TYPE_ENV_VAR": AmbientEnvDisposition(
        var="SESSION_TYPE_ENV_VAR",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "R4 predicate-(b) false positive: an all-uppercase enum/status/regex-name/label member"
            "of an unrelated lookup collection; never set as a real OS environment variable."
        ),
    ),
    "SESSION_TYPE_FLEET": AmbientEnvDisposition(
        var="SESSION_TYPE_FLEET",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "R4 predicate-(b) false positive: an all-uppercase enum/status/regex-name/label member"
            "of an unrelated lookup collection; never set as a real OS environment variable."
        ),
    ),
    "SESSION_TYPE_ORCHESTRATOR": AmbientEnvDisposition(
        var="SESSION_TYPE_ORCHESTRATOR",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "R4 predicate-(b) false positive: an all-uppercase enum/status/regex-name/label member"
            "of an unrelated lookup collection; never set as a real OS environment variable."
        ),
    ),
    "SESSION_TYPE_SKILL": AmbientEnvDisposition(
        var="SESSION_TYPE_SKILL",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "R4 predicate-(b) false positive: an all-uppercase enum/status/regex-name/label member"
            "of an unrelated lookup collection; never set as a real OS environment variable."
        ),
    ),
    "SHA256_RE": AmbientEnvDisposition(
        var="SHA256_RE",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "R4 predicate-(b) false positive: an all-uppercase enum/status/regex-name/label member"
            "of an unrelated lookup collection; never set as a real OS environment variable."
        ),
    ),
    "SHELL": AmbientEnvDisposition(
        var="SHELL",
        disposition="preserve",
        owner="posix",
        justification=(
            "Generic POSIX/system environment configuration (process"
            "locate/locale/proxy/certificate config) required for basic subprocess correctness;"
            "not AutoSkillit-private state."
        ),
    ),
    "SKILL_SESSION_REQUIRED_ENV": AmbientEnvDisposition(
        var="SKILL_SESSION_REQUIRED_ENV",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "R4 predicate-(b) false positive: an all-uppercase enum/status/regex-name/label member"
            "of an unrelated lookup collection; never set as a real OS environment variable."
        ),
    ),
    "SSL_CERT_DIR": AmbientEnvDisposition(
        var="SSL_CERT_DIR",
        disposition="preserve",
        owner="posix",
        justification=(
            "Generic POSIX/system environment configuration (process"
            "locate/locale/proxy/certificate config) required for basic subprocess correctness;"
            "not AutoSkillit-private state."
        ),
    ),
    "SSL_CERT_FILE": AmbientEnvDisposition(
        var="SSL_CERT_FILE",
        disposition="preserve",
        owner="posix",
        justification=(
            "Generic POSIX/system environment configuration (process"
            "locate/locale/proxy/certificate config) required for basic subprocess correctness;"
            "not AutoSkillit-private state."
        ),
    ),
    "STAGING_NAME_RE": AmbientEnvDisposition(
        var="STAGING_NAME_RE",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "R4 predicate-(b) false positive: an all-uppercase enum/status/regex-name/label member"
            "of an unrelated lookup collection; never set as a real OS environment variable."
        ),
    ),
    "STEP": AmbientEnvDisposition(
        var="STEP",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "R4 predicate-(b) false positive: an all-uppercase enum/status/regex-name/label member"
            "of an unrelated lookup collection; never set as a real OS environment variable."
        ),
    ),
    "STEPS": AmbientEnvDisposition(
        var="STEPS",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "R4 predicate-(b) false positive: an all-uppercase enum/status/regex-name/label member"
            "of an unrelated lookup collection; never set as a real OS environment variable."
        ),
    ),
    "STORAGE_FAILURE": AmbientEnvDisposition(
        var="STORAGE_FAILURE",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "R4 predicate-(b) false positive: an all-uppercase enum/status/regex-name/label member"
            "of an unrelated lookup collection; never set as a real OS environment variable."
        ),
    ),
    "STRUCTURAL": AmbientEnvDisposition(
        var="STRUCTURAL",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "R4 predicate-(b) false positive: an all-uppercase enum/status/regex-name/label member"
            "of an unrelated lookup collection; never set as a real OS environment variable."
        ),
    ),
    "SYSTEMROOT": AmbientEnvDisposition(
        var="SYSTEMROOT",
        disposition="preserve",
        owner="posix",
        justification=(
            "Generic POSIX/system environment configuration (process"
            "locate/locale/proxy/certificate config) required for basic subprocess correctness;"
            "not AutoSkillit-private state."
        ),
    ),
    "TEMP": AmbientEnvDisposition(
        var="TEMP",
        disposition="preserve",
        owner="posix",
        justification=(
            "Generic POSIX/system environment configuration (process"
            "locate/locale/proxy/certificate config) required for basic subprocess correctness;"
            "not AutoSkillit-private state."
        ),
    ),
    "TERM": AmbientEnvDisposition(
        var="TERM",
        disposition="preserve",
        owner="posix",
        justification=(
            "Generic POSIX/system environment configuration (process"
            "locate/locale/proxy/certificate config) required for basic subprocess correctness;"
            "not AutoSkillit-private state."
        ),
    ),
    "TERM_PROGRAM": AmbientEnvDisposition(
        var="TERM_PROGRAM",
        disposition="preserve",
        owner="posix",
        justification=(
            "Generic POSIX/system environment configuration (process"
            "locate/locale/proxy/certificate config) required for basic subprocess correctness;"
            "not AutoSkillit-private state."
        ),
    ),
    "TIME": AmbientEnvDisposition(
        var="TIME",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "R4 predicate-(b) false positive: an all-uppercase enum/status/regex-name/label member"
            "of an unrelated lookup collection; never set as a real OS environment variable."
        ),
    ),
    "TMP": AmbientEnvDisposition(
        var="TMP",
        disposition="preserve",
        owner="posix",
        justification=(
            "Generic POSIX/system environment configuration (process"
            "locate/locale/proxy/certificate config) required for basic subprocess correctness;"
            "not AutoSkillit-private state."
        ),
    ),
    "TMPDIR": AmbientEnvDisposition(
        var="TMPDIR",
        disposition="preserve",
        owner="harness",
        justification=(
            "Test-harness-owned temp-directory root the pytest/tmp-lifecycle machinery itself"
            "depends on; deleting it mid-run would break the scrub fixture's own execution."
        ),
    ),
    "TURNS": AmbientEnvDisposition(
        var="TURNS",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "R4 predicate-(b) false positive: an all-uppercase enum/status/regex-name/label member"
            "of an unrelated lookup collection; never set as a real OS environment variable."
        ),
    ),
    "UNCACHED": AmbientEnvDisposition(
        var="UNCACHED",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "R4 predicate-(b) false positive: an all-uppercase enum/status/regex-name/label member"
            "of an unrelated lookup collection; never set as a real OS environment variable."
        ),
    ),
    "UNKNOWN": AmbientEnvDisposition(
        var="UNKNOWN",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "R4 predicate-(b) false positive: an all-uppercase enum/status/regex-name/label member"
            "of an unrelated lookup collection; never set as a real OS environment variable."
        ),
    ),
    "UNSTABLE": AmbientEnvDisposition(
        var="UNSTABLE",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "R4 predicate-(b) false positive: an all-uppercase enum/status/regex-name/label member"
            "of an unrelated lookup collection; never set as a real OS environment variable."
        ),
    ),
    "USER": AmbientEnvDisposition(
        var="USER",
        disposition="preserve",
        owner="posix",
        justification=(
            "Generic POSIX/system environment configuration (process"
            "locate/locale/proxy/certificate config) required for basic subprocess correctness;"
            "not AutoSkillit-private state."
        ),
    ),
    "UV_DEFAULT_INDEX": AmbientEnvDisposition(
        var="UV_DEFAULT_INDEX",
        disposition="preserve",
        owner="posix",
        justification=(
            "Generic POSIX/system environment configuration (process"
            "locate/locale/proxy/certificate config) required for basic subprocess correctness;"
            "not AutoSkillit-private state."
        ),
    ),
    "UV_EXTRA_INDEX_URL": AmbientEnvDisposition(
        var="UV_EXTRA_INDEX_URL",
        disposition="preserve",
        owner="posix",
        justification=(
            "Generic POSIX/system environment configuration (process"
            "locate/locale/proxy/certificate config) required for basic subprocess correctness;"
            "not AutoSkillit-private state."
        ),
    ),
    "UV_INDEX_URL": AmbientEnvDisposition(
        var="UV_INDEX_URL",
        disposition="preserve",
        owner="posix",
        justification=(
            "Generic POSIX/system environment configuration (process"
            "locate/locale/proxy/certificate config) required for basic subprocess correctness;"
            "not AutoSkillit-private state."
        ),
    ),
    "VSCODE_GIT_ASKPASS_MAIN": AmbientEnvDisposition(
        var="VSCODE_GIT_ASKPASS_MAIN",
        disposition="scrub",
        owner="claude-code",
        justification=(
            "IDE discovery/bridge variable in IDE_ENV_DENYLIST that lets a host IDE (VS"
            "Code/Cursor/Zed) attach across the trust boundary; scrubbed to prevent test leakage."
        ),
    ),
    "WINDIR": AmbientEnvDisposition(
        var="WINDIR",
        disposition="preserve",
        owner="posix",
        justification=(
            "Generic POSIX/system environment configuration (process"
            "locate/locale/proxy/certificate config) required for basic subprocess correctness;"
            "not AutoSkillit-private state."
        ),
    ),
    "XDG_CACHE_HOME": AmbientEnvDisposition(
        var="XDG_CACHE_HOME",
        disposition="preserve",
        owner="posix",
        justification=(
            "Generic POSIX/system environment configuration (process"
            "locate/locale/proxy/certificate config) required for basic subprocess correctness;"
            "not AutoSkillit-private state."
        ),
    ),
    "XDG_CONFIG_HOME": AmbientEnvDisposition(
        var="XDG_CONFIG_HOME",
        disposition="preserve",
        owner="posix",
        justification=(
            "Generic POSIX/system environment configuration (process"
            "locate/locale/proxy/certificate config) required for basic subprocess correctness;"
            "not AutoSkillit-private state."
        ),
    ),
    "XDG_DATA_HOME": AmbientEnvDisposition(
        var="XDG_DATA_HOME",
        disposition="preserve",
        owner="posix",
        justification=(
            "Generic POSIX/system environment configuration (process"
            "locate/locale/proxy/certificate config) required for basic subprocess correctness;"
            "not AutoSkillit-private state."
        ),
    ),
    "XDG_RUNTIME_DIR": AmbientEnvDisposition(
        var="XDG_RUNTIME_DIR",
        disposition="preserve",
        owner="posix",
        justification=(
            "Generic POSIX/system environment configuration (process"
            "locate/locale/proxy/certificate config) required for basic subprocess correctness;"
            "not AutoSkillit-private state."
        ),
    ),
    "XDG_STATE_HOME": AmbientEnvDisposition(
        var="XDG_STATE_HOME",
        disposition="preserve",
        owner="posix",
        justification=(
            "Generic POSIX/system environment configuration (process"
            "locate/locale/proxy/certificate config) required for basic subprocess correctness;"
            "not AutoSkillit-private state."
        ),
    ),
    "ZED_TERM": AmbientEnvDisposition(
        var="ZED_TERM",
        disposition="scrub",
        owner="claude-code",
        justification=(
            "IDE discovery/bridge variable in IDE_ENV_DENYLIST that lets a host IDE (VS"
            "Code/Cursor/Zed) attach across the trust boundary; scrubbed to prevent test leakage."
        ),
    ),
    "_API_KEY": AmbientEnvDisposition(
        var="_API_KEY",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "R4 false positive: a secret-name-detection suffix fragment from"
            "launch_resolution.py's redaction heuristic, not a full variable name; never itself a"
            "real env var."
        ),
    ),
    "_CREDENTIAL": AmbientEnvDisposition(
        var="_CREDENTIAL",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "R4 false positive: a secret-name-detection suffix fragment from"
            "launch_resolution.py's redaction heuristic, not a full variable name; never itself a"
            "real env var."
        ),
    ),
    "_PASSWORD": AmbientEnvDisposition(
        var="_PASSWORD",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "R4 false positive: a secret-name-detection suffix fragment from"
            "launch_resolution.py's redaction heuristic, not a full variable name; never itself a"
            "real env var."
        ),
    ),
    "_SECRET": AmbientEnvDisposition(
        var="_SECRET",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "R4 false positive: a secret-name-detection suffix fragment from"
            "launch_resolution.py's redaction heuristic, not a full variable name; never itself a"
            "real env var."
        ),
    ),
    "_TOKEN": AmbientEnvDisposition(
        var="_TOKEN",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "R4 false positive: a secret-name-detection suffix fragment from"
            "launch_resolution.py's redaction heuristic, not a full variable name; never itself a"
            "real env var."
        ),
    ),
    "all_proxy": AmbientEnvDisposition(
        var="all_proxy",
        disposition="preserve",
        owner="posix",
        justification=(
            "Generic POSIX/system environment configuration (process"
            "locate/locale/proxy/certificate config) required for basic subprocess correctness;"
            "not AutoSkillit-private state."
        ),
    ),
    "encoding_version": AmbientEnvDisposition(
        var="encoding_version",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "R4 false positive: a persistence-envelope schema field name swept in only because the"
            "defining collection name contains the substring ENV; unrelated to os.environ."
        ),
    ),
    "http_proxy": AmbientEnvDisposition(
        var="http_proxy",
        disposition="preserve",
        owner="posix",
        justification=(
            "Generic POSIX/system environment configuration (process"
            "locate/locale/proxy/certificate config) required for basic subprocess correctness;"
            "not AutoSkillit-private state."
        ),
    ),
    "https_proxy": AmbientEnvDisposition(
        var="https_proxy",
        disposition="preserve",
        owner="posix",
        justification=(
            "Generic POSIX/system environment configuration (process"
            "locate/locale/proxy/certificate config) required for basic subprocess correctness;"
            "not AutoSkillit-private state."
        ),
    ),
    "no_proxy": AmbientEnvDisposition(
        var="no_proxy",
        disposition="preserve",
        owner="posix",
        justification=(
            "Generic POSIX/system environment configuration (process"
            "locate/locale/proxy/certificate config) required for basic subprocess correctness;"
            "not AutoSkillit-private state."
        ),
    ),
    "payload": AmbientEnvDisposition(
        var="payload",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "R4 false positive: a persistence-envelope schema field name swept in only because the"
            "defining collection name contains the substring ENV; unrelated to os.environ."
        ),
    ),
    "protocol_version": AmbientEnvDisposition(
        var="protocol_version",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "R4 false positive: a persistence-envelope schema field name swept in only because the"
            "defining collection name contains the substring ENV; unrelated to os.environ."
        ),
    ),
    "type_discriminator": AmbientEnvDisposition(
        var="type_discriminator",
        disposition="scrub",
        owner="autoskillit",
        justification=(
            "R4 false positive: a persistence-envelope schema field name swept in only because the"
            "defining collection name contains the substring ENV; unrelated to os.environ."
        ),
    ),
}
