"""Keep bare Git revision resolution sites explicitly inventoried.

Git resolves an unqualified revision through multiple namespaces.  This guard
does not rewrite every existing site; it makes each bare or unresolvable one a
reviewed inventory entry and fails closed for command shapes it cannot prove.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from typing import Final

import pytest

from tests.arch._deferred_debt import (
    TrackedDeferral,
    assert_deferrals_have_regression_tests,
    assert_entries_still_apply,
    assert_not_stale,
    assert_rationale_present,
)
from tests.arch._helpers import SRC_ROOT

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

_VIOLATION_CLASSIFICATIONS: Final = frozenset({"BARE", "UNRESOLVABLE"})
_NO_REF_SUBCOMMANDS: Final = frozenset(
    {
        "add",
        "check-ignore",
        "clean",
        "clone",
        "commit",
        "config",
        "fsck",
        "gc",
        "help",
        "init",
        "ls-files",
        "remote",
        "status",
        "version",
        "write-tree",
        "worktree",
    }
)
_ALL_NON_FLAG_REVISIONS: Final = frozenset(
    {
        "cat-file",
        "log",
        "merge",
        "merge-base",
        "rebase",
        "reset",
        "rev-list",
        "rev-parse",
        "show",
    }
)
_REMOTE_REFSPEC_SUBCOMMANDS: Final = frozenset({"fetch", "ls-remote", "push"})
_GLOBAL_OPTIONS_WITH_VALUE: Final = frozenset(
    {"-C", "-c", "--git-dir", "--namespace", "--work-tree"}
)
_GLOBAL_OPTIONS_WITH_INLINE_VALUE: Final = (
    "--git-dir=",
    "--namespace=",
    "--work-tree=",
)
_GLOBAL_OPTIONS_WITHOUT_VALUE: Final = frozenset(
    {
        "--bare",
        "--literal-pathspecs",
        "--no-lazy-fetch",
        "--no-pager",
        "--no-replace-objects",
        "--paginate",
    }
)
_PSEUDO_REF_PATTERN: Final = re.compile(
    r"^(?:FETCH_HEAD|HEAD|MERGE_HEAD|ORIG_HEAD|@)(?:(?:\^)|(?:~\d+))*$"
)
_SHA_PATTERN: Final = re.compile(r"^(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})$")
_CLONE_GUARD_PATH: Final = "execution/runtime/clone_guard.py"
_CMD_RPC_MERGE_PATH: Final = "recipe/cmd_rpc/_cmd_rpc_merge.py"

# (relative path, enclosing function, line number, subcommand, classification)
_EXPECTED_GIT_REF_SITES: tuple[tuple[str, str, int, str, str], ...] = (
    ("cli/update/_update_checks_source.py", "_git_ls_remote_sha", 175, "ls-remote", "BARE"),
    ("core/git/git_refs.py", "verify_qualified_ref_sync", 61, "rev-parse", "BARE"),
    ("core/install/cmd_runner.py", "run_git", 62, "<unresolved>", "UNRESOLVABLE"),
    ("execution/headless/_headless_git.py", "_compute_loc_changed", 56, "diff", "BARE"),
    (
        "execution/headless/_headless_git.py",
        "_detect_branch_divergence",
        97,
        "merge-base",
        "BARE",
    ),
    (_CLONE_GUARD_PATH, "_prune_stash_overflow", 459, "stash", "UNRESOLVABLE"),
    (_CLONE_GUARD_PATH, "_prune_stash_overflow", 481, "stash", "UNRESOLVABLE"),
    (_CLONE_GUARD_PATH, "_stash_and_clean", 406, "stash", "UNRESOLVABLE"),
    (_CLONE_GUARD_PATH, "revert_contamination", 585, "reset", "BARE"),
    (_CLONE_GUARD_PATH, "revert_contamination", 604, "reset", "BARE"),
    (_CLONE_GUARD_PATH, "revert_contamination", 619, "reset", "BARE"),
    (_CLONE_GUARD_PATH, "validate_pre_session_index", 316, "reset", "BARE"),
    (_CLONE_GUARD_PATH, "validate_pre_session_index", 347, "rm", "UNRESOLVABLE"),
    (
        "execution/headless/_headless_git.py",
        "_detect_branch_divergence",
        110,
        "rev-list",
        "BARE",
    ),
    ("exploration/identity.py", "_git_value", 112, "<unresolved>", "UNRESOLVABLE"),
    ("exploration/snapshot/_capture.py", "_git", 94, "<unresolved>", "UNRESOLVABLE"),
    ("fleet/_reset.py", "_delete_reset_branches", 258, "push", "BARE"),
    (
        "hooks/guards/_git_command_classification.py",
        "_git_result",
        224,
        "<unresolved>",
        "UNRESOLVABLE",
    ),
    ("hooks/guards/remove_clone_guard.py", "_git", 47, "<unresolved>", "UNRESOLVABLE"),
    ("recipe/cmd_rpc/_cmd_rpc_guards.py", "_check_regression", 543, "merge-base", "BARE"),
    (
        "recipe/cmd_rpc/_cmd_rpc_guards.py",
        "_check_regression",
        548,
        "<unresolved>",
        "UNRESOLVABLE",
    ),
    (
        "recipe/cmd_rpc/_cmd_rpc_guards.py",
        "_check_regression",
        558,
        "<unresolved>",
        "UNRESOLVABLE",
    ),
    ("recipe/cmd_rpc/_cmd_rpc_guards.py", "commit_guard", 648, "<unresolved>", "UNRESOLVABLE"),
    ("recipe/cmd_rpc/_cmd_rpc_guards.py", "main_repo_guard", 167, "stash", "UNRESOLVABLE"),
    (_CMD_RPC_MERGE_PATH, "_attempt_rebase_with_conflict_report", 135, "fetch", "BARE"),
    (_CMD_RPC_MERGE_PATH, "_attempt_rebase_with_conflict_report", 136, "fetch", "BARE"),
    (
        _CMD_RPC_MERGE_PATH,
        "_attempt_rebase_with_conflict_report",
        139,
        "<unresolved>",
        "UNRESOLVABLE",
    ),
    (_CMD_RPC_MERGE_PATH, "_attempt_rebase_with_conflict_report", 140, "rebase", "BARE"),
    (_CMD_RPC_MERGE_PATH, "create_persistent_integration", 195, "checkout", "BARE"),
    (_CMD_RPC_MERGE_PATH, "create_persistent_integration", 196, "pull", "UNRESOLVABLE"),
    (_CMD_RPC_MERGE_PATH, "create_persistent_integration", 198, "push", "BARE"),
    (_CMD_RPC_MERGE_PATH, "force_push_and_wait_mergeability", 217, "push", "BARE"),
    (_CMD_RPC_MERGE_PATH, "queue_ejected_fix", 47, "fetch", "BARE"),
    (_CMD_RPC_MERGE_PATH, "queue_ejected_fix", 50, "rebase", "BARE"),
    ("server/_self_revert.py", "_scan_commit", 147, "rev-list", "BARE"),
    ("server/_self_revert.py", "_scan_commit", 147, "rev-list", "BARE"),
    ("server/_self_revert.py", "_scan_commit", 163, "show", "BARE"),
    ("server/_self_revert.py", "detect_self_reverts", 238, "rev-list", "BARE"),
    ("server/git.py", "perform_merge", 322, "<unresolved>", "UNRESOLVABLE"),
    ("server/git.py", "perform_merge", 327, "<unresolved>", "UNRESOLVABLE"),
    ("server/git.py", "perform_merge", 445, "log", "BARE"),
    ("server/git.py", "perform_merge", 468, "rebase", "BARE"),
    ("server/git.py", "perform_merge", 548, "rev-parse", "BARE"),
    ("server/git.py", "perform_merge", 565, "merge-base", "BARE"),
    ("server/git.py", "perform_merge", 565, "merge-base", "BARE"),
    ("server/git.py", "perform_merge", 617, "merge", "BARE"),
    ("server/tools/tools_ci_watch.py", "_auto_trigger_ci", 452, "push", "BARE"),
    ("server/tools/tools_git.py", "classify_fix", 198, "fetch", "BARE"),
    ("server/tools/tools_git.py", "classify_fix", 217, "diff", "BARE"),
    (
        "server/tools/_pre_commit_transaction.py",
        "_run_pre_commit_transaction",
        67,
        "<unresolved>",
        "UNRESOLVABLE",
    ),
    ("server/tools/_self_revert.py", "validate_self_revert_base", 26, "rev-parse", "BARE"),
    ("server/tools/_self_revert.py", "validate_self_revert_base", 39, "merge-base", "BARE"),
    ("server/tools/tools_workspace.py", "commit_files", 374, "<unresolved>", "UNRESOLVABLE"),
    ("smoke_utils/_git.py", "check_commits_ahead", 179, "rev-list", "BARE"),
    ("smoke_utils/_git.py", "check_ref_state", 235, "ls-remote", "BARE"),
    ("smoke_utils/_git.py", "check_ref_state", 259, "merge-base", "BARE"),
    ("smoke_utils/_git.py", "check_ref_state", 259, "merge-base", "BARE"),
    ("smoke_utils/_git.py", "compute_domain_partitions", 45, "diff", "BARE"),
    ("smoke_utils/_git.py", "detect_zero_changes", 135, "rev-list", "BARE"),
    ("smoke_utils/_review.py", "annotate_pr_diff", 268, "merge-base", "BARE"),
    ("smoke_utils/_review.py", "annotate_pr_diff", 268, "merge-base", "BARE"),
    ("smoke_utils/_review.py", "annotate_pr_diff", 282, "diff", "BARE"),
    ("smoke_utils/_review.py", "annotate_pr_diff", 283, "diff", "BARE"),
    (
        "smoke_utils/_review.py",
        "annotate_pr_diff._ensure_provider_objects",
        213,
        "cat-file",
        "BARE",
    ),
    (
        "smoke_utils/_review.py",
        "annotate_pr_diff._ensure_provider_objects",
        243,
        "fetch",
        "BARE",
    ),
    (
        "workspace/clone/__init__.py",
        "_decontaminate_generated_files",
        169,
        "<unresolved>",
        "UNRESOLVABLE",
    ),
    (
        "workspace/clone/__init__.py",
        "_decontaminate_generated_files",
        177,
        "<unresolved>",
        "UNRESOLVABLE",
    ),
    ("workspace/clone/__init__.py", "push_to_remote", 511, "push", "BARE"),
)
_DEFERRED_REF_SITES: dict[tuple[str, str, int, str, str], TrackedDeferral] = {}


@dataclass(frozen=True)
class _GitRefSite:
    path: str
    function: str
    lineno: int
    subcommand: str
    classification: str

    @property
    def violation(self) -> bool:
        return self.classification in _VIOLATION_CLASSIFICATIONS


def _constant_string(expression: ast.expr) -> str | None:
    if isinstance(expression, ast.Constant) and isinstance(expression.value, str):
        return expression.value
    return None


def _joined_string_has_qualified_prefix(expression: ast.JoinedStr) -> bool:
    first = expression.values[0] if expression.values else None
    return (
        isinstance(first, ast.Constant)
        and isinstance(first.value, str)
        and first.value.startswith("refs/")
    )


def _classify_revision(expression: ast.expr) -> str:
    value = _constant_string(expression)
    if value is not None:
        if value.startswith("refs/"):
            return "QUALIFIED"
        if _PSEUDO_REF_PATTERN.fullmatch(value):
            return "PSEUDO_REF"
        if _SHA_PATTERN.fullmatch(value):
            return "SHA_LITERAL"
        if value.startswith("-"):
            return "FLAG"
        return "BARE"
    if isinstance(expression, ast.JoinedStr):
        return "QUALIFIED" if _joined_string_has_qualified_prefix(expression) else "BARE"
    if isinstance(expression, ast.Starred | ast.List | ast.Tuple):
        return "UNRESOLVABLE"
    return "BARE"


def _non_flag_indices(arguments: tuple[ast.expr, ...]) -> tuple[int, ...]:
    return tuple(
        index
        for index, argument in enumerate(arguments)
        if not (_constant_string(argument) or "").startswith("-")
    )


def _before_path_separator(arguments: tuple[ast.expr, ...]) -> tuple[ast.expr, ...]:
    for index, argument in enumerate(arguments):
        if _constant_string(argument) == "--":
            return arguments[:index]
    return arguments


def _revision_indices(subcommand: str, arguments: tuple[ast.expr, ...]) -> tuple[int, ...] | None:
    before_separator = _before_path_separator(arguments)
    if subcommand in _ALL_NON_FLAG_REVISIONS or subcommand == "diff":
        return _non_flag_indices(before_separator)
    if subcommand in _REMOTE_REFSPEC_SUBCOMMANDS:
        positions = _non_flag_indices(before_separator)
        return positions[1:]
    if subcommand in {"checkout", "switch"}:
        if any(
            _constant_string(argument) in {"-B", "-C", "-b", "-c"} for argument in before_separator
        ):
            return ()
        positions = _non_flag_indices(before_separator)
        return positions[:1]
    if subcommand == "branch":
        if any(
            _constant_string(argument)
            in {
                "-D",
                "-M",
                "-d",
                "-m",
                "--copy",
                "--delete",
                "--move",
                "--show-current",
            }
            for argument in before_separator
        ):
            return ()
        positions = _non_flag_indices(before_separator)
        return positions[-1:] if len(positions) >= 2 else ()
    if subcommand == "symbolic-ref":
        positions = _non_flag_indices(before_separator)
        return positions[1:2]
    if subcommand in _NO_REF_SUBCOMMANDS:
        return ()
    return None


def _process_argv(node: ast.Call) -> ast.expr | None:
    if node.args:
        return node.args[0]
    return next((keyword.value for keyword in node.keywords if keyword.arg == "args"), None)


def _is_run_git_call(node: ast.Call) -> bool:
    return isinstance(node.func, ast.Name) and node.func.id == "run_git"


def _is_string_join_call(node: ast.Call) -> bool:
    """A ``str.join`` input can spell a command without executing one."""
    return isinstance(node.func, ast.Attribute) and node.func.attr == "join"


class _GitRefCollector(ast.NodeVisitor):
    def __init__(self, path: str) -> None:
        self._path = path
        self._functions: list[str] = []
        self._assignments: list[dict[str, ast.expr | None]] = [{}]
        self.sites: list[_GitRefSite] = []

    @property
    def _scope(self) -> dict[str, ast.expr | None]:
        return self._assignments[-1]

    @property
    def _function(self) -> str:
        return ".".join(self._functions) or "<module>"

    def _visit_scope(self, node: ast.AST) -> None:
        self._assignments.append({})
        self.generic_visit(node)
        self._assignments.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._functions.append(node.name)
        self._visit_scope(node)
        self._functions.pop()

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._functions.append(node.name)
        self._visit_scope(node)
        self._functions.pop()

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._visit_scope(node)

    def visit_DictComp(self, node: ast.DictComp) -> None:
        self._visit_scope(node)

    def visit_GeneratorExp(self, node: ast.GeneratorExp) -> None:
        self._visit_scope(node)

    def visit_ListComp(self, node: ast.ListComp) -> None:
        self._visit_scope(node)

    def visit_SetComp(self, node: ast.SetComp) -> None:
        self._visit_scope(node)

    def visit_Starred(self, node: ast.Starred) -> None:
        # ``_exact_argv`` sees the starred node and marks the consuming argv
        # unresolvable; still visit its value for nested calls.
        self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign) -> None:
        self.generic_visit(node)
        for target in node.targets:
            self._bind_assignment(target, node.value)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        self.generic_visit(node)
        if node.value is not None:
            self._bind_assignment(node.target, node.value)

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        self.generic_visit(node)
        if isinstance(node.target, ast.Name) and isinstance(node.op, ast.Add):
            self._extend_binding(node.target.id, node.value)

    def visit_Call(self, node: ast.Call) -> None:
        if _is_run_git_call(node):
            self._collect_run_git_site(node)
        elif not _is_string_join_call(node):
            command = _process_argv(node)
            if command is not None and self._looks_like_git_argv(command):
                self._collect_direct_git_site(node, command)
        self.generic_visit(node)
        if (
            isinstance(node.func, ast.Attribute)
            and node.func.attr == "append"
            and isinstance(node.func.value, ast.Name)
            and len(node.args) == 1
            and not node.keywords
        ):
            self._append_binding(node.func.value.id, node.args[0])

    def _bind_assignment(self, target: ast.expr, value: ast.expr) -> None:
        if not isinstance(target, ast.Name):
            return
        if (
            isinstance(value, ast.BinOp)
            and isinstance(value.op, ast.Add)
            and isinstance(value.left, ast.Name)
            and value.left.id == target.id
        ):
            self._extend_binding(target.id, value.right)
            return
        self._scope[target.id] = value

    def _append_binding(self, name: str, value: ast.expr) -> None:
        existing = self._scope.get(name)
        if not isinstance(existing, ast.List) or any(
            isinstance(element, ast.Starred) for element in existing.elts
        ):
            self._scope[name] = None
            return
        self._scope[name] = ast.List(elts=[*existing.elts, value], ctx=ast.Load())

    def _extend_binding(self, name: str, value: ast.expr) -> None:
        existing = self._scope.get(name)
        if not isinstance(existing, ast.List) or not isinstance(value, ast.List):
            self._scope[name] = None
            return
        if any(isinstance(element, ast.Starred) for element in (*existing.elts, *value.elts)):
            self._scope[name] = None
            return
        self._scope[name] = ast.List(elts=[*existing.elts, *value.elts], ctx=ast.Load())

    def _one_hop(self, expression: ast.expr) -> ast.expr | None:
        if isinstance(expression, ast.Name):
            return self._scope.get(expression.id)
        return expression

    def _exact_argv(self, expression: ast.expr) -> tuple[ast.expr, ...] | None:
        resolved = self._one_hop(expression)
        if not isinstance(resolved, ast.List) or any(
            isinstance(element, ast.Starred) for element in resolved.elts
        ):
            return None
        return tuple(resolved.elts)

    def _looks_like_git_argv(self, expression: ast.expr) -> bool:
        resolved = self._one_hop(expression)
        if isinstance(resolved, ast.BinOp) and isinstance(resolved.op, ast.Add):
            return self._looks_like_git_argv(resolved.left)
        if not isinstance(resolved, ast.List | ast.Tuple) or not resolved.elts:
            return False
        return _constant_string(resolved.elts[0]) == "git"

    def _collect_direct_git_site(self, call: ast.Call, expression: ast.expr) -> None:
        arguments = self._exact_argv(expression)
        if arguments is None or not arguments or _constant_string(arguments[0]) != "git":
            self._record_unresolvable(call, "<unresolved>")
            return
        self._collect_subcommand_site(call, arguments[1:])

    def _collect_run_git_site(self, call: ast.Call) -> None:
        expression = _process_argv(call)
        arguments = self._exact_argv(expression) if expression is not None else None
        if arguments is None:
            self._record_unresolvable(call, "<unresolved>")
            return
        self._collect_subcommand_site(call, arguments)

    def _collect_subcommand_site(self, call: ast.Call, arguments: tuple[ast.expr, ...]) -> None:
        if not arguments:
            self._record_unresolvable(call, "<unresolved>")
            return
        subcommand_index = self._subcommand_index(arguments)
        if subcommand_index is None:
            self._record_unresolvable(call, "<unresolved>")
            return
        subcommand = _constant_string(arguments[subcommand_index])
        if subcommand is None:
            self._record_unresolvable(call, "<unresolved>")
            return
        revision_arguments = arguments[subcommand_index + 1 :]
        indices = _revision_indices(subcommand, revision_arguments)
        if indices is None:
            self._record_unresolvable(call, subcommand)
            return
        for index in indices:
            argument = revision_arguments[index]
            classification = _classify_revision(argument)
            if classification != "FLAG":
                self.sites.append(
                    _GitRefSite(
                        path=self._path,
                        function=self._function,
                        lineno=getattr(argument, "lineno", call.lineno),
                        subcommand=subcommand,
                        classification=classification,
                    )
                )

    def _subcommand_index(self, arguments: tuple[ast.expr, ...]) -> int | None:
        index = 0
        while index < len(arguments):
            option = _constant_string(arguments[index])
            if option is None:
                return index
            if option in _GLOBAL_OPTIONS_WITH_VALUE:
                index += 2
                continue
            if option.startswith(("-C", "-c")) and option not in {"-C", "-c"}:
                index += 1
                continue
            if option.startswith(_GLOBAL_OPTIONS_WITH_INLINE_VALUE):
                index += 1
                continue
            if option in _GLOBAL_OPTIONS_WITHOUT_VALUE:
                index += 1
                continue
            return index
        return None

    def _record_unresolvable(self, call: ast.Call, subcommand: str) -> None:
        self.sites.append(
            _GitRefSite(
                path=self._path,
                function=self._function,
                lineno=call.lineno,
                subcommand=subcommand,
                classification="UNRESOLVABLE",
            )
        )


def _scan_tree(tree: ast.AST, path: str) -> tuple[_GitRefSite, ...]:
    collector = _GitRefCollector(path)
    collector.visit(tree)
    return tuple(collector.sites)


def _scan_source() -> tuple[_GitRefSite, ...]:
    sites: list[_GitRefSite] = []
    for source_path in sorted(SRC_ROOT.rglob("*.py")):
        relative_path = source_path.relative_to(SRC_ROOT).as_posix()
        source = source_path.read_text(encoding="utf-8")
        sites.extend(_scan_tree(ast.parse(source), relative_path))
    return tuple(sites)


def _inventory_key(site: _GitRefSite) -> tuple[str, str, int, str, str]:
    return site.path, site.function, site.lineno, site.subcommand, site.classification


def _live_violations() -> tuple[_GitRefSite, ...]:
    return tuple(site for site in _scan_source() if site.violation)


def _assert_inventory_matches(
    observed: tuple[tuple[str, str, int, str, str], ...],
    expected: tuple[tuple[str, str, int, str, str], ...],
) -> None:
    assert sorted(observed) == sorted(expected)


def test_no_unqualified_ref_site_outside_inventory() -> None:
    unexpected = [
        _inventory_key(site)
        for site in _live_violations()
        if _inventory_key(site) not in _EXPECTED_GIT_REF_SITES
    ]
    assert not unexpected, f"Uninventoried bare or unresolvable Git refs:\n{unexpected}"


def test_git_ref_site_inventory_is_complete() -> None:
    _assert_inventory_matches(
        tuple(_inventory_key(site) for site in _live_violations()),
        _EXPECTED_GIT_REF_SITES,
    )


def test_git_ref_deferrals_are_live_justified_and_current() -> None:
    live_violations = {_inventory_key(site) for site in _live_violations()}
    assert_entries_still_apply(
        _DEFERRED_REF_SITES,
        registry_name="Git ref qualification deferrals",
        live_keys=live_violations,
    )
    assert_rationale_present(_DEFERRED_REF_SITES, registry_name="Git ref qualification deferrals")
    assert_not_stale(_DEFERRED_REF_SITES, registry_name="Git ref qualification deferrals")
    assert_deferrals_have_regression_tests(
        _DEFERRED_REF_SITES,
        registry_name="Git ref qualification deferrals",
    )
    assert _DEFERRED_REF_SITES == {}


def test_bare_branch_revision_is_caught() -> None:
    sites = _scan_tree(
        ast.parse('subprocess.run(["git", "rev-parse", base_branch])'), "synthetic.py"
    )
    assert [site.classification for site in sites] == ["BARE"]


def test_qualified_literal_ref_is_accepted() -> None:
    sites = _scan_tree(
        ast.parse('subprocess.run(["git", "rev-parse", "--verify", f"refs/heads/{b}"])'),
        "synthetic.py",
    )
    assert [site.classification for site in sites] == ["QUALIFIED"]


def test_pseudo_ref_is_accepted() -> None:
    sites = _scan_tree(ast.parse('subprocess.run(["git", "rev-parse", "HEAD"])'), "synthetic.py")
    assert [site.classification for site in sites] == ["PSEUDO_REF"]


def test_splat_argv_fails_closed() -> None:
    sites = _scan_tree(ast.parse('subprocess.run(["git", *args])'), "synthetic.py")
    assert [site.classification for site in sites] == ["UNRESOLVABLE"]


def test_appended_revision_is_resolved_one_hop() -> None:
    sites = _scan_tree(
        ast.parse('cmd = ["git", "diff"]\ncmd.append(base)\nsubprocess.run(cmd)\n'),
        "synthetic.py",
    )
    assert [site.classification for site in sites] == ["BARE"]


def test_augmented_assignment_revision_is_resolved_one_hop() -> None:
    sites = _scan_tree(
        ast.parse('cmd = ["git", "diff"]\ncmd += [base]\nsubprocess.run(cmd)\n'),
        "synthetic.py",
    )
    assert [site.classification for site in sites] == ["BARE"]


def test_reassigned_revision_is_resolved_one_hop() -> None:
    sites = _scan_tree(
        ast.parse('cmd = ["git", "diff"]\ncmd = cmd + [base]\nsubprocess.run(cmd)\n'),
        "synthetic.py",
    )
    assert [site.classification for site in sites] == ["BARE"]


def test_run_git_wrapper_is_scanned() -> None:
    sites = _scan_tree(ast.parse('run_git(["merge-base", "HEAD", base])'), "synthetic.py")
    assert [site.classification for site in sites] == ["PSEUDO_REF", "BARE"]


@pytest.mark.parametrize(
    "source",
    [
        'subprocess.run(("git", "diff", base))',
        'subprocess.run(["git"] + args)',
    ],
)
def test_tuple_or_concatenated_argv_fails_closed(source: str) -> None:
    sites = _scan_tree(ast.parse(source), "synthetic.py")
    assert [site.classification for site in sites] == ["UNRESOLVABLE"]


def test_nested_scope_bindings_do_not_leak() -> None:
    sites = _scan_tree(
        ast.parse(
            "def outer():\n"
            '    cmd = ["git", "diff", outer_base]\n'
            "    subprocess.run(cmd)\n"
            "    def inner():\n"
            '        cmd = ["git", "diff", inner_base]\n'
            "        subprocess.run(cmd)\n"
        ),
        "synthetic.py",
    )
    assert [(site.function, site.classification) for site in sites] == [
        ("outer", "BARE"),
        ("outer.inner", "BARE"),
    ]


def test_non_ref_subcommand_is_ignored() -> None:
    sites = _scan_tree(
        ast.parse('subprocess.run(["git", "status", "--porcelain"])'), "synthetic.py"
    )
    assert sites == ()


def test_push_remote_name_is_not_a_revision() -> None:
    sites = _scan_tree(
        ast.parse('subprocess.run(["git", "push", "-u", "upstream", branch])'),
        "synthetic.py",
    )
    assert [site.classification for site in sites] == ["BARE"]


def test_checkout_dash_b_names_a_new_branch() -> None:
    sites = _scan_tree(
        ast.parse('subprocess.run(["git", "checkout", "-b", name])'), "synthetic.py"
    )
    assert sites == ()


def test_branch_delete_is_not_a_revision() -> None:
    sites = _scan_tree(ast.parse('subprocess.run(["git", "branch", "-D", name])'), "synthetic.py")
    assert sites == ()


def test_unmodelled_subcommand_fails_closed() -> None:
    sites = _scan_tree(ast.parse('subprocess.run(["git", "notes", "list", rev])'), "synthetic.py")
    assert [site.classification for site in sites] == ["UNRESOLVABLE"]


def test_global_options_are_skipped() -> None:
    sites = _scan_tree(
        ast.parse('subprocess.run(["git", "-C", p, "rev-parse", "HEAD"])'),
        "synthetic.py",
    )
    assert [(site.subcommand, site.classification) for site in sites] == [
        ("rev-parse", "PSEUDO_REF")
    ]


def test_path_separator_stops_revision_scan() -> None:
    sites = _scan_tree(
        ast.parse('subprocess.run(["git", "diff", "--", "file.py"])'), "synthetic.py"
    )
    assert sites == ()


def test_missing_expected_site_is_caught() -> None:
    observed = (("synthetic.py", "<module>", 1, "diff", "BARE"),)
    with pytest.raises(AssertionError):
        _assert_inventory_matches(observed, ())
    with pytest.raises(AssertionError):
        _assert_inventory_matches(observed, (*observed, (*observed[0][:-1], "UNRESOLVABLE")))
