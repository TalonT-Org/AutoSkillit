"""Invariant registry — prose prohibitions mapped to runtime gates.

Zero autoskillit imports — except sibling _type_constants_env for backend name constants.
"""

from __future__ import annotations

import re as _re
from dataclasses import dataclass
from typing import Final, Literal

from ._type_constants_env import (
    AGENT_BACKEND_CLAUDE_CODE,
    AGENT_BACKEND_CODEX,
    KNOWN_BACKEND_NAMES,
)

__all__ = [
    "InvariantDef",
    "INVARIANT_REGISTRY",
]


@dataclass(frozen=True, slots=True)
class InvariantDef:
    """Definition of a prose prohibition with its runtime enforcement binding."""

    id: str
    prohibition: str
    source_doc: str
    gate_target: str
    enforcement_layer: Literal["server-side", "sandbox-ci", "hook-deny", "advisory"]
    backends: frozenset[str]


_BOTH = frozenset({AGENT_BACKEND_CLAUDE_CODE, AGENT_BACKEND_CODEX})
_CLAUDE_ONLY = frozenset({AGENT_BACKEND_CLAUDE_CODE})

INVARIANT_REGISTRY: Final[dict[str, InvariantDef]] = {
    "run-in-background": InvariantDef(
        id="run-in-background",
        prohibition="run_in_background=true is prohibited in skill sessions",
        source_doc="docs/decisions/0001-prohibit-background-subagent-execution.md",
        gate_target="guards/background_exec_guard.py",
        enforcement_layer="hook-deny",
        backends=_BOTH,
    ),
    "git-amend": InvariantDef(
        id="git-amend",
        prohibition="git commit --amend is prohibited in headless sessions",
        source_doc="AGENTS.md",
        gate_target="guards/git_ops_guard.py",
        enforcement_layer="hook-deny",
        backends=_BOTH,
    ),
    "git-force-push": InvariantDef(
        id="git-force-push",
        prohibition="git push --force is prohibited in headless sessions",
        source_doc="AGENTS.md",
        gate_target="guards/git_ops_guard.py",
        enforcement_layer="hook-deny",
        backends=_BOTH,
    ),
    "git-reset-hard": InvariantDef(
        id="git-reset-hard",
        prohibition="git reset --hard is prohibited in headless sessions",
        source_doc="AGENTS.md",
        gate_target="guards/git_ops_guard.py",
        enforcement_layer="hook-deny",
        backends=_BOTH,
    ),
    "git-clean-f": InvariantDef(
        id="git-clean-f",
        prohibition="git clean -f is prohibited in headless sessions",
        source_doc="AGENTS.md",
        gate_target="guards/git_ops_guard.py",
        enforcement_layer="hook-deny",
        backends=_BOTH,
    ),
    "recipe-read-headless": InvariantDef(
        id="recipe-read-headless",
        prohibition="Must not read recipe/skill/agent files directly in headless sessions",
        source_doc="SKILL.md",
        gate_target="guards/recipe_read_guard.py",
        enforcement_layer="hook-deny",
        backends=_BOTH,
    ),
    "write-path-prefix": InvariantDef(
        id="write-path-prefix",
        prohibition="Writes outside allowed prefix are blocked in write-scoped sessions",
        source_doc="SKILL.md",
        gate_target="guards/write_guard.py",
        enforcement_layer="hook-deny",
        backends=_CLAUDE_ONLY,
    ),
    "skill-orchestration-from-L1": InvariantDef(
        id="skill-orchestration-from-L1",
        prohibition="Orchestration tools cannot be called from L1 skill sessions",
        source_doc="SKILL.md",
        gate_target="guards/skill_orchestration_guard.py",
        enforcement_layer="hook-deny",
        backends=_BOTH,
    ),
    "interpreter-write-bypass": InvariantDef(
        id="interpreter-write-bypass",
        prohibition="Interpreter-mediated writes outside allowed prefix are blocked",
        source_doc="SKILL.md",
        gate_target="guards/write_guard.py",
        enforcement_layer="hook-deny",
        backends=_CLAUDE_ONLY,
    ),
    "inline-script-in-cmd": InvariantDef(
        id="inline-script-in-cmd",
        prohibition="Inline shell scripts in recipe cmd: fields are banned",
        source_doc="docs/decisions/0002-ban-inline-shell-scripts-from-cmd.md",
        gate_target="recipe/rules/rules_inline_script.py",
        enforcement_layer="server-side",
        backends=_BOTH,
    ),
    "env-key-in-with-args": InvariantDef(
        id="env-key-in-with-args",
        prohibition="env: keys in recipe step with_args are banned",
        source_doc="docs/decisions/0003-skill-args.md",
        gate_target="recipe/rules/rules_recipe.py",
        enforcement_layer="server-side",
        backends=_BOTH,
    ),
    "generated-file-write": InvariantDef(
        id="generated-file-write",
        prohibition=(
            "Writes to generated files (hooks.json, settings.json, contracts/) are blocked"
        ),
        source_doc="AGENTS.md",
        gate_target="guards/generated_file_write_guard.py",
        enforcement_layer="hook-deny",
        backends=_BOTH,
    ),
    "bre-grep-pattern": InvariantDef(
        id="bre-grep-pattern",
        prohibition="BRE alternation syntax (backslash-pipe) is blocked in Grep patterns",
        source_doc="AGENTS.md",
        gate_target="guards/grep_pattern_lint_guard.py",
        enforcement_layer="hook-deny",
        backends=_CLAUDE_ONLY,
    ),
}

_BAD_KEY_SHAPE = [
    k for k in INVARIANT_REGISTRY if not _re.match(r"^[a-z][a-z0-9]*(-[a-zA-Z0-9]+)*$", k)
]
if _BAD_KEY_SHAPE:
    raise AssertionError(
        f"INVARIANT_REGISTRY keys must be kebab-case (L1 exception allowed): {_BAD_KEY_SHAPE}"
    )
del _BAD_KEY_SHAPE
del _re

_KEY_ID_MISMATCHES = [k for k, v in INVARIANT_REGISTRY.items() if k != v.id]
if _KEY_ID_MISMATCHES:
    raise AssertionError(f"INVARIANT_REGISTRY key != InvariantDef.id: {_KEY_ID_MISMATCHES}")
del _KEY_ID_MISMATCHES

_EMPTY_BACKENDS = [k for k, v in INVARIANT_REGISTRY.items() if not v.backends]
if _EMPTY_BACKENDS:
    raise AssertionError(f"INVARIANT_REGISTRY entries with empty backends: {_EMPTY_BACKENDS}")
del _EMPTY_BACKENDS

_UNKNOWN_BACKENDS = [
    k for k, v in INVARIANT_REGISTRY.items() if not v.backends.issubset(KNOWN_BACKEND_NAMES)
]
if _UNKNOWN_BACKENDS:
    raise AssertionError(f"INVARIANT_REGISTRY entries with unknown backends: {_UNKNOWN_BACKENDS}")
del _UNKNOWN_BACKENDS
