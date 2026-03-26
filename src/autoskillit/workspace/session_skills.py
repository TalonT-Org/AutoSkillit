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
from typing import TYPE_CHECKING

from autoskillit.core import (
    ClaudeDirectoryConventions,
    SkillSource,
    ValidatedAddDir,
    atomic_write,
    get_logger,
)
from autoskillit.workspace.skills import SkillInfo, SkillResolver, detect_project_local_overrides

if TYPE_CHECKING:
    from autoskillit.config.settings import AutomationConfig

# Candidate ephemeral roots, tried in order.
# resolve_ephemeral_root() appends tempfile.gettempdir() as the final fallback.
_CANDIDATE_ROOTS: list[Path] = [
    Path("/dev/shm"),
    Path("/tmp"),
]

_FM_PATTERN = re.compile(r"^---\n(.*?)\n?---\n?(.*)", re.DOTALL)

_SKILLS_SUBDIR = ClaudeDirectoryConventions.ADD_DIR_SKILLS_SUBDIR

logger = get_logger(__name__)


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
    if not fm_text.strip():
        return body
    return f"---\n{fm_text}\n---\n{body}"


def _is_skill_disabled(
    skill_info: SkillInfo,
    disabled: list[str],
    custom_tags: dict[str, list[str]],
) -> bool:
    """Return True if skill should be excluded due to a disabled subset.

    For each tag in disabled:
    - If the tag is a custom_tag key: check if skill.name is in custom_tags[tag]
    - Otherwise (built-in category): check if tag is in skill_info.categories
    """
    for tag in disabled:
        if tag in custom_tags:
            if skill_info.name in custom_tags[tag]:
                return True
        elif tag in skill_info.categories:
            return True
    return False


def _should_inject_skill(
    skill_info: SkillInfo,
    *,
    cook_session: bool,
    overrides: frozenset[str],
    disabled_subsets: list[str],
    custom_tags: dict[str, list[str]],
) -> bool:
    """Return True if this skill should be written to the ephemeral session dir.

    Three-stage decision model:
    1. Channel deduplication (unconditional) — BUNDLED skills are already served
       by --plugin-dir; project-local overrides are already visible via CWD
       auto-discovery.  These gates run regardless of session mode.
    2. Cook bypass — cook_session=True skips subset filtering so the cook sees
       the full extended menu.
    3. Subset filtering — disabled categories and custom tags.
    """
    # Channel deduplication — unconditional, regardless of session mode.
    # BUNDLED skills are already registered via --plugin-dir (Channel 1).
    if skill_info.source == SkillSource.BUNDLED:
        return False
    # Project-local overrides are already visible via CWD auto-discovery (Channel 3).
    if skill_info.name in overrides:
        return False
    # Subset filtering — cook_session bypasses this (the cook sees the full menu).
    if cook_session:
        return True
    if _is_skill_disabled(skill_info, disabled_subsets, custom_tags):
        return False
    return True


class SkillsDirectoryProvider:
    """Provides bundled skill content with tier-aware frontmatter injection."""

    def __init__(self) -> None:
        self._resolver = SkillResolver()

    @property
    def resolver(self) -> SkillResolver:
        """Expose the underlying SkillResolver for target skill resolution."""
        return self._resolver

    def list_skills(self) -> list[SkillInfo]:
        """List all public bundled skills."""
        return self._resolver.list_all()

    def get_skill_content(self, name: str, *, gated: bool = True) -> str:
        """Return SKILL.md content with gating frontmatter injected when required.

        - gated=True  → ensure disable-model-invocation: true is present
        - gated=False → return unmodified content (cook session or Tier 1)
        """
        skill_info = self._resolver.resolve(name)
        if skill_info is None:
            raise FileNotFoundError(f"Skill not found: {name}")
        content = skill_info.path.read_text()
        if gated:
            content = _inject_disable_model_invocation(content)
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

    def init_session(
        self,
        session_id: str,
        *,
        cook_session: bool = False,
        config: AutomationConfig | None = None,
        project_dir: Path | None = None,
    ) -> ValidatedAddDir:
        """Create ephemeral skill dir for session_id.

        Returns path to the created skills directory.
        For non-cook sessions, Tier 2 skills (from config.skills.tier2) get
        disable-model-invocation injected. Unknown skill names in config are
        logged as warnings and ignored.
        """
        if (
            not session_id
            or "\x00" in session_id
            or "/" in session_id
            or "\\" in session_id
            or session_id in (".", "..")
        ):
            raise ValueError(f"Invalid session_id: {session_id!r}")

        if config is None:
            tier2_skills: frozenset[str] = frozenset()
        else:
            all_known = {s.name for s in self._provider.list_skills()}
            configured = (
                set(config.skills.tier1) | set(config.skills.tier2) | set(config.skills.tier3)
            )
            unknown = configured - all_known
            if unknown:
                logger.warning("Unknown skill names in tier config (ignored): %s", sorted(unknown))
            tier2_skills = frozenset(config.skills.tier2)

        # Extract subset disable info from config (empty by default)
        if config is None:
            disabled_subsets: list[str] = []
            custom_tags: dict[str, list[str]] = {}
        else:
            disabled_subsets = list(config.subsets.disabled)
            custom_tags = dict(config.subsets.custom_tags)

        # Compute project-local overrides (REQ-OVR-001..004)
        overrides: frozenset[str] = (
            detect_project_local_overrides(project_dir) if project_dir is not None else frozenset()
        )
        _log = logger

        session_skills_dir = self._root / session_id
        skills_base = session_skills_dir / _SKILLS_SUBDIR
        skills_base.mkdir(parents=True, exist_ok=True)
        for skill_info in self._provider.list_skills():
            if not _should_inject_skill(
                skill_info,
                cook_session=cook_session,
                overrides=overrides,
                disabled_subsets=disabled_subsets,
                custom_tags=custom_tags,
            ):
                if skill_info.source == SkillSource.BUNDLED:
                    _log.debug("init_session_plugin_dir_skip", skill=skill_info.name)
                elif skill_info.name in overrides:
                    _log.debug("init_session_override_skip", skill=skill_info.name)
                elif _is_skill_disabled(skill_info, disabled_subsets, custom_tags):
                    _log.debug("init_session_subset_skip", skill=skill_info.name)
                continue
            skill_dir = skills_base / skill_info.name
            skill_dir.mkdir(exist_ok=True)
            gated = (not cook_session) and (skill_info.name in tier2_skills)
            content = self._provider.get_skill_content(skill_info.name, gated=gated)
            atomic_write(skill_dir / "SKILL.md", content)
        return ValidatedAddDir(path=str(session_skills_dir))

    def activate_tier2(self, session_id: str, skill_name: str) -> bool:
        """Remove disable-model-invocation from the ephemeral copy of skill_name.

        Returns True if the file was found and updated, False otherwise.
        """
        if (
            not session_id
            or "\x00" in session_id
            or "/" in session_id
            or "\\" in session_id
            or session_id in (".", "..")
        ):
            raise ValueError(f"Invalid session_id: {session_id!r}")
        if not skill_name or "/" in skill_name or "\\" in skill_name or skill_name in (".", ".."):
            raise ValueError(f"Invalid skill_name: {skill_name!r}")
        skill_md = self._root / session_id / _SKILLS_SUBDIR / skill_name / "SKILL.md"
        if not skill_md.exists():
            return False
        content = skill_md.read_text()
        updated = _remove_disable_model_invocation(content)
        atomic_write(skill_md, updated)
        return True

    def cleanup_stale(self, max_age_seconds: int = 259200) -> int:
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
