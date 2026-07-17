"""Source-parity coverage for audit-friction's bounded JSONL command battery."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.medium

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SKILL_PATH = (
    PROJECT_ROOT / "src" / "autoskillit" / "skills_extended" / "audit-friction" / "SKILL.md"
)
GUARD_PATH = PROJECT_ROOT / "src" / "autoskillit" / "hooks" / "guards" / "output_budget_guard.py"


def _extract_battery_commands() -> tuple[str, ...]:
    source = SKILL_PATH.read_text(encoding="utf-8")
    section = source.split("Use this keyword battery against each file:", 1)[1]
    bash_block = section.split("```bash", 1)[1].split("```", 1)[0]
    return tuple(
        line.strip()
        for line in bash_block.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    )


BATTERY_COMMANDS = _extract_battery_commands()


def test_real_command_battery_is_structurally_byte_bounded() -> None:
    assert len(BATTERY_COMMANDS) == 8
    for command in BATTERY_COMMANDS:
        assert not re.search(r"(^|\s)grep(?:\s|$)", command)
        assert command.endswith("head -c 12000")

        stages = command.split(" | ")
        assert stages[-1] == "head -c 12000"
        assert all(stage.endswith("2>&1") for stage in stages[:-1])

        jsonl_reader = stages[0]
        assert jsonl_reader.startswith("rg ")
        assert re.search(r"(?:^|\s)-M\s+500(?:\s|$)", jsonl_reader)


@pytest.mark.parametrize("command", BATTERY_COMMANDS)
def test_real_command_battery_passes_output_budget_guard(
    command: str,
    tmp_path: Path,
) -> None:
    log_path = tmp_path / "session.jsonl"
    log_path.write_text(json.dumps({"payload": "x" * 6000}) + "\n", encoding="utf-8")
    rendered = (
        command.replace("FILE", str(log_path))
        .replace("TOOL_NAME", "Read")
        .replace("CONFIRMING_PATTERN", "payload")
    )
    payload = {
        "tool_name": "Bash",
        "tool_input": {"command": rendered},
        "cwd": str(tmp_path),
    }
    isolated_home = tmp_path / "home"
    isolated_home.mkdir()
    env = {key: value for key, value in os.environ.items() if not key.startswith("AUTOSKILLIT_")}
    env.update(
        {
            "AUTOSKILLIT_CWD": str(tmp_path),
            "HOME": str(isolated_home),
            "XDG_CONFIG_HOME": str(isolated_home / ".config"),
        }
    )

    result = subprocess.run(  # noqa: S603
        [sys.executable, str(GUARD_PATH)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env=env,
        timeout=5,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip(), (
        f"guard produced no classification output for: {rendered}\nstderr: {result.stderr}"
    )
    hook_output = json.loads(result.stdout)["hookSpecificOutput"]
    assert hook_output.get("permissionDecision") != "deny", (
        f"real audit-friction command was denied: {rendered}\n{result.stdout}"
    )
