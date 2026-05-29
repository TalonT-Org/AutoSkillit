"""Per-session ephemeral skill directory management.

Provides three components:
  - resolve_ephemeral_root(): platform-aware writable dir discovery
  - SkillsDirectoryProvider: tier-aware skill content provider
  - DefaultSessionSkillManager: manages per-session ephemeral skill directories
"""

from __future__ import annotations

import shutil
import tempfile
import time
from pathlib import Path
from typing import TYPE_CHECKING

import regex as re

from autoskillit.core import (
    FEATURE_REGISTRY,
    PACK_REGISTRY,
    ClaudeDirectoryConventions,
    PackDef,
    SkillResolver,
    SkillSource,
    ValidatedAddDir,
    atomic_write,
    default_log_dir,
    get_logger,
    is_feature_enabled,
    pkg_root,
)
from autoskillit.workspace.skill_format import (
    parse_frontmatter_content,
    validate_skill_frontmatter,
)
from autoskillit.workspace.skills import (
    DefaultSkillResolver,
    SkillInfo,
    detect_project_local_overrides,
)

if TYPE_CHECKING:
    from autoskillit.config import AutomationConfig
    from autoskillit.core import CodingAgentBackend

# Candidate ephemeral roots, tried in order.
# resolve_ephemeral_root() appends tempfile.gettempdir() as the final fallback.
_CANDIDATE_ROOTS: list[Path] = [
    Path("/dev/shm"),
    Path("/tmp"),
]

_FM_PATTERN = re.compile(r"^---\n(.*?)\n?---\n?(.*)", re.DOTALL)

_SKILLS_SUBDIR = ClaudeDirectoryConventions.ADD_DIR_SKILLS_SUBDIR
CODEX_SKILLS_SUBDIR = ClaudeDirectoryConventions.PLUGIN_DIR_SKILLS_SUBDIR

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


_ORDER_UP_LINE = re.compile(r"^.*%%ORDER_UP%%.*\n?", re.MULTILINE)


def _strip_marker_from_body(content: str, marker: str = "%%ORDER_UP%%") -> str:
    """Remove completion marker lines from SKILL.md body content."""
    m = _FM_PATTERN.match(content)
    if not m:
        return _ORDER_UP_LINE.sub("", content) if marker in content else content
    fm_text = m.group(1)
    body = m.group(2)
    if marker not in body:
        return content
    body = _ORDER_UP_LINE.sub("", body)
    return f"---\n{fm_text}\n---\n{body}"


_ACTIVATE_DEPS_PATTERN = re.compile(r"^activate_deps:\s*\[([^\]]*)\]", re.MULTILINE)

_SKILL_NS_REF_RE = re.compile(r"/autoskillit:([a-z][a-z0-9-]*)")


def _rewrite_skill_namespace_refs(content: str, resolver: SkillResolver) -> str:
    """Rewrite /autoskillit:<name> → /<name> for BUNDLED_EXTENDED targets.

    BUNDLED_EXTENDED skills are delivered via --add-dir as bare /name; the
    /autoskillit: prefix only works for BUNDLED skills delivered via --plugin-dir.
    """

    def _replace(m: re.Match) -> str:  # type: ignore[type-arg]
        name = m.group(1)
        info = resolver.resolve(name)
        if info is not None and info.source == SkillSource.BUNDLED_EXTENDED:
            return f"/{name}"
        return m.group(0)

    return _SKILL_NS_REF_RE.sub(_replace, content)


def _parse_activate_deps(content: str) -> list[str]:
    """Extract activate_deps list from SKILL.md frontmatter.

    Parses ``activate_deps: [item1, item2]`` from YAML frontmatter.
    Each item is either a PACK_REGISTRY key (pack dependency) or a
    bare skill name (individual dependency).
    """
    m = _FM_PATTERN.match(content)
    if not m:
        return []
    fm_text = m.group(1)
    match = _ACTIVATE_DEPS_PATTERN.search(fm_text)
    if not match:
        return []
    items = match.group(1).strip()
    if not items:
        return []
    return [item.strip() for item in items.split(",") if item.strip()]


def _is_skill_disabled(
    skill_info: SkillInfo,
    disabled: list[str],
    custom_tags: dict[str, list[str]],
    features: dict[str, bool],
    *,
    experimental_enabled: bool = False,
    allow_only: frozenset[str] | None = None,
) -> bool:
    """Return True if skill should be excluded due to a disabled subset.

    For each tag in disabled:
    - If the tag is a custom_tag key: check if skill.name is in custom_tags[tag]
    - Otherwise (built-in category): check if tag is in skill_info.categories

    Feature-gate branch: for each feature in FEATURE_REGISTRY that is disabled
    in `features`, suppress any skill whose categories intersect the feature's
    skill_categories. An empty `features` dict uses each feature's default_enabled.

    Policy layering: when `allow_only` is set and `skill_info.name` is in it, both
    the FEATURE_REGISTRY branch and any feature-gate-derived tool tags in the
    `disabled` list (injected via disabled_feature_tags) are bypassed. Explicit
    `disabled` entries from user config and packs are never bypassed.
    """
    _feature_tool_tags: frozenset[str] = (
        frozenset(
            tag
            for feat_name, feat_def in FEATURE_REGISTRY.items()
            for tag in feat_def.tool_tags
            if not is_feature_enabled(
                feat_name, features, experimental_enabled=experimental_enabled
            )
        )
        if allow_only is not None and skill_info.name in allow_only
        else frozenset()
    )
    for tag in disabled:
        if tag in custom_tags:
            if skill_info.name in custom_tags[tag]:
                return True
        elif tag in skill_info.categories:
            if tag in _feature_tool_tags:
                logger.info(
                    "feature_gate_bypassed_by_allow_only",
                    skill=skill_info.name,
                    category=tag,
                )
                continue
            return True

    # Union model: suppress a category only when ALL features that gate it are disabled.
    _in_allow_only: bool = allow_only is not None and skill_info.name in allow_only
    enabled_cats: set[str] = set()
    disabled_cats: set[str] = set()
    for feat_name, feat_def in FEATURE_REGISTRY.items():
        if is_feature_enabled(feat_name, features, experimental_enabled=experimental_enabled):
            enabled_cats |= feat_def.skill_categories
        else:
            disabled_cats |= feat_def.skill_categories
    for cat in disabled_cats - enabled_cats:
        if cat in skill_info.categories:
            if _in_allow_only:
                logger.info(
                    "feature_gate_bypassed_by_allow_only",
                    skill=skill_info.name,
                    category=cat,
                )
                continue
            return True

    return False


def _resolve_effective_disabled(
    explicit_disabled: list[str],
    pack_registry: dict[str, PackDef],
    packs_enabled: list[str],
    recipe_packs: frozenset[str] | None,
    disabled_feature_tags: frozenset[str] | None = None,
) -> frozenset[str]:
    """Compute the merged effective disabled set from all visibility sources.

    Formula:
      effective = (explicit_disabled ∪ default_disabled_packs ∪ disabled_feature_tags)
                − (packs_enabled ∪ recipe_packs)

    Precedence: explicit_disabled and disabled_feature_tags always stay.
    Default-disabled packs CAN be overridden by packs_enabled/recipe_packs.
    """
    default_disabled = frozenset(
        tag for tag, pack_def in pack_registry.items() if not pack_def.default_enabled
    )
    enabled = frozenset(packs_enabled) | (recipe_packs or frozenset())
    # Default-disabled packs that are not explicitly enabled
    default_disabled_effective = default_disabled - enabled
    return (
        frozenset(explicit_disabled)
        | default_disabled_effective
        | (disabled_feature_tags or frozenset())
    )


def _should_inject_skill(
    skill_info: SkillInfo,
    *,
    overrides: frozenset[str],
    effective_disabled: frozenset[str],
    effective_custom_tags: dict[str, list[str]],
    features: dict[str, bool],
    experimental_enabled: bool = False,
    allow_only: frozenset[str] | None = None,
) -> bool:
    """Return True if this skill should be written to the ephemeral session dir.

    Two-stage decision:
    1. Channel deduplication (unconditional): BUNDLED skills served via --plugin-dir;
       project-local overrides visible via CWD auto-discovery.
    2. Effective disable filtering (already accounts for cook session, packs, recipe).
    """
    # Channel deduplication — unconditional
    if skill_info.source == SkillSource.BUNDLED:
        return False
    if skill_info.name in overrides:
        return False
    # Apply effective filtering
    if _is_skill_disabled(
        skill_info,
        list(effective_disabled),
        effective_custom_tags,
        features,
        experimental_enabled=experimental_enabled,
        allow_only=allow_only,
    ):
        return False
    return True


def _build_pack_index(provider: SkillsDirectoryProvider) -> dict[str, set[str]]:
    """Build a pack-name → set of member skill names index from the provider."""
    index: dict[str, set[str]] = {}
    for skill in provider.list_skills():
        for cat in skill.categories:
            index.setdefault(cat, set()).add(skill.name)
    return index


def compute_skill_closure(
    skill_name: str,
    provider: SkillsDirectoryProvider,
) -> frozenset[str]:
    """Return the transitive activate_deps closure for a skill, including the skill itself.

    Returns ``frozenset()`` if ``skill_name`` does not resolve to a real skill.
    Pack-name dependencies are expanded to all pack members. Unknown deps are silently dropped.
    """
    if provider.resolver.resolve(skill_name) is None:
        return frozenset()
    pack_index: dict[str, set[str]] | None = None
    visited: set[str] = set()
    resolved: set[str] = set()
    queue: list[str] = [skill_name]
    while queue:
        name = queue.pop()
        if name in visited:
            continue
        visited.add(name)
        info = provider.resolver.resolve(name)
        if info is None:
            continue
        try:
            content = info.path.read_text()
        except OSError:
            continue
        resolved.add(name)
        for dep in _parse_activate_deps(content):
            if dep in PACK_REGISTRY:
                if pack_index is None:
                    pack_index = _build_pack_index(provider)
                for member in pack_index.get(dep, ()):
                    if member not in visited:
                        queue.append(member)
            elif dep not in visited:
                queue.append(dep)
    return frozenset(resolved)


class SkillsDirectoryProvider:
    """Provides bundled skill content with tier-aware frontmatter injection."""

    def __init__(
        self,
        temp_dir_relpath: str = ".autoskillit/temp",
        default_base_branch: str = "main",
    ) -> None:
        if "\n" in temp_dir_relpath or ": " in temp_dir_relpath:
            raise ValueError(f"temp_dir_relpath is YAML-unsafe: {temp_dir_relpath!r}")
        if "\n" in default_base_branch or ": " in default_base_branch:
            raise ValueError(f"default_base_branch is YAML-unsafe: {default_base_branch!r}")
        self._resolver = DefaultSkillResolver()
        self._temp_dir_relpath = temp_dir_relpath
        self._default_base_branch = default_base_branch

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
          (used only by the activate path — init_session omits gated skills entirely)
        - gated=False → return unmodified content (cook session or Tier 1 skills)

        Substitutes ``{{AUTOSKILLIT_TEMP}}`` with the configured temp dir relpath.
        Tier 1 skills (which contain no placeholder) are unaffected.
        """
        skill_info = self._resolver.resolve(name)
        if skill_info is None:
            raise FileNotFoundError(f"Skill not found: {name}")
        content = skill_info.path.read_text()
        if gated:
            content = _inject_disable_model_invocation(content)
        content = content.replace("{{AUTOSKILLIT_TEMP}}", self._temp_dir_relpath)
        content = content.replace(
            "{{AUTOSKILLIT_SCRIPTS}}",
            str(pkg_root() / "recipes" / "scripts"),
        )
        return content.replace("{{DEFAULT_BASE_BRANCH}}", self._default_base_branch)


class DefaultSessionSkillManager:
    """Manages per-session ephemeral skill directories."""

    def __init__(
        self,
        provider: SkillsDirectoryProvider,
        ephemeral_root: Path,
    ) -> None:
        self._provider = provider
        self._root = ephemeral_root
        self._skills_subdir = _SKILLS_SUBDIR

    def compute_skill_closure(self, skill_name: str) -> frozenset[str]:
        """Return the transitive activate_deps closure for ``skill_name``.

        See :func:`compute_skill_closure` for semantics.
        """
        return compute_skill_closure(skill_name, self._provider)

    def init_session(
        self,
        session_id: str,
        *,
        cook_session: bool = False,
        config: AutomationConfig | None = None,
        project_dir: Path | None = None,
        recipe_packs: frozenset[str] | None = None,
        recipe_features: frozenset[str] | None = None,
        allow_only: frozenset[str] | None = None,
        backend: CodingAgentBackend | None = None,
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

        # Extract subset info based on session mode
        if cook_session:
            explicit_disabled: list[str] = []
            effective_custom_tags: dict[str, list[str]] = {}
        elif config is None:
            explicit_disabled = []
            effective_custom_tags = {}
        else:
            explicit_disabled = list(config.subsets.disabled)
            effective_custom_tags = dict(config.subsets.custom_tags)

        packs_enabled: list[str] = [] if config is None else list(config.packs.enabled)
        from autoskillit.core import FeatureLifecycle

        session_features: dict[str, bool] = (
            {
                name: True
                for name, defn in FEATURE_REGISTRY.items()
                if defn.lifecycle != FeatureLifecycle.DISABLED
            }
            if cook_session
            else (config.features if config is not None else {})
        )

        # Merge recipe-level feature requirements: features declared by the active recipe
        # are injected into session_features when not already set by the user config.
        # This allows recipes to enable feature-gated skill categories without requiring
        # the user to configure each feature explicitly.
        if recipe_features and not cook_session:
            for feat_name in recipe_features:
                if feat_name in FEATURE_REGISTRY and feat_name not in session_features:
                    session_features = {**session_features, feat_name: True}

        disabled_feature_tags: frozenset[str] = frozenset()
        if config is not None and not cook_session:
            enabled_tool_tags: set[str] = set()
            disabled_tool_tags: set[str] = set()
            for feature_name, feature_def in FEATURE_REGISTRY.items():
                if is_feature_enabled(
                    feature_name,
                    session_features,
                    experimental_enabled=config.experimental_enabled,
                ):
                    enabled_tool_tags |= feature_def.tool_tags
                else:
                    disabled_tool_tags |= feature_def.tool_tags
            disabled_feature_tags = frozenset(disabled_tool_tags - enabled_tool_tags)

        effective_disabled = _resolve_effective_disabled(
            explicit_disabled=explicit_disabled,
            pack_registry=PACK_REGISTRY,
            packs_enabled=packs_enabled,
            recipe_packs=recipe_packs,
            disabled_feature_tags=disabled_feature_tags,
        )

        # Compute project-local overrides (REQ-OVR-001..004)
        overrides: frozenset[str] = (
            detect_project_local_overrides(project_dir) if project_dir is not None else frozenset()
        )
        self._skills_subdir = (
            Path(backend.capabilities.skills_subdir)
            if backend is not None and backend.capabilities.skills_subdir
            else _SKILLS_SUBDIR
        )

        session_skills_dir = self._root / session_id
        skills_base = session_skills_dir / self._skills_subdir
        skills_base.mkdir(parents=True, exist_ok=True)

        if backend is not None and backend.capabilities.required_session_files:
            codex_home_source = Path.home() / ".codex"
            if "config.toml" in backend.capabilities.required_session_files:
                try:
                    shutil.copy2(
                        codex_home_source / "config.toml",
                        session_skills_dir / "config.toml",
                    )
                    logger.debug("codex_config_copy", src=str(codex_home_source / "config.toml"))
                except FileNotFoundError:
                    logger.error(
                        "codex_config_copy_missing",
                        src=str(codex_home_source / "config.toml"),
                    )
                    raise
            if "auth.json" in backend.capabilities.session_dir_symlinks:
                auth_source = codex_home_source / "auth.json"
                auth_dest = session_skills_dir / "auth.json"
                if auth_source.exists():
                    try:
                        auth_dest.symlink_to(auth_source.resolve())
                        logger.debug(
                            "codex_auth_symlink",
                            src=str(auth_source),
                            dest=str(auth_dest),
                        )
                    except OSError:
                        logger.warning("codex_auth_symlink_failed", src=str(auth_source))
                else:
                    logger.warning("codex_auth_copy_missing", src=str(auth_source))
            env_source = codex_home_source / ".env"
            if env_source.exists():
                shutil.copy2(env_source, session_skills_dir / ".env")
                logger.debug("codex_env_copy", src=str(env_source))
            if "sessions" in backend.capabilities.session_dir_symlinks:
                sessions_target = default_log_dir() / "codex-sessions"
                sessions_target.mkdir(parents=True, exist_ok=True)
                sessions_link = session_skills_dir / "sessions"
                try:
                    sessions_link.symlink_to(sessions_target)
                    logger.debug("codex_sessions_symlink", target=str(sessions_target))
                except OSError:
                    logger.warning("codex_sessions_symlink_failed", target=str(sessions_target))
        format_rejected: set[str] = set()
        for skill_info in self._provider.list_skills():
            if allow_only is not None and skill_info.name not in allow_only:
                logger.debug("init_session_allow_only_skip", skill=skill_info.name)
                continue
            if not _should_inject_skill(
                skill_info,
                overrides=overrides,
                effective_disabled=effective_disabled,
                effective_custom_tags=effective_custom_tags,
                features=session_features,
                experimental_enabled=(
                    config.experimental_enabled
                    if config is not None and not cook_session
                    else False
                ),
                allow_only=allow_only,
            ):
                if skill_info.source == SkillSource.BUNDLED:
                    logger.debug("init_session_plugin_dir_skip", skill=skill_info.name)
                elif skill_info.name in overrides:
                    logger.debug("init_session_override_skip", skill=skill_info.name)
                else:
                    logger.debug("init_session_subset_skip", skill=skill_info.name)
                continue
            gated = (not cook_session) and (skill_info.name in tier2_skills)
            if gated and (allow_only is None or skill_info.name not in allow_only):
                logger.debug("init_session_tier2_omit", skill=skill_info.name)
                continue
            try:
                content = self._provider.get_skill_content(skill_info.name, gated=False)
            except FileNotFoundError:
                logger.warning("init_session_skill_content_missing", skill=skill_info.name)
                continue
            fm = parse_frontmatter_content(content)
            fm_errors = validate_skill_frontmatter(fm, skill_info.name)
            if fm_errors:
                for err in fm_errors:
                    logger.warning(
                        "skill_format_validation",
                        skill=skill_info.name,
                        error=err,
                        backend=backend.name if backend is not None else "unknown",
                    )
                format_rejected.add(skill_info.name)
                continue
            content = _rewrite_skill_namespace_refs(content, self._provider.resolver)
            skill_dir = skills_base / skill_info.name
            skill_dir.mkdir(exist_ok=True)
            atomic_write(skill_dir / "SKILL.md", content)
        if allow_only is not None and allow_only:
            written = {p.name for p in skills_base.iterdir() if p.is_dir()}
            bundled_names = {
                s.name for s in self._provider.list_skills() if s.source == SkillSource.BUNDLED
            }
            gated_omitted = tier2_skills & allow_only
            achievable = allow_only - overrides - bundled_names - gated_omitted - format_rejected
            if achievable and not (written & achievable):
                raise RuntimeError(
                    f"init_session: allow_only={sorted(allow_only)!r} specified but "
                    f"zero skills were written to {skills_base}. "
                    f"This indicates a gating conflict — check feature gates, "
                    f"disabled categories, or pack visibility."
                )
        if backend is not None:
            layout_errors = backend.validate_session_layout(session_skills_dir)
            for err in layout_errors:
                logger.warning(
                    "session_layout_validation",
                    session_id=session_id,
                    error=err,
                    backend=backend.name,
                )
        return ValidatedAddDir(path=str(session_skills_dir))

    def activate_skill_deps(self, session_id: str, skill_name: str) -> bool:
        """Remove disable-model-invocation from a skill and its declared dependencies.

        Reads ``activate_deps`` from the target skill's frontmatter and transitively
        activates all dependencies:
        - Pack names (keys in PACK_REGISTRY) -> activate all session skills with that category
        - Skill names -> activate the specific named skill

        Cycle-safe: tracks already-activated skills to prevent infinite recursion.
        """
        for value, label in ((session_id, "session_id"), (skill_name, "skill_name")):
            if not value or any(c in value for c in ("/", "\\", "\x00")):
                raise ValueError(f"Invalid {label}: {value!r}")
            if value in (".", ".."):
                raise ValueError(f"Invalid {label}: {value!r}")

        activated: set[str] = set()
        return self._activate_with_deps(session_id, skill_name, activated)

    def _activate_with_deps(
        self, session_id: str, skill_name: str, activated: set[str], *, _is_root: bool = True
    ) -> bool:
        """Activate a single skill and recursively activate its dependencies."""
        if skill_name in activated:
            return False
        activated.add(skill_name)

        skill_dir = self._root / session_id / self._skills_subdir / skill_name
        skill_md = skill_dir / "SKILL.md"
        if not skill_md.exists():
            try:
                fetched = self._provider.get_skill_content(skill_name, gated=False)
            except FileNotFoundError:
                return False
            fm = parse_frontmatter_content(fetched)
            fm_errors = validate_skill_frontmatter(fm, skill_name)
            if fm_errors:
                for err in fm_errors:
                    logger.warning(
                        "skill_format_validation",
                        skill=skill_name,
                        error=err,
                        context="activate_deps",
                    )
                return False
            skill_dir.mkdir(parents=True, exist_ok=True)
            fetched = _rewrite_skill_namespace_refs(fetched, self._provider.resolver)
            atomic_write(skill_md, fetched)
            logger.debug("activate_deps_materialise", skill=skill_name)

        content = skill_md.read_text()
        new_content = _remove_disable_model_invocation(content)
        if not _is_root:
            new_content = _strip_marker_from_body(new_content)
        if new_content != content:
            atomic_write(skill_md, new_content)

        deps = _parse_activate_deps(content)
        for dep in deps:
            if dep in PACK_REGISTRY:
                self._activate_pack_deps(session_id, dep, activated)
            else:
                self._activate_with_deps(session_id, dep, activated, _is_root=False)

        return True

    def _activate_pack_deps(self, session_id: str, pack_name: str, activated: set[str]) -> None:
        """Activate all session skills whose category matches *pack_name*."""
        skills_base = self._root / session_id / self._skills_subdir
        on_disk: set[str] = set()
        if skills_base.is_dir():
            for skill_dir in sorted(skills_base.iterdir()):
                if not skill_dir.is_dir():
                    continue
                name = skill_dir.name
                on_disk.add(name)
                if name in activated:
                    continue
                info = self._provider.resolver.resolve(name)
                if info and pack_name in info.categories:
                    self._activate_with_deps(session_id, name, activated, _is_root=False)
        for info in self._provider.list_skills():
            if pack_name not in info.categories:
                continue
            if info.name in on_disk or info.name in activated:
                continue
            self._activate_with_deps(session_id, info.name, activated, _is_root=False)

    def cleanup_session(self, session_id: str) -> bool:
        """Remove the session skill directory for a completed session.

        Returns True if the directory was found and removed, False otherwise.
        """
        session_dir = self._root / session_id
        if session_dir.is_dir():
            shutil.rmtree(session_dir, ignore_errors=True)
            return True
        return False

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
