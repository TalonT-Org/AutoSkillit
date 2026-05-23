"""Shared constants and helpers for contract tests — avoids cross-file duplication."""

from __future__ import annotations

from autoskillit.workspace.skills import bundled_skills_dir, bundled_skills_extended_dir


def _all_skill_mds() -> list[tuple[str, str]]:
    result = []
    for skills_dir in (bundled_skills_dir(), bundled_skills_extended_dir()):
        result.extend(
            (d.name, (d / "SKILL.md").read_text())
            for d in sorted(skills_dir.iterdir())
            if d.is_dir() and (d / "SKILL.md").is_file()
        )
    return result


REFUSAL_SIGNALS = [
    "disable-model-invocation",
    "cannot be used",
    "refused",
    "Skill tool returns",
    "Skill tool fails",
]

ACTION_SIGNALS = [
    "do not",
    "do NOT",
    "discard",
    "skip",
    "omit",
    "proceed without",
]
