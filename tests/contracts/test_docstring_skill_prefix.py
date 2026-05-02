"""Contract: source files must not use /autoskillit: prefix for skills_extended skills.

The /autoskillit: namespace prefix is reserved for Tier 1 skills delivered via --plugin-dir
(open-kitchen, close-kitchen, sous-chef). Skills in skills_extended/ are registered as bare
names via --add-dir and must be referenced as /name, not /autoskillit:name.
"""

import re

from autoskillit.core import SkillSource
from autoskillit.core.paths import pkg_root
from autoskillit.workspace.skills import DefaultSkillResolver

_PKG = pkg_root()
_SCAN_DIRS_AND_GLOBS: list[tuple[str, list[str]]] = [
    ("server", ["tools_*.py", "_guards.py"]),
    ("hooks", ["*.py"]),
]
_PREFIX_RE = re.compile(r"/autoskillit:([a-z][\w-]*)")


def _collect_prefixed_skill_refs() -> list[tuple[str, int, str]]:
    """Return (relative_path, line_number, skill_name) for every /autoskillit:name reference."""
    hits: list[tuple[str, int, str]] = []
    for subdir, globs in _SCAN_DIRS_AND_GLOBS:
        scan_dir = _PKG / subdir
        for glob in globs:
            for path in sorted(scan_dir.glob(glob)):
                for i, line in enumerate(path.read_text().splitlines(), 1):
                    for m in _PREFIX_RE.finditer(line):
                        hits.append((f"{subdir}/{path.name}", i, m.group(1)))
    return hits


def test_no_skills_extended_skill_uses_autoskillit_prefix() -> None:
    resolver = DefaultSkillResolver()
    violations: list[str] = []
    for rel_path, lineno, skill_name in _collect_prefixed_skill_refs():
        info = resolver.resolve(skill_name)
        if info is not None and info.source == SkillSource.BUNDLED_EXTENDED:
            violations.append(
                f"{rel_path}:{lineno}: /autoskillit:{skill_name} "
                f"is a skills_extended skill — use /{skill_name} instead"
            )
    assert not violations, (
        "Source files use /autoskillit: prefix for skills_extended skills:\n"
        + "\n".join(f"  - {v}" for v in violations)
    )
