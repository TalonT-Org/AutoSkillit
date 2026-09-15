"""Headless session helper utilities and resolution functions."""

from __future__ import annotations

import dataclasses
import os
from collections.abc import Mapping, Sequence
from hashlib import sha256
from pathlib import Path
from typing import TYPE_CHECKING

from autoskillit.core import (
    VARIADIC_CLAUDE_FLAGS,
    ClaudeFlags,
    CmdSpec,
    CodingAgentBackend,
    ProviderBinding,
    SkillResult,
    get_logger,
)
from autoskillit.execution.backends.codex import CodexFlags
from autoskillit.execution.headless._headless_git import _compute_loc_changed
from autoskillit.execution.headless._headless_model import (
    resolve_model_identity,  # noqa: F401 - public helper compatibility export
    resolve_model_pin,  # noqa: F401 - public helper compatibility export
)
from autoskillit.quota_constraints import quota_scope

if TYPE_CHECKING:
    from autoskillit.config import AutomationConfig

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

logger = get_logger(__name__)


def resolve_launch_quota_identity(
    *,
    backend: CodingAgentBackend,
    binding: ProviderBinding | None,
    provider_extras: Mapping[str, str] | None,
    config: AutomationConfig,
) -> dict[str, str]:
    """Bind quota evidence to the credential actually selected for this launch."""
    provider = (
        binding.provider
        if binding is not None
        else "anthropic"
        if backend.capabilities.anthropic_provider_capable
        else backend.name
    )
    if not backend.capabilities.anthropic_provider_capable:
        return {
            "provider": provider,
            "mode": "backend-native",
            "credential_scope": f"backend-native:{provider}",
        }

    extras = provider_extras or {}
    api_key = extras.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")
    if api_key:
        return {
            "provider": provider,
            "mode": "api-key",
            "credential_scope": f"api-key:{sha256(api_key.encode()).hexdigest()}",
        }
    if provider != "anthropic":
        endpoint = binding.normalized_endpoint if binding is not None else ""
        scope = sha256(f"{provider}:{endpoint}".encode()).hexdigest()[:16]
        return {
            "provider": provider,
            "mode": "external",
            "credential_scope": f"external:{scope}",
        }

    oauth_token = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")
    if oauth_token:
        return {
            "provider": provider,
            "mode": "anthropic-oauth-env",
            "credential_scope": f"anthropic-oauth:{sha256(oauth_token.encode()).hexdigest()}",
        }
    try:
        scope = quota_scope("anthropic", Path(config.quota_guard.credentials_path).expanduser())
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        logger.warning(
            "launch_quota_scope_unavailable",
            error=str(exc),
            error_type=type(exc).__name__,
            exc_info=True,
        )
        return {"provider": provider, "mode": "anthropic-oauth", "credential_scope": ""}
    return {"provider": provider, "mode": "anthropic-oauth", "credential_scope": scope}


def _session_log_dir(cwd: str, backend: CodingAgentBackend) -> Path:
    log_dir = backend.session_locator().project_log_dir(cwd)
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


def assert_interactive_ordering(
    spec: CmdSpec,
    *,
    variadic_flags: frozenset[str] = VARIADIC_CLAUDE_FLAGS,
    value_bearing_flags: frozenset[str] | None = None,
) -> None:
    """Validate that positional arguments precede variadic flags in an interactive CmdSpec.

    Always scans the raw cmd tuple — never trusts origin metadata alone, since
    CmdOrigin is a public dataclass and callers could set it on a misordered cmd.

    Shape only, and backend-agnostic: environment content is never inspected
    here. The interactive content-policy check
    (``_interactive_invocation_environment_policy``, gated on
    ``spec.force_inactive_agent_teams``) lives solely behind the backend's own
    ``validate_interactive_invocation``, which can read the spec's declared
    intent — see #4684 Fix D (single-enforcement-point). Calling it here too
    would double-gate the same policy from two call sites.
    See tests/execution/test_assert_interactive_ordering.py.
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
    return _session_log_dir(cwd, backend)


def _derive_step_name_from_skill_command(skill_command: str) -> str:
    stripped = skill_command.strip()
    if not stripped:
        return ""
    token = stripped.split()[0].lstrip("/$")
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


def _detect_fs_writes(
    watch_dirs: Sequence[Path],
    before: dict[Path, dict[str, tuple[int, int]] | None],
) -> bool:
    for watch_dir in watch_dirs:
        if not watch_dir.is_dir():
            continue
        try:
            after = _stat_snapshot(watch_dir)
        except OSError:
            logger.warning("watch_dir_post_scan_failed", watch_dir=str(watch_dir), exc_info=True)
            continue
        previous = before.get(watch_dir)
        if previous is not None and previous != after:
            return True
    return False


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
