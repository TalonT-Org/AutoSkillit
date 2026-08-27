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
from typing import Literal, NamedTuple

import regex as re

# Events that do not require a tool-name matcher pattern (Stop fires once
# per turn; SessionStart fires before any tool call).
_MATCHERLESS_EVENT_TYPES: frozenset[str] = frozenset({"SessionStart", "Stop", "PreToolUse"})


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
    ] = "PreToolUse"
    scripts: list[str] = field(default_factory=list)
    timeout_seconds: int | None = None
    session_scope: Literal["any", "headless_only", "interactive_only"] = "any"
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

    def __post_init__(self) -> None:
        if self.event_type not in _MATCHERLESS_EVENT_TYPES and not self.matcher:
            raise ValueError(
                f"HookDef with event_type={self.event_type!r} requires a non-empty matcher"
            )
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
    session_scope: Literal["any", "headless_only", "interactive_only"]
    required_owner_roles: frozenset[Literal["same_runner", "session_start"]]

    def __post_init__(self) -> None:
        if not isinstance(self.resource, str) or not self.resource:
            raise ValueError("LifecycleContractDef.resource must be non-empty")
        if not isinstance(self.producer_script, str) or not self.producer_script:
            raise ValueError("LifecycleContractDef.producer_script must be non-empty")
        if self.backend not in ("claude_code", "codex"):
            raise ValueError("LifecycleContractDef.backend is invalid")
        if self.session_scope not in ("any", "headless_only", "interactive_only"):
            raise ValueError("LifecycleContractDef.session_scope is invalid")
        if not isinstance(self.required_owner_roles, frozenset) or not (self.required_owner_roles):
            raise ValueError("LifecycleContractDef.required_owner_roles must be non-empty")
        if not self.required_owner_roles <= {"same_runner", "session_start"}:
            raise ValueError("LifecycleContractDef.required_owner_roles contains an invalid role")


class HookDriftResult(NamedTuple):
    """Bidirectional hook drift counts."""

    missing: int  # canonical − deployed (hooks not yet deployed)
    orphaned: int  # deployed − canonical (ghost hooks, fatal ENOENT risk)
    orphaned_cmds: frozenset[str] = frozenset()


_LOGICAL_HOOK_COMPONENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")
