"""Context-aware pattern detection distinguishes documentation from usage."""

from __future__ import annotations

import re

import pytest

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

_FM_SPLIT = re.compile(r"^---\n(.*?)\n?---\n?(.*)", re.DOTALL)


def _strip_frontmatter(content: str) -> str:
    m = _FM_SPLIT.match(content)
    return m.group(2) if m else content


def _is_self_referential_only(body: str, skill_name: str) -> bool:
    for line in body.splitlines():
        stripped = line.strip()
        if "autoskillit:" not in stripped:
            continue
        if stripped.startswith("#") or stripped.startswith("|"):
            continue
        if f"autoskillit:{skill_name}" not in stripped:
            return False
    return True


def test_self_referential_autoskillit_not_classified_as_dependency():
    from autoskillit.core import paths
    from autoskillit.workspace.skills import _read_skill_frontmatter

    pkg = paths.pkg_root()
    false_positives: list[str] = []

    for skill_dir in (pkg / "skills", pkg / "skills_extended"):
        if not skill_dir.is_dir():
            continue
        for entry in sorted(skill_dir.iterdir()):
            if not entry.is_dir():
                continue
            skill_md = entry / "SKILL.md"
            if not skill_md.is_file():
                continue
            content = skill_md.read_text(encoding="utf-8")
            body = _strip_frontmatter(content)
            if "autoskillit:" not in body:
                continue
            if not _is_self_referential_only(body, entry.name):
                continue
            has_other_patterns = any(
                pat in body
                for pat in (
                    "Agent(model=",
                    "Agent(subagent_type=",
                    "open_kitchen",
                    "close_kitchen",
                    "run_skill",
                    "test_check",
                )
            )
            if has_other_patterns:
                continue
            fm = _read_skill_frontmatter(skill_md)
            if "claude-code" in fm.get("backend_requirements", []):
                false_positives.append(entry.name)

    assert not false_positives, (
        f"{len(false_positives)} skill(s) annotated as claude-code due to self-referential "
        f"autoskillit: only:\n" + "\n".join(f"  {s}" for s in false_positives)
    )


def test_documentation_mentions_distinguished_from_usage():
    from autoskillit.core import paths
    from autoskillit.workspace.skills import _read_skill_frontmatter

    pkg = paths.pkg_root()
    false_positives: list[str] = []

    for skill_dir in (pkg / "skills", pkg / "skills_extended"):
        if not skill_dir.is_dir():
            continue
        for entry in sorted(skill_dir.iterdir()):
            if not entry.is_dir():
                continue
            skill_md = entry / "SKILL.md"
            if not skill_md.is_file():
                continue
            content = skill_md.read_text(encoding="utf-8")
            body = _strip_frontmatter(content)
            mentions_agent_in_docs = False
            prescribes_agent_call = False
            for line in body.splitlines():
                stripped = line.strip()
                if "Agent(model=" in stripped or "Agent(subagent_type=" in stripped:
                    if stripped.startswith("|") or stripped.startswith("-"):
                        mentions_agent_in_docs = True
                    else:
                        prescribes_agent_call = True

            if mentions_agent_in_docs and not prescribes_agent_call:
                fm = _read_skill_frontmatter(skill_md)
                if "claude-code" in fm.get("backend_requirements", []):
                    false_positives.append(entry.name)

    assert not false_positives, (
        f"{len(false_positives)} skill(s) annotated as claude-code due to Agent( mentions "
        f"in documentation only:\n" + "\n".join(f"  {s}" for s in false_positives)
    )
