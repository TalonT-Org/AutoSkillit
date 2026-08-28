"""Guard against prose directing agents to re-read protected source files."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from autoskillit.core import pkg_root
from autoskillit.hooks._command_classification import DECLARABLE_SOURCE_PATH_PATTERNS
from autoskillit.workspace.skills import bundled_skills_dir, bundled_skills_extended_dir

pytestmark = [pytest.mark.layer("arch"), pytest.mark.medium]

_FM_PATTERN = re.compile(r"^---\n(.*?)\n?---\n?(.*)", re.DOTALL)
_DIRECT_READ_VERB = re.compile(r"\b(?:read|consult|inspect)\b", re.IGNORECASE)

# These are the only bundled skill-body uses of the literal token that do not
# direct a session to read another skill's source. Keep each exception precise:
# a new occurrence needs an explicit rationale or a declared dependency.
_ALLOWED_SKILL_MD_MENTIONS: dict[tuple[str, int], str] = {
    (
        "src/autoskillit/skills/sous-chef/SKILL.md",
        1093,
    ): "States the direct-read prohibition that this guard enforces.",
    (
        "skills_extended/write-recipe/SKILL.md",
        36,
    ): "Generic authoring guidance about the filename, not a content read.",
    (
        "skills_extended/write-recipe/SKILL.md",
        76,
    ): "Generic authoring guidance about recipe artifacts, not a content read.",
    (
        "skills_extended/write-recipe/SKILL.md",
        276,
    ): "Checks whether a project-local skill file exists, without reading it.",
    (
        "skills_extended/setup-project/SKILL.md",
        202,
    ): "Describes a project-local skill-file existence check, not a content read.",
    (
        "skills_extended/prepare-pr/SKILL.md",
        179,
    ): "Uses the filename in a generic glob description for a lens table.",
}


def _bundled_skill_paths() -> list[Path]:
    return sorted(
        [
            *bundled_skills_dir().glob("*/SKILL.md"),
            *bundled_skills_extended_dir().glob("*/SKILL.md"),
        ]
    )


def _body_lines(content: str) -> tuple[int, list[str]]:
    """Return the authored body with source-file line numbers preserved."""
    match = _FM_PATTERN.match(content)
    if match is None:
        return 1, content.splitlines()
    start_line = content[: match.start(2)].count("\n") + 1
    return start_line, match.group(2).splitlines()


def _skill_remedy(line: str) -> str:
    """Render the bounded, actionable repair for a bare SKILL.md reference."""
    name = re.search(r"([a-z][a-z0-9-]*)(?:/|\s+)SKILL\.md", line)
    if name is not None:
        return (
            f"declare activate_deps: [{name.group(1)}] if this executes that skill; "
            "otherwise extract policy/data to requires_resources: [<resource-id>]."
        )
    return (
        "declare activate_deps: [<skill-name>] for executable instructions, "
        "or requires_resources: [<resource-id>] for policy/data."
    )


def test_bundled_skill_prose_declares_source_dependencies() -> None:
    """SKILL.md bodies may not direct sessions to read protected package sources."""
    findings: list[str] = []
    seen_allowlist: set[tuple[str, int]] = set()

    for skill_md in _bundled_skill_paths():
        relative_path = skill_md.relative_to(pkg_root()).as_posix()
        first_body_line, body_lines = _body_lines(skill_md.read_text(encoding="utf-8"))
        for line_number, line in enumerate(body_lines, start=first_body_line):
            location = (relative_path, line_number)
            if "SKILL.md" in line:
                if location in _ALLOWED_SKILL_MD_MENTIONS:
                    seen_allowlist.add(location)
                else:
                    findings.append(
                        f"{relative_path}:{line_number}: {line.strip()!r}\n"
                        f"  Remedy: {_skill_remedy(line)}"
                    )

            # The literal SKILL.md sweep above catches every skill reference,
            # even prose such as "open-pr SKILL.md" without a path separator.
            # Other protected source families only count when the prose asks a
            # session to read their contents; creation/output examples are not
            # source dependencies.
            if "SKILL.md" not in line and _DIRECT_READ_VERB.search(line):
                for pattern in DECLARABLE_SOURCE_PATH_PATTERNS:
                    for match in pattern.finditer(line):
                        findings.append(
                            f"{relative_path}:{line_number}: {match.group(0)!r}\n"
                            "  Remedy: extract the needed policy/data into a registered "
                            "resource and declare requires_resources: [<resource-id>]; "
                            "do not direct-read agent definitions or recipe YAML."
                        )

    assert seen_allowlist == set(_ALLOWED_SKILL_MD_MENTIONS), (
        "Stale SKILL.md prose allowlist entries:\n"
        + "\n".join(
            f"  {path}:{line}: {_ALLOWED_SKILL_MD_MENTIONS[(path, line)]}"
            for path, line in sorted(set(_ALLOWED_SKILL_MD_MENTIONS) - seen_allowlist)
        )
    )
    assert not findings, "Undeclared protected-source prose dependencies:\n" + "\n".join(findings)
