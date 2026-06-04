"""Headless session helper utilities and resolution functions."""

from __future__ import annotations

import dataclasses
import os
from pathlib import Path
from typing import TYPE_CHECKING

from autoskillit.core import (
    VARIADIC_CLAUDE_FLAGS,
    ClaudeFlags,
    CmdSpec,
    CodingAgentBackend,
    ModelIdentity,
    SkillResult,
    claude_code_project_dir,
    get_logger,
)
from autoskillit.execution.backends.codex import CodexFlags
from autoskillit.execution.headless._headless_git import _compute_loc_changed

_CLAUDE_VALUE_BEARING_FLAGS: frozenset[str] = frozenset(
    {
        ClaudeFlags.PRINT,
        ClaudeFlags.MODEL,
        ClaudeFlags.OUTPUT_FORMAT,
        ClaudeFlags.RESUME,
        ClaudeFlags.APPEND_SYSTEM_PROMPT,
        ClaudeFlags.PLUGIN_DIR,
        ClaudeFlags.ADD_DIR,
        ClaudeFlags.TOOLS,
    }
)

_CODEX_VALUE_BEARING_FLAGS: frozenset[str] = frozenset(
    {
        CodexFlags.MODEL,
        CodexFlags.MODEL_SHORT,
        CodexFlags.ADD_DIR,
        CodexFlags.SANDBOX,
        CodexFlags.CONFIG_OVERRIDE,
    }
)

_ALL_VALUE_BEARING_FLAGS: frozenset[str] = _CLAUDE_VALUE_BEARING_FLAGS | _CODEX_VALUE_BEARING_FLAGS

if TYPE_CHECKING:
    from autoskillit.config import AutomationConfig

logger = get_logger(__name__)


def _session_log_dir(cwd: str) -> Path:
    log_dir = claude_code_project_dir(cwd)
    logger.info("session_log_dir_computed", path=str(log_dir), cwd=cwd)
    if not log_dir.exists():
        logger.info("session_log_dir_precreating", path=str(log_dir), cwd=cwd)
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            logger.warning("session_log_dir_mkdir_failed", path=str(log_dir), cwd=cwd)
            raise
    return log_dir


def _resolve_pty_mode(backend: CodingAgentBackend) -> bool:
    return backend.capabilities.pty_required


def assert_headless_cmd(spec: CmdSpec) -> None:
    binary = Path(spec.cmd[0]).name if spec.cmd else ""
    if binary != "claude":
        return
    if "-p" not in spec.cmd and "--print" not in spec.cmd:
        raise ValueError(
            f"CmdSpec for claude is missing -p flag — would enter TUI mode. cmd={spec.cmd!r}"
        )


def assert_interactive_ordering(
    spec: CmdSpec,
    *,
    variadic_flags: frozenset[str] = VARIADIC_CLAUDE_FLAGS,
    value_bearing_flags: frozenset[str] | None = None,
) -> None:
    """Validate that positional arguments precede variadic flags in an interactive CmdSpec.

    Always scans the raw cmd tuple — never trusts origin metadata alone, since
    CmdOrigin is a public dataclass and callers could set it on a misordered cmd.
    """
    if value_bearing_flags is None:
        value_bearing_flags = _ALL_VALUE_BEARING_FLAGS
    cmd = spec.cmd
    positional_indices = [
        i
        for i, tok in enumerate(cmd)
        if not tok.startswith("-") and i > 0 and cmd[i - 1] not in value_bearing_flags
    ]
    for flag in variadic_flags:
        if flag in cmd:
            flag_idx = cmd.index(flag)
            for pi in positional_indices:
                if pi > flag_idx:
                    raise ValueError(
                        f"positional arg at index {pi} ({cmd[pi]!r}) must precede "
                        f"variadic flag {str(flag)!r} at index {flag_idx}"
                    )


def _resolve_session_log_dir(cwd: str, backend: CodingAgentBackend) -> Path | None:
    if not backend.capabilities.channel_b_capable:
        return None
    return _session_log_dir(cwd)


def _resolve_model(
    step_model: str,
    config: AutomationConfig,
    *,
    step_name: str = "",
    recipe_name: str = "",
) -> str | None:
    if config.model.model_override:
        logger.debug("model_resolved", tier="override", model=config.model.model_override)
        return config.model.model_override
    if recipe_name and step_name:
        recipe_model = config.model.recipe_overrides.get(recipe_name, {}).get(step_name)
        if recipe_model:
            logger.debug("model_resolved", tier="recipe_override", model=recipe_model)
            return recipe_model
    if step_name:
        step_override = config.model.step_overrides.get(step_name)
        if step_override:
            logger.debug("model_resolved", tier="step_override", model=step_override)
            return step_override
    if step_model:
        logger.debug("model_resolved", tier="step", model=step_model)
        return step_model
    if config.model.default_model:
        logger.debug("model_resolved", tier="default", model=config.model.default_model)
        return config.model.default_model
    logger.debug("model_resolved", tier="none", model=None)
    return None


def resolve_model_identity(
    step_model: str,
    config: AutomationConfig,
    *,
    step_name: str = "",
    recipe_name: str = "",
    profile_name: str = "",
) -> ModelIdentity:
    """Wrap _resolve_model() with provider awareness.

    For non-Anthropic providers, effective_model is left empty so the downstream
    argmax fallback in flush_session_log fires and extracts the real provider model
    from model_breakdown.
    """
    resolved = _resolve_model(step_model, config, step_name=step_name, recipe_name=recipe_name)
    configured = resolved or ""
    if profile_name and profile_name != "anthropic":
        return ModelIdentity.for_provider(
            configured=configured, effective="", profile=profile_name
        )
    if configured:
        return ModelIdentity.anthropic(configured)
    return ModelIdentity.unknown()


def _derive_step_name_from_skill_command(skill_command: str) -> str:
    stripped = skill_command.strip()
    if not stripped:
        return ""
    token = stripped.split()[0].lstrip("/")
    if ":" in token:
        token = token.rsplit(":", 1)[-1]
    return token


def _stat_snapshot(directory: Path) -> dict[str, tuple[int, int]]:
    result: dict[str, tuple[int, int]] = {}
    for dp, _, fns in os.walk(directory):
        for f in fns:
            rel = str(Path(dp).relative_to(directory) / f)
            full = os.path.join(dp, f)
            try:
                st = os.stat(full)
                result[rel] = (st.st_mtime_ns, st.st_size)
            except OSError:
                logger.debug("stat_snapshot_skipped", path=full)
    return result


@dataclasses.dataclass(frozen=True, slots=True)
class PostSessionMetrics:
    loc_insertions: int
    loc_deletions: int
    effective_cwd: str


def _compute_post_session_metrics(
    cwd: str,
    pre_session_sha: str,
    skill_result: SkillResult,
) -> PostSessionMetrics:
    effective_cwd = skill_result.worktree_path or cwd
    loc_ins, loc_del = _compute_loc_changed(effective_cwd, pre_session_sha)
    return PostSessionMetrics(
        loc_insertions=loc_ins,
        loc_deletions=loc_del,
        effective_cwd=effective_cwd,
    )
