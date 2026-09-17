"""Bind prescribed git path reads to the protected-read guard."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import pytest

from autoskillit.hooks import (
    PROTECTED_SOURCE_PATH_PATTERNS,
    command_has_blocked_protected_path_read,
)
from autoskillit.recipe.helpers._skill_placeholder_parser import extract_git_commands
from autoskillit.server.lifecycle import _guards as server_guards

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SAMPLE_PROTECTED_PATH = "src/autoskillit/recipes/implementation.yaml"

ADMITTED = "ADMITTED"
DENIED_ACKNOWLEDGED = "DENIED_ACKNOWLEDGED"
GuardVerdict = Literal["ADMITTED", "DENIED_ACKNOWLEDGED"]


@dataclass(frozen=True)
class PrescribedGuardCommand:
    source_path: str
    fragment: str
    path_placeholder: str
    verdict: GuardVerdict
    reason: str


PRESCRIBED_GUARD_COMMANDS = (
    PrescribedGuardCommand(
        "src/autoskillit/skills_extended/dry-walkthrough/SKILL.md",
        "git check-ignore -v {path}",
        "{path}",
        ADMITTED,
        "The command asks Git for ignore metadata, not file content.",
    ),
    PrescribedGuardCommand(
        "src/autoskillit/skills_extended/audit-impl/SKILL.md",
        "git show {implementation_ref}:{path}",
        "{path}",
        DENIED_ACKNOWLEDGED,
        (
            "Implementation review may read ordinary changed files, but protected sources stay "
            "denied."
        ),
    ),
    PrescribedGuardCommand(
        "src/autoskillit/skills_extended/investigate/SKILL.md",
        "git show {HASH} -- {AFFECTED_FILES}",
        "{AFFECTED_FILES}",
        DENIED_ACKNOWLEDGED,
        (
            "Investigation history is useful, but a substituted protected path must remain "
            "unreadable."
        ),
    ),
    PrescribedGuardCommand(
        "src/autoskillit/skills_extended/validate-review-decisions/SKILL.md",
        "git log --follow -5 --oneline -- {file}",
        "{file}",
        DENIED_ACKNOWLEDGED,
        "Review-decision provenance cannot become a protected-source read channel.",
    ),
    PrescribedGuardCommand(
        "src/autoskillit/skills_extended/validate-test-audit/SKILL.md",
        "git log -10 --oneline -- {file}",
        "{file}",
        DENIED_ACKNOWLEDGED,
        "Test-audit provenance cannot become a protected-source read channel.",
    ),
    PrescribedGuardCommand(
        "src/autoskillit/agents/audit-impl-slice-auditor.md",
        "git show {implementation_ref}:{path}",
        "{path}",
        DENIED_ACKNOWLEDGED,
        "Slice auditing may inspect implementation files, but not protected instruction sources.",
    ),
    PrescribedGuardCommand(
        "src/autoskillit/agents/audit-impl-deviation-evaluator.md",
        "git show {implementation_ref}:{path}",
        "{path}",
        DENIED_ACKNOWLEDGED,
        (
            "Deviation evaluation may inspect implementation files, but not protected instruction "
            "sources."
        ),
    ),
)


def _substitute_protected_path(command: PrescribedGuardCommand) -> str:
    assert command.path_placeholder in command.fragment
    return command.fragment.replace(command.path_placeholder, _SAMPLE_PROTECTED_PATH)


def test_prescribed_commands_remain_extractable_and_match_declared_verdict() -> None:
    for command in PRESCRIBED_GUARD_COMMANDS:
        source = (_REPO_ROOT / command.source_path).read_text(encoding="utf-8")
        extracted = extract_git_commands(source)
        assert isinstance(extracted, list), (
            f"extract_git_commands must return a list of strings, got {type(extracted).__name__}"
        )
        assert all(isinstance(item, str) for item in extracted), (
            "extract_git_commands must return a list of strings"
        )
        assert command.fragment in extracted, (
            f"{command.source_path} no longer prescribes {command.fragment!r}"
        )

        is_denied = command_has_blocked_protected_path_read(
            _substitute_protected_path(command), PROTECTED_SOURCE_PATH_PATTERNS
        )
        assert is_denied is (command.verdict == DENIED_ACKNOWLEDGED), (
            f"{command.source_path}: {command.fragment!r} no longer matches its "
            f"declared {command.verdict} protected-path verdict"
        )
        if command.verdict == DENIED_ACKNOWLEDGED:
            assert command.reason.strip(), (
                f"{command.source_path}: denied command needs an acknowledgement reason"
            )


def test_hook_and_server_share_the_prescribed_command_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert (
        server_guards.command_has_blocked_protected_path_read
        is command_has_blocked_protected_path_read
    )
    assert server_guards.PROTECTED_SOURCE_PATH_PATTERNS is PROTECTED_SOURCE_PATH_PATTERNS

    monkeypatch.setenv("AUTOSKILLIT_HEADLESS", "1")
    for command in PRESCRIBED_GUARD_COMMANDS:
        result = server_guards._check_recipe_read_prohibition(
            cmd=_substitute_protected_path(command)
        )
        assert (result is None) is (command.verdict == ADMITTED), (
            f"server recipe-read gate disagrees with {command.verdict} for "
            f"{command.source_path}: {command.fragment!r}"
        )
