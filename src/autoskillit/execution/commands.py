"""Claude CLI command builders — re-exports internal prompt-building primitives."""

from __future__ import annotations

from autoskillit.core import CmdSpec
from autoskillit.execution.backends._claude_prompt import (
    _HEADLESS_EXCLUSIVE_VARS,  # noqa: F401 — re-export for downstream consumers
    _MAX_MCP_OUTPUT_TOKENS_VALUE,  # noqa: F401 — re-export for downstream consumers
    _SESSION_BASELINE_ENV,  # noqa: F401 — re-export for downstream consumers
    _apply_output_format,  # noqa: F401 — re-export for downstream consumers
    _build_resume_context,  # noqa: F401 — re-export for downstream consumers
    _compose_resume_prompt,  # noqa: F401 — re-export for downstream consumers
    _ensure_skill_prefix,  # noqa: F401 — re-export for downstream consumers
    _inject_completion_directive,  # noqa: F401 — re-export for downstream consumers
    _inject_completion_reminder,  # noqa: F401 — re-export for downstream consumers
    _inject_cwd_anchor,  # noqa: F401 — re-export for downstream consumers
    _inject_narration_suppression,  # noqa: F401 — re-export for downstream consumers
)

ClaudeHeadlessCmd = CmdSpec
