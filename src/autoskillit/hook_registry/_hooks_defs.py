"""Frozen dataclasses and namedtuples that anchor the hook-registry data model.

Single source of truth for the HookDef / LifecycleContractDef data shapes,
the matcherless-event-types constant, and the logical-hook-component regex.
``_LOGICAL_HOOK_COMPONENT`` lives here (not in ``_rendering``) because the
regex is the canonical validator for the dispatcher's ``logical_name`` shape.
It is imported as ``regex`` (not stdlib ``re``) per
``tests/arch/test_regex_import.py``'s allowlist for this package tree.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Literal, NamedTuple

import regex as re

if TYPE_CHECKING:
    # Issue #5121: route SessionScopeLiteral through the canonical authority
    # module's type alias. TYPE_CHECKING-guarded so the import does NOT trigger
    # the autoskillit.hooks package init at runtime (which would cycle back
    # through autoskillit.hook_registry and partially-load _hooks_defs).
    from autoskillit.hooks._runtime._session_scope_authority import SessionScopeLiteral

# Events that do not require a tool-name matcher pattern (Stop fires once
# per turn; SessionStart fires before any tool call).
_MATCHERLESS_EVENT_TYPES: frozenset[str] = frozenset(
    {"SessionStart", "Stop", "PreToolUse", "UserPromptExpansion"}
)


@dataclass(frozen=True, slots=True)
class HookDef:
    """A single hook group: event type, matcher pattern, and ordered script list."""

    matcher: str = ""
    event_type: Literal[
        "PreToolUse",
        "PostToolUse",
        "PostToolUseFailure",
        "SessionStart",
        "Stop",
        "SubagentStart",
        "SubagentStop",
        "SessionEnd",
        "PreCompact",
        "UserPromptExpansion",
    ] = "PreToolUse"
    scripts: list[str] = field(default_factory=list)
    timeout_seconds: int | None = None
    session_scope: SessionScopeLiteral = "any"
    exempt_skills: frozenset[str] = field(default_factory=frozenset)
    exempt_session_types: frozenset[str] = field(default_factory=frozenset)
    codex_status: Literal["works-as-is", "degraded", "fix-required", "not-applicable"] = (
        "works-as-is"
    )
    mechanism: Literal[
        "deny",
        "additionalContext",
        "output-rewrite",
        "input-rewrite",
        "side-effect",
    ] = "deny"
    enforcement_strength: dict[str, str] = field(default_factory=dict)
    produces_resources: frozenset[str] = field(default_factory=frozenset)
    reclaims_resources: frozenset[str] = field(default_factory=frozenset)
    self_reclaims_resources: frozenset[str] = field(default_factory=frozenset)
    runtime_only: bool = False

    def __post_init__(self) -> None:
        if self.event_type not in _MATCHERLESS_EVENT_TYPES and not self.matcher:
            raise ValueError(
                f"HookDef with event_type={self.event_type!r} requires a non-empty matcher"
            )
        if self.session_scope not in _load_session_scope_values():
            raise ValueError("HookDef.session_scope is invalid")
        for field_name in (
            "produces_resources",
            "reclaims_resources",
            "self_reclaims_resources",
        ):
            resources = getattr(self, field_name)
            if not isinstance(resources, frozenset) or any(
                not isinstance(resource, str) or not resource for resource in resources
            ):
                raise ValueError(f"HookDef.{field_name} must be a frozenset of non-empty strings")


class HookEnvVarDef(NamedTuple):
    """Static contract for one environment variable consumed by a hook process."""

    var: str
    provenance: Literal["autoskillit", "harness", "operator"]
    producer: str | None
    entrypoint: str | None
    justification: str


@dataclass(frozen=True, slots=True)
class LifecycleContractDef:
    """Static ownership contract for a hook-produced persistent resource."""

    resource: str
    producer_script: str
    backend: Literal["claude_code", "codex"]
    session_scope: SessionScopeLiteral
    required_owner_roles: frozenset[Literal["same_runner", "session_start"]]

    def __post_init__(self) -> None:
        if not isinstance(self.resource, str) or not self.resource:
            raise ValueError("LifecycleContractDef.resource must be non-empty")
        if not isinstance(self.producer_script, str) or not self.producer_script:
            raise ValueError("LifecycleContractDef.producer_script must be non-empty")
        if self.backend not in ("claude_code", "codex"):
            raise ValueError("LifecycleContractDef.backend is invalid")
        if self.session_scope not in _load_session_scope_values():
            raise ValueError("LifecycleContractDef.session_scope is invalid")
        if not isinstance(self.required_owner_roles, frozenset) or not (self.required_owner_roles):
            raise ValueError("LifecycleContractDef.required_owner_roles must be non-empty")
        if not self.required_owner_roles <= {"same_runner", "session_start"}:
            raise ValueError("LifecycleContractDef.required_owner_roles contains an invalid role")


@dataclass(frozen=True, slots=True)
class ProtectionWaiverDef:
    """Declared coverage for an intentional deny-guard exclusion."""

    guard_script: str
    excluded_scope: Literal["headless", "interactive", "all"]
    backend: Literal["claude_code", "codex"]
    risk: str
    covering_mechanism: str
    justification: str
    covering_guard_script: str | None = None

    def __post_init__(self) -> None:
        if not all((self.guard_script, self.risk, self.covering_mechanism, self.justification)):
            raise ValueError("protection waiver fields must be nonempty")
        if self.covering_mechanism == "hook" and not self.covering_guard_script:
            raise ValueError("hook protection waiver requires covering_guard_script")


class HookDriftResult(NamedTuple):
    """Bidirectional hook drift counts."""

    missing: int  # canonical − deployed (hooks not yet deployed)
    orphaned: int  # deployed − canonical (ghost hooks, fatal ENOENT risk)
    orphaned_cmds: frozenset[str] = frozenset()


_LOGICAL_HOOK_COMPONENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")


def _load_session_scope_values() -> frozenset[str]:
    """Return the canonical SESSION_SCOPE_VALUES from autoskillit.hooks._runtime.

    Issue #5121 (D7 module-reference pattern): the canonical constant lives in
    _session_scope_authority.py. Importing it through the normal
    ``from autoskillit.hooks._runtime import _session_scope_authority`` path
    triggers ``autoskillit.hooks/__init__.py``, which imports
    ``autoskillit.hook_registry`` — and that package is mid-load when
    ``_hooks_defs`` is being imported (it is loaded as part of
    ``autoskillit.hook_registry.__init__``).

    We avoid the cycle by loading the module via ``importlib.util`` against
    its on-disk path, which bypasses the package-init machinery. The result
    is the SAME module object as the package-init path would produce (it
    ends up cached in ``sys.modules`` under the dotted name on first
    attribute access), so monkeypatching still works in tests.

    Returns the constant ``frozenset[str]``; the call is cheap — it caches
    the resolved module on the function attribute after first invocation.
    """
    cached = getattr(_load_session_scope_values, "_cached", None)
    if cached is not None:
        return cached
    import importlib.util
    import sys as _sys

    # Resolve via direct file path to bypass the dotted-name import machinery,
    # which would otherwise trigger autoskillit.hooks/__init__.py and cycle
    # back through autoskillit.hook_registry mid-load. After the cycle
    # resolves, subsequent imports of the canonical dotted name find this
    # exact module object (registered in sys.modules below), preserving the
    # module-reference pattern that T5's monkeypatch exercises.
    module_path = (
        Path(__file__).resolve().parent.parent
        / "hooks"
        / "_runtime"
        / "_session_scope_authority.py"
    )
    direct_spec = importlib.util.spec_from_file_location(
        "autoskillit.hooks._runtime._session_scope_authority", module_path
    )
    if direct_spec is None or direct_spec.loader is None:
        # Should not happen in a properly-installed environment — the file is
        # always present. Fall back to the hardcoded value set, which matches
        # the canonical constant by value (pinned by T17).
        cached = frozenset({"any", "headless_only", "interactive_only"})
        _load_session_scope_values._cached = cached  # type: ignore[attr-defined]
        return cached
    module = importlib.util.module_from_spec(direct_spec)
    _sys.modules["autoskillit.hooks._runtime._session_scope_authority"] = module
    direct_spec.loader.exec_module(module)  # type: ignore[union-attr]
    cached = module.SESSION_SCOPE_VALUES
    _load_session_scope_values._cached = cached  # type: ignore[attr-defined]
    return cached
