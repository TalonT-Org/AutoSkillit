"""Canonical hook registry — single source of truth for all hook definitions.

Both hooks.json (plugin manifest) and _hooks.py (settings.json registration)
derive from this registry.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, NamedTuple

from autoskillit.core import pkg_root


@dataclass(frozen=True)
class HookDef:
    """A single hook group: event type, matcher pattern, and ordered script list."""

    matcher: str = ""
    event_type: Literal["PreToolUse", "PostToolUse", "SessionStart"] = "PreToolUse"
    scripts: list[str] = field(default_factory=list)
    timeout_seconds: int | None = None
    session_scope: Literal["any", "headless_only", "interactive_only"] = "any"

    def __post_init__(self) -> None:
        if self.event_type != "SessionStart" and not self.matcher:
            raise ValueError(
                f"HookDef with event_type={self.event_type!r} requires a non-empty matcher"
            )


HOOK_REGISTRY: list[HookDef] = [
    HookDef(
        matcher="mcp__.*autoskillit.*__run_skill.*",
        scripts=["skill_cmd_guard.py", "quota_guard.py", "skill_command_guard.py"],
    ),
    HookDef(
        matcher="mcp__.*autoskillit.*__remove_clone",
        scripts=["remove_clone_guard.py"],
    ),
    HookDef(
        matcher=r"mcp__.*autoskillit.*__open_kitchen.*",
        scripts=["open_kitchen_guard.py"],
        timeout_seconds=5,
    ),
    HookDef(
        matcher="AskUserQuestion",
        scripts=["ask_user_question_guard.py"],
        timeout_seconds=5,
        session_scope="headless_only",
    ),
    HookDef(
        matcher=r"mcp__.*autoskillit.*__merge_worktree",
        scripts=["branch_protection_guard.py"],
    ),
    HookDef(
        matcher=r"mcp__.*autoskillit.*__push_to_remote",
        scripts=["branch_protection_guard.py"],
    ),
    HookDef(
        matcher=r"mcp__.*autoskillit.*__run_cmd",
        scripts=["unsafe_install_guard.py"],
    ),
    HookDef(
        matcher=r"Write|Edit",
        scripts=["generated_file_write_guard.py"],
    ),
    HookDef(
        matcher=r"mcp__.*autoskillit.*__(run_skill|run_cmd|run_python).*",
        scripts=["leaf_orchestration_guard.py"],
        session_scope="headless_only",
    ),
    HookDef(
        matcher=r"(mcp__.*autoskillit.*__)?dispatch_food_truck",
        scripts=["franchise_dispatch_guard.py"],
    ),
    HookDef(
        event_type="PostToolUse",
        matcher="mcp__.*autoskillit.*",
        scripts=["pretty_output_hook.py"],
    ),
    HookDef(
        event_type="PostToolUse",
        matcher=r"mcp__.*autoskillit.*__run_skill.*",
        scripts=["token_summary_hook.py", "quota_post_hook.py"],
    ),
    HookDef(
        event_type="SessionStart",
        scripts=["session_start_hook.py"],
    ),
]

HOOKS_DIR: Path = pkg_root() / "hooks"

RETIRED_SCRIPT_BASENAMES: frozenset[str] = frozenset(
    {
        "quota_check.py",
        "skill_cmd_check.py",
        "quota_post_check.py",
        "pretty_output.py",
        "token_summary_appender.py",
        "session_start_reminder.py",
        "headless_orchestration_guard.py",
        # Append any future retired basenames here, atomically with the rename commit.
    }
)


def _canonical_registry_payload(
    registry: list[HookDef],
    retired: frozenset[str],
) -> str:
    registry_rows = sorted(
        [
            {
                "event_type": h.event_type,
                "matcher": h.matcher,
                "scripts": list(h.scripts),
                "session_scope": h.session_scope,
                "timeout_seconds": h.timeout_seconds,
            }
            for h in registry
        ],
        key=lambda row: (row["event_type"], row["matcher"], tuple(row["scripts"])),  # type: ignore[arg-type]
    )
    return json.dumps(
        {"registry": registry_rows, "retired": sorted(retired)},
        sort_keys=True,
        separators=(",", ":"),
    )


def compute_registry_hash(registry: list[HookDef], retired: frozenset[str]) -> str:
    """Compute a stable sha256 over HOOK_REGISTRY + RETIRED_SCRIPT_BASENAMES."""
    payload = _canonical_registry_payload(registry, retired)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


HOOK_REGISTRY_HASH: str = compute_registry_hash(HOOK_REGISTRY, RETIRED_SCRIPT_BASENAMES)


def load_hooks_json_hash(path: Path) -> str | None:
    """Read the _autoskillit_registry_hash from a hooks.json file."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        val = data.get("_autoskillit_registry_hash")
        return str(val) if val else None
    except (OSError, json.JSONDecodeError, AttributeError):
        return None


def _build_hook_entry(hook_def: HookDef, hook_commands: list[dict]) -> dict:
    """Build the per-entry dict for a hook definition.

    SessionStart entries omit the 'matcher' key; all others include it.
    This is the single authoritative formatter for both hooks.json and
    settings.json generation.
    """
    if hook_def.event_type == "SessionStart":
        return {"hooks": hook_commands}
    return {"matcher": hook_def.matcher, "hooks": hook_commands}


def generate_hooks_json() -> dict:
    """Generate the hooks.json structure from HOOK_REGISTRY using absolute paths."""
    by_event: dict[str, list] = {}
    for hook_def in HOOK_REGISTRY:
        hook_commands = [
            {
                "type": "command",
                "command": f"python3 {HOOKS_DIR / script}",
                **(
                    {"timeout": hook_def.timeout_seconds}
                    if hook_def.timeout_seconds is not None
                    else {}
                ),
            }
            for script in hook_def.scripts
        ]
        by_event.setdefault(hook_def.event_type, []).append(
            _build_hook_entry(hook_def, hook_commands)
        )
    return {"hooks": by_event, "_autoskillit_registry_hash": HOOK_REGISTRY_HASH}


# ---------------------------------------------------------------------------
# Hook diagnostic utilities — shared between cli/ and server/ (both L3).
# Placed here (package root, L0-accessible) to avoid L3-to-L3 peer imports.
# ---------------------------------------------------------------------------


def _claude_settings_path(scope: str) -> Path:
    """Return the Claude Code settings.json path for the given scope."""
    if scope == "user":
        return Path.home() / ".claude" / "settings.json"
    return Path.cwd() / ".claude" / "settings.json"


def iter_all_scope_paths(
    project_root: Path | None = None,
) -> Iterator[tuple[str, Path]]:
    """Yield (scope_label, settings_path) for all Claude Code settings scopes.

    Always yields the user scope. Project and local scopes are yielded only
    when project_root is provided AND the corresponding .claude/ directory exists.
    """
    yield ("user", Path.home() / ".claude" / "settings.json")
    if project_root is not None:
        claude_dir = project_root / ".claude"
        if claude_dir.is_dir():
            yield ("project", claude_dir / "settings.json")
            local_path = claude_dir / "settings.local.json"
            if local_path.exists():
                yield ("local", local_path)


def _load_settings_data(settings_path: Path) -> dict:
    """Read and parse settings.json; return empty dict on any error."""
    if settings_path.exists():
        try:
            return json.loads(settings_path.read_text())
        except (OSError, json.JSONDecodeError):
            pass
    return {}


def canonical_script_basenames() -> frozenset[str]:
    """Return the set of all known autoskillit hook script basenames."""
    return frozenset(s for h in HOOK_REGISTRY for s in h.scripts)


def _is_own_hook(command: str) -> bool:
    """Check if a hook command belongs to autoskillit (any format)."""
    if "autoskillit" in command:
        return True
    known = canonical_script_basenames() | RETIRED_SCRIPT_BASENAMES
    return any(command.endswith(script) or f"/{script}" in command for script in known)


def _extract_script_basenames(hooks_dict: dict) -> set[str]:
    """Extract autoskillit hook script basenames from a hooks dict.

    Filters to autoskillit-owned commands only, then normalizes
    to bare script filenames for installation-path-agnostic comparison.
    """
    return {
        Path(cmd.split()[-1]).name
        for event_entries in hooks_dict.values()
        if isinstance(event_entries, list)
        for entry in event_entries
        for hook in entry.get("hooks", [])
        if (cmd := hook.get("command", "")) and _is_own_hook(cmd)
    }


class HookDriftResult(NamedTuple):
    """Bidirectional hook drift counts."""

    missing: int  # canonical − deployed (hooks not yet deployed)
    orphaned: int  # deployed − canonical (ghost hooks, fatal ENOENT risk)
    orphaned_cmds: frozenset[str] = frozenset()


def _count_hook_registry_drift(settings_path: Path) -> HookDriftResult:
    """Return bidirectional hook drift counts between canonical and deployed settings.json."""
    deployed_data = _load_settings_data(settings_path)
    canonical_basenames = canonical_script_basenames()
    deployed_basenames = _extract_script_basenames(deployed_data.get("hooks", {}))
    orphaned = deployed_basenames - canonical_basenames
    return HookDriftResult(
        missing=len(canonical_basenames - deployed_basenames),
        orphaned=len(orphaned),
        orphaned_cmds=frozenset(orphaned),
    )


def find_broken_hook_scripts(settings_path: Path) -> list[str]:
    """Return list of hook commands whose script files do not exist on disk."""
    data = _load_settings_data(settings_path)
    broken: list[str] = []
    for event_type in ("PreToolUse", "PostToolUse", "SessionStart"):
        for entry in data.get("hooks", {}).get(event_type, []):
            for hook in entry.get("hooks", []):
                cmd = hook.get("command", "")
                if not _is_own_hook(cmd):  # skip non-autoskillit hooks
                    continue
                parts = cmd.split()
                if len(parts) >= 2:
                    if not Path(parts[-1]).is_file():
                        broken.append(cmd)
    return broken
