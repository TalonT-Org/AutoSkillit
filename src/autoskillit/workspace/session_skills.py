"""Per-session ephemeral skill directory management.

Provides three components:
  - resolve_ephemeral_root(): platform-aware writable dir discovery
  - SkillsDirectoryProvider: tier-aware skill content provider
  - DefaultSessionSkillManager: manages per-session ephemeral skill directories
"""

from __future__ import annotations

import re
import shutil
import tempfile
import time
from pathlib import Path

from autoskillit.core import _atomic_write
from autoskillit.workspace.skills import SkillInfo, SkillResolver

# Tier 2 skills: human-only slash commands that agents must not invoke autonomously.
# These get disable-model-invocation: true injected into their SKILL.md unless the
# session is a cook session (AUTOSKILLIT_KITCHEN_OPEN=1).
TIER2_SKILLS: frozenset[str] = frozenset({"open-kitchen", "close-kitchen"})

# Candidate ephemeral roots, tried in order.
# resolve_ephemeral_root() appends tempfile.gettempdir() as the final fallback.
_CANDIDATE_ROOTS: list[Path] = [
    Path("/dev/shm"),
    Path("/tmp"),
]

_FM_PATTERN = re.compile(r"^---\n(.*?)\n---\n?(.*)", re.DOTALL)


def resolve_ephemeral_root() -> Path:
    """Return a writable ephemeral root directory for session skill dirs.

    Tries /dev/shm/autoskillit-sessions (Linux tmpfs), then
    /tmp/autoskillit-sessions, then tempfile.gettempdir().
    Creates the chosen directory if it does not exist.
    """
    candidates = _CANDIDATE_ROOTS + [Path(tempfile.gettempdir())]
    for base in candidates:
        target = base / "autoskillit-sessions"
        try:
            target.mkdir(parents=True, exist_ok=True)
            probe = target / ".write_probe"
            probe.touch()
            probe.unlink()
            return target
        except (OSError, PermissionError):
            continue
    raise RuntimeError("No writable ephemeral root found for session skill dirs")


def _inject_disable_model_invocation(content: str) -> str:
    """Ensure disable-model-invocation: true is present in SKILL.md frontmatter."""
    m = _FM_PATTERN.match(content)
    if m:
        fm_text = m.group(1)
        body = m.group(2)
        if re.search(r"^disable-model-invocation:", fm_text, re.MULTILINE):
            fm_text = re.sub(
                r"^(disable-model-invocation:).*$",
                r"\1 true",
                fm_text,
                flags=re.MULTILINE,
            )
        else:
            fm_text = fm_text + "\ndisable-model-invocation: true"
        return f"---\n{fm_text}\n---\n{body}"
    # No frontmatter — prepend one
    return f"---\ndisable-model-invocation: true\n---\n{content}"


def _remove_disable_model_invocation(content: str) -> str:
    """Remove disable-model-invocation from SKILL.md frontmatter if present."""
    m = _FM_PATTERN.match(content)
    if not m:
        return content
    fm_text = m.group(1)
    body = m.group(2)
    if not re.search(r"^disable-model-invocation:", fm_text, re.MULTILINE):
        return content
    fm_text = re.sub(r"\ndisable-model-invocation:.*", "", fm_text)
    fm_text = re.sub(r"^disable-model-invocation:.*\n?", "", fm_text, flags=re.MULTILINE)
    fm_text = fm_text.rstrip("\n")
    return f"---\n{fm_text}\n---\n{body}"


class SkillsDirectoryProvider:
    """Provides bundled skill content with tier-aware frontmatter injection."""

    def __init__(self) -> None:
        self._resolver = SkillResolver()

    def list_skills(self) -> list[SkillInfo]:
        """List all public bundled skills."""
        return self._resolver.list_all()

    def get_skill_content(self, name: str, *, tier2_gated: bool = True) -> str:
        """Return SKILL.md content with tier-appropriate frontmatter.

        For Tier 2 skills:
        - tier2_gated=True  → ensure disable-model-invocation: true is present
        - tier2_gated=False → remove disable-model-invocation (cook session)
        For Tier 1 skills, returns unmodified content.
        """
        skill_info = self._resolver.resolve(name)
        if skill_info is None:
            raise FileNotFoundError(f"Skill not found: {name}")
        content = skill_info.path.read_text()
        if name in TIER2_SKILLS:
            if tier2_gated:
                content = _inject_disable_model_invocation(content)
            else:
                content = _remove_disable_model_invocation(content)
        return content


class DefaultSessionSkillManager:
    """Manages per-session ephemeral skill directories."""

    def __init__(
        self,
        provider: SkillsDirectoryProvider,
        ephemeral_root: Path,
    ) -> None:
        self._provider = provider
        self._root = ephemeral_root

    def init_session(self, session_id: str, *, cook_session: bool = False) -> Path:
        """Create ephemeral skill dir for session_id.

        Returns path to the created skills directory.
        For non-cook sessions, Tier 2 skills get disable-model-invocation injected.
        """
        if not session_id or "/" in session_id or "\\" in session_id or session_id in (".", ".."):
            raise ValueError(f"Invalid session_id: {session_id!r}")
        session_skills_dir = self._root / session_id
        session_skills_dir.mkdir(parents=True, exist_ok=True)
        for skill_info in self._provider.list_skills():
            skill_dir = session_skills_dir / skill_info.name
            skill_dir.mkdir(exist_ok=True)
            tier2_gated = not cook_session
            content = self._provider.get_skill_content(skill_info.name, tier2_gated=tier2_gated)
            _atomic_write(skill_dir / "SKILL.md", content)
        return session_skills_dir

    def activate_tier2(self, session_id: str, skill_name: str) -> bool:
        """Remove disable-model-invocation from the ephemeral copy of skill_name.

        Returns True if the file was found and updated, False otherwise.
        """
        if not session_id or "/" in session_id or "\\" in session_id or session_id in (".", ".."):
            raise ValueError(f"Invalid session_id: {session_id!r}")
        if not skill_name or "/" in skill_name or "\\" in skill_name or skill_name in (".", ".."):
            raise ValueError(f"Invalid skill_name: {skill_name!r}")
        skill_md = self._root / session_id / skill_name / "SKILL.md"
        if not skill_md.exists():
            return False
        content = skill_md.read_text()
        updated = _remove_disable_model_invocation(content)
        _atomic_write(skill_md, updated)
        return True

    def cleanup_stale(self, max_age_seconds: int = 86400) -> int:
        """Remove session dirs not accessed within max_age_seconds.

        Returns count of removed directories.
        """
        now = time.time()
        removed = 0
        if not self._root.exists():
            return 0
        for entry in self._root.iterdir():
            if not entry.is_dir():
                continue
            last_access = entry.stat().st_atime
            if now - last_access > max_age_seconds:
                shutil.rmtree(entry, ignore_errors=True)
                removed += 1
        return removed
