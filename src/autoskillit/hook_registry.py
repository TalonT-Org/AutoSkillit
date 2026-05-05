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

from autoskillit.core import DIRECT_INSTALL_CACHE_SUBDIR, pkg_root


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
        scripts=[
            "guards/skill_cmd_guard.py",
            "guards/quota_guard.py",
            "guards/skill_command_guard.py",
        ],
    ),
    HookDef(
        matcher="mcp__.*autoskillit.*__remove_clone",
        scripts=["guards/remove_clone_guard.py"],
    ),
    HookDef(
        matcher=r"mcp__.*autoskillit.*__open_kitchen.*",
        scripts=["guards/open_kitchen_guard.py"],
        timeout_seconds=5,
    ),
    HookDef(
        matcher="AskUserQuestion",
        scripts=["guards/ask_user_question_guard.py"],
        timeout_seconds=5,
        session_scope="headless_only",
    ),
    HookDef(
        matcher=r"mcp__.*autoskillit.*__merge_worktree",
        scripts=["guards/branch_protection_guard.py"],
    ),
    HookDef(
        matcher=r"mcp__.*autoskillit.*__push_to_remote",
        scripts=["guards/branch_protection_guard.py"],
    ),
    HookDef(
        matcher=r"mcp__.*autoskillit.*__run_cmd",
        scripts=["guards/unsafe_install_guard.py", "guards/pr_create_guard.py"],
    ),
    HookDef(
        matcher=r"Bash|mcp__.*autoskillit.*__run_cmd",
        scripts=["guards/planner_gh_discovery_guard.py"],
        session_scope="headless_only",
    ),
    HookDef(
        matcher=r"Write|Edit",
        scripts=["guards/generated_file_write_guard.py"],
    ),
    HookDef(
        matcher=r"Write|Edit",
        scripts=["guards/write_guard.py"],
        session_scope="headless_only",
    ),
    HookDef(
        matcher=r"Write|Edit",
        scripts=["guards/recipe_write_advisor.py"],
        session_scope="interactive_only",
    ),
    HookDef(
        matcher=r"Grep",
        scripts=["guards/grep_pattern_lint_guard.py"],
    ),
    HookDef(
        matcher=r"Bash|Write|Edit|Read|Glob|Grep",
        scripts=["guards/mcp_health_guard.py"],
        timeout_seconds=5,
        session_scope="interactive_only",
    ),
    HookDef(
        matcher=r"mcp__.*autoskillit.*__(run_skill|run_cmd|run_python).*",
        scripts=["guards/skill_orchestration_guard.py"],
        session_scope="headless_only",
    ),
    HookDef(
        matcher=r"(mcp__.*autoskillit.*__)?dispatch_food_truck",
        scripts=["guards/fleet_dispatch_guard.py"],
    ),
    HookDef(
        event_type="PostToolUse",
        matcher="mcp__.*autoskillit.*",
        scripts=["formatters/pretty_output_hook.py"],
    ),
    HookDef(
        event_type="PostToolUse",
        matcher=r"mcp__.*autoskillit.*__run_skill.*",
        scripts=["token_summary_hook.py", "quota_post_hook.py"],
    ),
    HookDef(
        event_type="PostToolUse",
        matcher=r"mcp__.*autoskillit.*__(run_skill|run_python).*",
        scripts=["review_gate_post_hook.py"],
    ),
    HookDef(
        event_type="PostToolUse",
        matcher=r"Write|Edit",
        scripts=["lint_after_edit_hook.py"],
        session_scope="headless_only",
    ),
    HookDef(
        event_type="PostToolUse",
        matcher="Skill",
        scripts=["skill_load_post_hook.py"],
    ),
    HookDef(
        matcher=r"Read|Write|Edit|Bash|Grep|Glob",
        scripts=["guards/skill_load_guard.py"],
        session_scope="headless_only",
    ),
    HookDef(
        matcher=r"mcp__.*autoskillit.*__(wait_for_ci|enqueue_pr)",
        scripts=["guards/review_loop_gate.py"],
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
        "franchise_dispatch_guard.py",
        "ask_user_question_guard.py",
        "branch_protection_guard.py",
        "fleet_dispatch_guard.py",
        "generated_file_write_guard.py",
        "grep_pattern_lint_guard.py",
        "leaf_orchestration_guard.py",
        "mcp_health_guard.py",
        "open_kitchen_guard.py",
        "planner_gh_discovery_guard.py",
        "pr_create_guard.py",
        "quota_guard.py",
        "recipe_write_advisor.py",
        "remove_clone_guard.py",
        "review_loop_gate.py",
        "skill_cmd_guard.py",
        "skill_command_guard.py",
        "unsafe_install_guard.py",
        "write_guard.py",
        "pretty_output_hook.py",
        # Append any future retired basenames here, atomically with the rename commit.
    }
)

# Basenames of scripts added directly to a subdirectory without ever having a flat path.
# Add the basename here when introducing a new subdir script that was never previously flat,
# so test_moved_scripts_must_be_in_retired does not false-positive on it.
NEW_SUBDIR_BASENAMES: frozenset[str] = frozenset(
    {
        "skill_orchestration_guard.py",
        "skill_load_guard.py",
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
        {"format_version": 2, "registry": registry_rows, "retired": sorted(retired)},
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


def _build_hook_command(hooks_dir: Path, script: str, timeout_seconds: int | None) -> dict:
    """Build a single hook command dict using the stable dispatcher format."""
    logical_name = script.removesuffix(".py")
    cmd: dict = {
        "type": "command",
        "command": f"python3 {hooks_dir / '_dispatch.py'} {logical_name}",
    }
    if timeout_seconds is not None:
        cmd["timeout"] = timeout_seconds
    return cmd


def generate_hooks_json() -> dict:
    """Generate the hooks.json structure from HOOK_REGISTRY using the stable dispatcher.

    Multiple HookDef entries with the same (event_type, matcher) are consolidated
    into a single settings.json entry so Claude Code sees no duplicate matchers.
    """
    # Preserve insertion order; merge scripts from same (event_type, matcher) key.
    groups: dict[tuple[str, str], dict] = {}
    for hook_def in HOOK_REGISTRY:
        key = (hook_def.event_type, hook_def.matcher)
        hook_commands = [
            _build_hook_command(HOOKS_DIR, script, hook_def.timeout_seconds)
            for script in hook_def.scripts
        ]
        if key not in groups:
            groups[key] = _build_hook_entry(hook_def, hook_commands)
        else:
            groups[key]["hooks"].extend(hook_commands)

    by_event: dict[str, list] = {}
    for (event_type, _), entry in groups.items():
        by_event.setdefault(event_type, []).append(entry)
    return {"hooks": by_event, "_autoskillit_registry_hash": HOOK_REGISTRY_HASH}


# ---------------------------------------------------------------------------
# Hook diagnostic utilities — shared between cli/ and server/ (both IL-3).
# Placed here (package root, IL-0-accessible) to avoid IL-3-to-IL-3 peer imports.
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
    if "_dispatch.py" in command:
        return True
    known = canonical_script_basenames() | RETIRED_SCRIPT_BASENAMES
    bare = {Path(s).name for s in known}
    return any(command.endswith(script) or f"/{script}" in command for script in known | bare)


def _extract_script_basenames(hooks_dict: dict) -> set[str]:
    """Extract autoskillit hook script relative paths from a hooks dict.

    Filters to autoskillit-owned commands only, then normalizes
    to hooks-dir-relative paths for installation-path-agnostic comparison.
    """
    result: set[str] = set()
    for event_entries in hooks_dict.values():
        if not isinstance(event_entries, list):
            continue
        for entry in event_entries:
            for hook in entry.get("hooks", []):
                cmd = hook.get("command", "")
                if not cmd or not _is_own_hook(cmd):
                    continue
                parts = cmd.split()
                if "_dispatch.py" in cmd and len(parts) >= 3:
                    logical_name = parts[-1]
                    result.add(logical_name + ".py")
                else:
                    script_path = Path(parts[-1])
                    bare = script_path.name
                    canonical = canonical_script_basenames()
                    matched = next((c for c in canonical if Path(c).name == bare), bare)
                    result.add(matched)
    return result


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
                if not _is_own_hook(cmd):
                    continue
                parts = cmd.split()
                if len(parts) >= 3 and parts[-2].endswith("_dispatch.py"):
                    if not Path(parts[-2]).is_file():
                        broken.append(cmd)
                elif len(parts) >= 2:
                    if not Path(parts[-1]).is_file():
                        broken.append(cmd)
    return broken


def validate_plugin_cache_hooks(cache_dir: Path | None = None) -> list[str]:
    """Return list of broken hook commands from the plugin cache hooks.json.

    Reads each hooks.json found under cache_dir/*/hooks.json and checks that
    every autoskillit hook script path exists on disk.
    """
    _cache = cache_dir or (
        Path.home() / ".claude" / "plugins" / "cache" / DIRECT_INSTALL_CACHE_SUBDIR / "autoskillit"
    )
    broken: list[str] = []
    if not _cache.is_dir():
        return broken
    for hooks_json_path in _cache.glob("*/hooks.json"):
        broken.extend(find_broken_hook_scripts(hooks_json_path))
    return broken
