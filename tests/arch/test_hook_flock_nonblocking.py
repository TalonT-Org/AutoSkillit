"""Every repository flock or registered lease acquisition is bounded.

The detector supports direct ``fcntl.flock``/``lockf`` calls and explicitly
registered factories. Dynamic ``getattr``, computed aliases, and other
indirection that prevents static resolution are prohibited and fail closed.
Unlocks are outside the rule because they release an already-held lock.
"""

from __future__ import annotations

import ast
import math
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date

import pytest

from tests.arch._deferred_debt import (
    TrackedDeferral,
    assert_entries_still_apply,
    assert_not_stale,
    assert_rationale_present,
)
from tests.arch._helpers import SRC_ROOT

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

_LOCK_UN = frozenset({"LOCK_UN"})
_ARTIFACT_LEASE_FACTORIES = frozenset(
    {"acquire_shared", "acquire_existing_shared", "acquire_exclusive"}
)
_TIMEOUT_FACTORY_NAMES = frozenset({"acquire_flock_with_timeout"})
_KNOWN_TIMEOUTS = {"ARTIFACT_LEASE_TIMEOUT_SECONDS": 2.0}
_DEFERRED_ACQUISITIONS: dict[tuple[str, str, str], TrackedDeferral] = {}
_EXPECTED_ACQUISITIONS = (
    (
        "cli/doctor/_doctor_runtime.py",
        "_check_session_index_projection",
        "ArtifactLease.acquire_existing_shared",
    ),
    (
        "cli/doctor/_doctor_runtime.py",
        "_check_session_index_projection",
        "ArtifactLease.acquire_existing_shared",
    ),
    (
        "cli/install/_plugin_artifact.py",
        "_acquire_shared_lease_with_retry",
        "ArtifactLease.acquire_existing_shared",
    ),
    (
        "cli/install/_plugin_artifact.py",
        "publish_installed_plugin_artifact",
        "ArtifactLease.acquire_exclusive",
    ),
    ("cli/session/_session_reload.py", "_reload_lock", "acquire_flock_with_timeout"),
    (
        "core/_install_binding.py",
        "_acquire_self_lease",
        "ArtifactLease.acquire_existing_shared",
    ),
    (
        "core/_plugin_artifact_retirement.py",
        "try_promote_legacy_evidence",
        "ArtifactLease.acquire_exclusive",
    ),
    (
        "core/_plugin_artifact_retirement.py",
        "try_reclaim",
        "ArtifactLease.acquire_exclusive",
    ),
    ("core/_retiring_cache.py", "_open_lock", "acquire_flock_with_timeout"),
    ("core/pipeline_tracker.py", "__enter__", "acquire_flock_with_timeout"),
    ("core/pipeline_tracker.py", "retain_tracker_lease", "ArtifactLease.acquire_shared"),
    (
        "core/pipeline_tracker.py",
        "try_retire_tracker",
        "ArtifactLease.acquire_exclusive",
    ),
    ("core/runtime/artifact_lease.py", "acquire_flock_with_timeout", "flock"),
    (
        "core/runtime/artifact_lease.py",
        "_acquire",
        "acquire_flock_with_timeout",
    ),
    ("core/runtime/session_registry.py", "_registry_lock", "acquire_flock_with_timeout"),
    (
        "core/runtime/worktree_gate_lease.py",
        "acquire",
        "ArtifactLease.acquire_exclusive",
    ),
    ("execution/backends/_codex_config_lock.py", "acquire", "flock"),
    (
        "execution/backends/_codex_session_lease.py",
        "acquire",
        "acquire_flock_with_timeout",
    ),
    ("execution/otlp_sink.py", "_persist_line", "ArtifactLease.acquire_exclusive"),
    (
        "execution/session/_managed_headless_session_lineage_records.py",
        "_store_lock",
        "acquire_flock_with_timeout",
    ),
    ("execution/session/_session_state.py", "acquire", "flock"),
    (
        "execution/session_log.py",
        "flush_session_log",
        "ArtifactLease.acquire_exclusive",
    ),
    ("fleet/_state_lock.py", "acquire", "acquire_flock_with_timeout"),
    ("hooks/_capture/_resolver.py", "_acquire_shared_lease", "flock"),
    ("hooks/_capture/_resolver.py", "acquire_writer_lease", "flock"),
    ("hooks/_capture_lifecycle/_admission.py", "_acquire_flock", "flock"),
    ("hooks/_capture_lifecycle/_admission.py", "_acquire_flock", "flock"),
    ("hooks/_capture_lifecycle/_store.py", "_try_artifact_lease", "flock"),
    ("hooks/_join_ledger.py", "_acquire_lock", "flock"),
    ("hooks/guards/open_kitchen_guard.py", "_acquire_registry_lock", "flock"),
    ("hooks/resume_gate_post_hook.py", "_acquire_lock", "flock"),
    ("planner/merge.py", "merge_files", "acquire_flock_with_timeout"),
    ("planner/merge.py", "replace_item", "acquire_flock_with_timeout"),
    ("server/_recipe_artifact.py", "_generation_lock", "acquire_flock_with_timeout"),
    ("server/tools/_overlay_state.py", "locked_overlay", "acquire_flock_with_timeout"),
    (
        "workspace/_install_state.py",
        "_enqueue_legacy_installed_plugin_versions",
        "ArtifactLease.acquire_shared",
    ),
    (
        "workspace/_install_state.py",
        "_generation_store_findings",
        "ArtifactLease.acquire_existing_shared",
    ),
    (
        "workspace/_installed_artifact.py",
        "_validate_supplied_lease",
        "ArtifactLease.acquire_existing_shared",
    ),
    (
        "workspace/_projected_artifact/_generation_publication.py",
        "_finalize_generation",
        "ArtifactLease.acquire_exclusive",
    ),
    (
        "workspace/_projected_artifact/_generation_publication.py",
        "_resume_generation_residue",
        "ArtifactLease.acquire_exclusive",
    ),
    (
        "workspace/_projected_artifact/_generation_publication.py",
        "_reconcile_generation_candidate",
        "ArtifactLease.acquire_exclusive",
    ),
    (
        "workspace/_projected_artifact/_hook_repair.py",
        "repair_broken_plugin_cache_hooks",
        "ArtifactLease.acquire_exclusive",
    ),
    (
        "workspace/_projected_artifact/_hook_repair.py",
        "repair_broken_projection_hooks",
        "ArtifactLease.acquire_exclusive",
    ),
    (
        "workspace/_projected_artifact/authority.py",
        "acquire_launch_binding",
        "ArtifactLease.acquire_exclusive",
    ),
    (
        "workspace/_projected_artifact/authority.py",
        "acquire_launch_binding",
        "ArtifactLease.acquire_shared",
    ),
    (
        "workspace/_projected_artifact/authority.py",
        "acquire_launch_binding",
        "ArtifactLease.acquire_shared",
    ),
    (
        "workspace/_projection_cache.py",
        "_reconcile_projection_entry",
        "ArtifactLease.acquire_exclusive",
    ),
    (
        "workspace/_projection_cache.py",
        "_resume_projection_residue",
        "ArtifactLease.acquire_exclusive",
    ),
    (
        "workspace/_shared_asset_store.py",
        "_populate_store_entry",
        "ArtifactLease.acquire_exclusive",
    ),
    (
        "workspace/_update_obligation.py",
        "clear_obligation",
        "ArtifactLease.acquire_exclusive",
    ),
    (
        "workspace/_update_obligation.py",
        "update_obligation_expected_version",
        "ArtifactLease.acquire_exclusive",
    ),
    (
        "workspace/_update_obligation.py",
        "write_obligation",
        "ArtifactLease.acquire_exclusive",
    ),
    ("workspace/clone_registry.py", "__enter__", "acquire_flock_with_timeout"),
    (
        "workspace/session_skill_lifecycle.py",
        "acquire",
        "ArtifactLease.acquire_exclusive",
    ),
)


@dataclass(frozen=True)
class _FlockSite:
    path: str
    function: str
    lineno: int
    flags: frozenset[str] | None

    @property
    def violation(self) -> str | None:
        if self.flags is None:
            return "operation expression is not statically resolvable"
        if self.flags == _LOCK_UN:
            return None
        if "LOCK_NB" not in self.flags:
            return "operation does not include fcntl.LOCK_NB"
        return None


@dataclass(frozen=True)
class _FactorySite:
    path: str
    function: str
    lineno: int
    factory: str
    timeout_present: bool
    timeout: float | None
    factory_resolved: bool = True

    @property
    def violation(self) -> str | None:
        if not self.factory_resolved:
            return "registered factory target is not statically resolvable"
        if not self.timeout_present:
            return "registered factory call omits explicit timeout="
        if self.factory in _TIMEOUT_FACTORY_NAMES:
            return None
        if self.timeout is None:
            return "timeout expression is not statically resolvable"
        if not math.isfinite(self.timeout) or self.timeout < 0.0:
            return "timeout must be finite and non-negative"
        return None


def _is_fcntl_lock_call(node: ast.Call) -> bool:
    return (
        isinstance(node.func, ast.Attribute)
        and node.func.attr in {"flock", "lockf"}
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "fcntl"
    )


def _operation_argument(node: ast.Call) -> ast.expr | None:
    if len(node.args) >= 2:
        return node.args[1]
    return next((keyword.value for keyword in node.keywords if keyword.arg == "operation"), None)


def _direct_assignments(body: Iterable[ast.stmt]) -> dict[str, ast.expr | None]:
    """Map directly assigned local names, marking duplicates unresolved."""
    assignments: dict[str, ast.expr | None] = {}
    for statement in body:
        if not isinstance(statement, (ast.Assign, ast.AnnAssign)):
            continue
        value = statement.value
        targets = statement.targets if isinstance(statement, ast.Assign) else (statement.target,)
        for target in targets:
            if not isinstance(target, ast.Name):
                continue
            assignments[target.id] = None if target.id in assignments else value
    return assignments


def _static_lock_flags(
    expression: ast.expr | None,
    assignments: dict[str, ast.expr | None],
    seen_names: frozenset[str] = frozenset(),
) -> frozenset[str] | None:
    if (
        isinstance(expression, ast.Attribute)
        and isinstance(expression.value, ast.Name)
        and expression.value.id == "fcntl"
        and expression.attr.startswith("LOCK_")
    ):
        return frozenset({expression.attr})
    if isinstance(expression, ast.BinOp) and isinstance(expression.op, ast.BitOr):
        left = _static_lock_flags(expression.left, assignments, seen_names)
        right = _static_lock_flags(expression.right, assignments, seen_names)
        if left is None and right is None:
            return None
        return (left or frozenset()) | (right or frozenset())
    if isinstance(expression, ast.Name) and expression.id not in seen_names:
        assigned = assignments.get(expression.id)
        if assigned is not None:
            return _static_lock_flags(assigned, assignments, seen_names | {expression.id})
    return None


def _static_timeout(
    expression: ast.expr | None,
    assignments: dict[str, ast.expr | None],
    seen_names: frozenset[str] = frozenset(),
) -> float | None:
    if isinstance(expression, ast.Constant) and isinstance(expression.value, (int, float)):
        return float(expression.value)
    if isinstance(expression, ast.UnaryOp) and isinstance(expression.op, ast.USub):
        value = _static_timeout(expression.operand, assignments, seen_names)
        return None if value is None else -value
    if isinstance(expression, ast.IfExp):
        body = _static_timeout(expression.body, assignments, seen_names)
        alternate = _static_timeout(expression.orelse, assignments, seen_names)
        if body is None or alternate is None:
            return None
        return max(body, alternate)
    if isinstance(expression, ast.Name):
        if expression.id in _KNOWN_TIMEOUTS:
            return _KNOWN_TIMEOUTS[expression.id]
        if expression.id not in seen_names:
            assigned = assignments.get(expression.id)
            if assigned is not None:
                return _static_timeout(assigned, assignments, seen_names | {expression.id})
    return None


def _factory_call(node: ast.Call) -> tuple[str, bool] | None:
    if isinstance(node.func, ast.Name) and node.func.id in _TIMEOUT_FACTORY_NAMES:
        return node.func.id, True
    if isinstance(node.func, ast.Attribute) and node.func.attr in _TIMEOUT_FACTORY_NAMES:
        return node.func.attr, isinstance(node.func.value, ast.Name)
    if isinstance(node.func, ast.Attribute) and node.func.attr in _ARTIFACT_LEASE_FACTORIES:
        resolved = isinstance(node.func.value, ast.Name) and node.func.value.id == "ArtifactLease"
        return f"ArtifactLease.{node.func.attr}", resolved
    if (
        isinstance(node.func, ast.Call)
        and isinstance(node.func.func, ast.Name)
        and node.func.func.id == "getattr"
        and len(node.func.args) >= 2
        and isinstance(node.func.args[1], ast.Constant)
        and node.func.args[1].value in _ARTIFACT_LEASE_FACTORIES | _TIMEOUT_FACTORY_NAMES
    ):
        return str(node.func.args[1].value), False
    return None


def _timeout_keyword(node: ast.Call) -> tuple[bool, ast.expr | None]:
    for keyword in node.keywords:
        if keyword.arg == "timeout":
            return True, keyword.value
    return False, None


class _FlockCollector(ast.NodeVisitor):
    def __init__(self, path: str) -> None:
        self._path = path
        self._functions: list[str] = []
        self._assignments: list[dict[str, ast.expr | None]] = [{}]
        self.sites: list[_FlockSite] = []
        self.factory_sites: list[_FactorySite] = []

    def visit_Module(self, node: ast.Module) -> None:
        self._assignments[-1] = _direct_assignments(node.body)
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self._functions.append(node.name)
        assignments = self._assignments[-1] | _direct_assignments(node.body)
        self._assignments.append(assignments)
        self.generic_visit(node)
        self._assignments.pop()
        self._functions.pop()

    def visit_Call(self, node: ast.Call) -> None:
        if _is_fcntl_lock_call(node):
            self.sites.append(
                _FlockSite(
                    path=self._path,
                    function=".".join(self._functions) or "<module>",
                    lineno=node.lineno,
                    flags=_static_lock_flags(_operation_argument(node), self._assignments[-1]),
                )
            )
        factory = _factory_call(node)
        if factory is not None:
            timeout_present, timeout_expression = _timeout_keyword(node)
            self.factory_sites.append(
                _FactorySite(
                    path=self._path,
                    function=".".join(self._functions) or "<module>",
                    lineno=node.lineno,
                    factory=factory[0],
                    timeout_present=timeout_present,
                    timeout=_static_timeout(timeout_expression, self._assignments[-1]),
                    factory_resolved=factory[1],
                )
            )
        self.generic_visit(node)


def _scan_tree(tree: ast.AST, path: str) -> tuple[_FlockSite | _FactorySite, ...]:
    collector = _FlockCollector(path)
    collector.visit(tree)
    return (*collector.sites, *collector.factory_sites)


def _scan_source() -> tuple[_FlockSite | _FactorySite, ...]:
    sites: list[_FlockSite | _FactorySite] = []
    for source_path in sorted(SRC_ROOT.rglob("*.py")):
        relative_path = source_path.relative_to(SRC_ROOT).as_posix()
        sites.extend(_scan_tree(ast.parse(source_path.read_text(encoding="utf-8")), relative_path))
    return tuple(sites)


def _inventory_key(site: _FlockSite | _FactorySite) -> tuple[str, str, str]:
    kind = "flock" if isinstance(site, _FlockSite) else site.factory
    return site.path, site.function, kind


def _live_acquisitions() -> tuple[_FlockSite | _FactorySite, ...]:
    return tuple(
        site
        for site in _scan_source()
        if not isinstance(site, _FlockSite) or site.flags != _LOCK_UN
    )


def test_every_repository_flock_or_registered_lease_acquisition_is_bounded() -> None:
    violations = [
        site
        for site in _live_acquisitions()
        if site.violation is not None and _inventory_key(site) not in _DEFERRED_ACQUISITIONS
    ]
    details = "\n".join(
        f"  {site.path}:{site.lineno} ({site.function}): {site.violation}" for site in violations
    )
    assert not violations, f"Flock and registered lease acquisitions must be bounded:\n{details}"


def test_repository_lock_acquisition_inventory_is_complete() -> None:
    """New acquisitions must consciously join the bounded-lock inventory."""
    observed = sorted(_inventory_key(site) for site in _live_acquisitions())
    assert observed == sorted(_EXPECTED_ACQUISITIONS)


def test_bounded_lock_deferrals_are_live_justified_and_current() -> None:
    live_violations = {
        _inventory_key(site) for site in _live_acquisitions() if site.violation is not None
    }
    assert_entries_still_apply(
        _DEFERRED_ACQUISITIONS,
        registry_name="bounded lock deferrals",
        live_keys=live_violations,
    )
    assert_rationale_present(_DEFERRED_ACQUISITIONS, registry_name="bounded lock deferrals")
    assert_not_stale(_DEFERRED_ACQUISITIONS, registry_name="bounded lock deferrals")
    assert _DEFERRED_ACQUISITIONS == {}


def test_unresolvable_flock_operation_is_caught() -> None:
    """Canary: an operation passed through an unknown name must fail closed."""
    sites = _scan_tree(
        ast.parse("def write(fd, operation):\n    fcntl.flock(fd, operation)\n"), "synthetic.py"
    )
    assert [site.violation for site in sites] == [
        "operation expression is not statically resolvable"
    ]


def test_reverting_to_bare_lock_ex_is_caught() -> None:
    """Canary: the exact bare-LOCK_EX regression remains detectable."""
    sites = _scan_tree(
        ast.parse("def write(fd):\n    fcntl.flock(fd, fcntl.LOCK_EX)\n"), "synthetic.py"
    )
    assert [site.violation for site in sites] == ["operation does not include fcntl.LOCK_NB"]


def test_nonblocking_flock_operation_is_not_flagged() -> None:
    """Canary: a statically explicit non-blocking acquisition remains valid."""
    sites = _scan_tree(
        ast.parse("def write(fd):\n    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)\n"),
        "synthetic.py",
    )
    assert [site.violation for site in sites] == [None]


@pytest.mark.parametrize("factory", sorted(_ARTIFACT_LEASE_FACTORIES))
def test_omitted_artifact_lease_timeout_is_caught(factory: str) -> None:
    sites = _scan_tree(
        ast.parse(f"def read(path):\n    ArtifactLease.{factory}(path)\n"),
        "synthetic.py",
    )
    assert [site.violation for site in sites] == [
        "registered factory call omits explicit timeout="
    ]


@pytest.mark.parametrize("timeout", ["0.0", "2.0", "LOCK_TIMEOUT_SECONDS"])
def test_finite_nonnegative_artifact_lease_timeout_is_accepted(timeout: str) -> None:
    source = "LOCK_TIMEOUT_SECONDS = 2.0\ndef read(path):\n"
    source += f"    ArtifactLease.acquire_shared(path, timeout={timeout})\n"
    sites = _scan_tree(ast.parse(source), "synthetic.py")
    assert [site.violation for site in sites] == [None]


def test_registered_flock_factory_requires_timeout() -> None:
    sites = _scan_tree(
        ast.parse(
            "def write(fd, path):\n"
            "    acquire_flock_with_timeout(fd, operation=fcntl.LOCK_EX, path=path)\n"
        ),
        "synthetic.py",
    )
    assert [site.violation for site in sites] == [
        "registered factory call omits explicit timeout="
    ]


def test_dynamic_registered_factory_dispatch_fails_closed() -> None:
    sites = _scan_tree(
        ast.parse(
            "def read(path):\n    getattr(ArtifactLease, 'acquire_shared')(path, timeout=0.0)\n"
        ),
        "synthetic.py",
    )
    assert [site.violation for site in sites] == [
        "registered factory target is not statically resolvable"
    ]


def test_backdated_lock_deferral_is_rejected() -> None:
    with pytest.raises(AssertionError, match="older than"):
        assert_not_stale(
            {
                ("synthetic.py", "write", "flock"): TrackedDeferral(
                    issue=4511,
                    rationale="Synthetic proof that stale entries are rejected.",
                    added_date=date(2000, 1, 1),
                    regression_test=(
                        "tests/arch/test_hook_flock_nonblocking.py::"
                        "test_backdated_lock_deferral_is_rejected"
                    ),
                )
            },
            registry_name="bounded lock deferrals",
        )
