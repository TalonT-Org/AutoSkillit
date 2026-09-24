"""Realistic-project corpus for interactive-corridor tests.

Tests that drive cook/order against an empty ``tmp_path`` prove only that the
corridor works on a machine nobody has. Real developer checkouts carry
``.claude/settings.json`` and ``.claude/settings.local.json`` with unrelated
keys, and often with agent teams enabled. Every fail-closed pre-spawn check
that shipped broken passed its own unit tests and then fired on exactly that
legitimate state.

This module builds those projects, and the fake agent binary needed to launch
against them without a real CLI on the host.
"""

from __future__ import annotations

import json
from pathlib import Path

from autoskillit.core import atomic_write

AGENT_TEAMS_ENV_VAR = "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS"

# Pinned version output the fake `claude --version` shim emits. Floors must match
# `CLAUDE_CODE_CAPABILITIES.min_version` in src/autoskillit/core/types/_type_backend.py.
# Bumping the product floor requires bumping this constant and any inline shim
# scripts that still embed the literal directly.
PINNED_CLAUDE_SHIM_VERSION_OUTPUT = "2.1.280 (Claude Code)"
PINNED_CLAUDE_SHIM_VERSION = "2.1.280"

_CONFIG_YAML = """agent_backend:
  backend: claude-code
workspace:
  temp_dir: .autoskillit/temp
"""

_CONFIG_YAML_FORCE_INACTIVE = """agent_backend:
  backend: claude-code
  force_inactive_agent_teams: true
workspace:
  temp_dir: .autoskillit/temp
"""


def make_realistic_project(
    tmp_path: Path,
    *,
    agent_teams: str | None = None,
    extra_settings_keys: bool = True,
    malformed_local: bool = False,
    force_inactive_agent_teams: bool = False,
    project_dir: Path | None = None,
) -> Path:
    """Build a project directory resembling a real developer checkout.

    ``agent_teams`` is the value written for ``CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS``
    in ``.claude/settings.local.json``; ``None`` omits the key entirely.
    ``malformed_local`` writes invalid JSON there instead, which is the
    fail-closed trigger for the force-inactive policy. Passing neither
    ``agent_teams`` nor ``malformed_local`` and setting ``extra_settings_keys``
    False produces a project with no ``.claude/`` directory at all.

    ``project_dir`` writes into an existing directory instead of creating
    ``tmp_path/"project"``, for tests that already own a project layout.
    """
    project = project_dir if project_dir is not None else tmp_path / "project"
    project.mkdir(parents=True, exist_ok=True)

    atomic_write(
        project / ".autoskillit" / "config.yaml",
        _CONFIG_YAML_FORCE_INACTIVE if force_inactive_agent_teams else _CONFIG_YAML,
    )

    if extra_settings_keys:
        atomic_write(
            project / ".claude" / "settings.json",
            json.dumps(
                {
                    "permissions": {
                        "allow": ["Bash(git status:*)", "Read(//home/**)"],
                        "deny": ["Bash(rm -rf:*)"],
                    },
                    "env": {"EDITOR": "vi", "PAGER": "cat"},
                    "includeCoAuthoredBy": False,
                },
                indent=2,
            )
            + "\n",
        )

    if malformed_local:
        atomic_write(project / ".claude" / "settings.local.json", "{not valid json")
    elif agent_teams is not None:
        atomic_write(
            project / ".claude" / "settings.local.json",
            json.dumps(
                {
                    "env": {
                        AGENT_TEAMS_ENV_VAR: agent_teams,
                        "AUTOSKILLIT_UNRELATED": "1",
                    },
                    "statusLine": {"type": "command", "command": "echo hi"},
                },
                indent=2,
            )
            + "\n",
        )

    return project


def write_fake_agent_binary(shim_dir: Path, name: str = "claude") -> Path:
    """Write a launchable stand-in for the real agent CLI.

    Answers ``--version`` with a realistic string so ``ensure_pre_launch``'s
    version probe succeeds, and otherwise records its argv to
    ``$AUTOSKILLIT_STATE_DIR`` before exiting 0. Tests assert on that marker
    file to prove the corridor actually reached spawn rather than merely
    building a spec.
    """
    shim_dir.mkdir(parents=True, exist_ok=True)
    shim = shim_dir / name
    atomic_write(
        shim,
        (
            "#!/bin/sh\n"
            "set -e\n"
            'if [ "${1-}" = "--version" ]; then\n'
            f"  printf '%s\\n' '{PINNED_CLAUDE_SHIM_VERSION_OUTPUT}'\n"
            "  exit 0\n"
            "fi\n"
            'marker="$AUTOSKILLIT_STATE_DIR/__AGENT_NAME__-launch-argv.txt"\n'
            'temporary="${marker}.tmp.$$"\n'
            'printf \'%s\\n\' "$@" > "$temporary"\n'
            'mv "$temporary" "$marker"\n'
            "exit 0\n"
        ).replace("__AGENT_NAME__", name),
    )
    shim.chmod(0o755)
    return shim


def launch_marker_path(state_dir: Path, name: str = "claude") -> Path:
    """Path the fake binary writes its argv to once it actually executes."""
    return state_dir / f"{name}-launch-argv.txt"
