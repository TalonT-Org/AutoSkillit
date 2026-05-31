"""Environment variable names, session type aliases, context markers, logging keys.

Zero autoskillit imports. Self-contained.
"""

from __future__ import annotations

from importlib.metadata import version

__all__ = [
    "AUTOSKILLIT_INSTALLED_VERSION",
    "AUTOSKILLIT_PRIVATE_ENV_VARS",
    "CODEX_CONTEXT_EXHAUSTION_MARKER",
    "CODEX_SCHEMA_VERSION",
    "CONTEXT_EXHAUSTION_MARKER",
    "RESERVED_LOG_RECORD_KEYS",
    "SESSION_TYPE_ENV_VAR",
    "SESSION_TYPE_FLEET",
    "SESSION_TYPE_ORCHESTRATOR",
    "SESSION_TYPE_SKILL",
    "HEADLESS_ENV_VAR",
    "HEADLESS_AUTO_GATE_ENV_VAR",
    "FLEET_MODE_ENV_VAR",
    "FLEET_DISPATCH_MODE",
    "CAMPAIGN_ID_ENV_VAR",
    "DISPATCH_ID_ENV_VAR",
    "KITCHEN_SESSION_ID_ENV_VAR",
    "LAUNCH_ID_ENV_VAR",
    "FOOD_TRUCK_TOOL_TAGS_ENV_VAR",
    "AGENT_BACKEND_ENV_VAR",
    "AGENT_BACKEND_CLAUDE_CODE",
    "AGENT_BACKEND_CODEX",
    "MCP_CLIENT_BACKEND_ENV_VAR",
    "FLEET_SESSION_REQUIRED_ENV",
    "SKILL_SESSION_REQUIRED_ENV",
    "ORCHESTRATOR_SESSION_REQUIRED_ENV",
    "RESUME_SESSION_BASELINE_KEYS",
    "CODEX_INTERACTIVE_REQUIRED_ENV",
    "CODEX_MCP_ENV_FORWARD_VARS",
]

AUTOSKILLIT_INSTALLED_VERSION: str = version("autoskillit")
CODEX_SCHEMA_VERSION: int = 1

# Session type environment variable and valid values.
SESSION_TYPE_ENV_VAR: str = "AUTOSKILLIT_SESSION_TYPE"
SESSION_TYPE_ORCHESTRATOR: str = "orchestrator"
SESSION_TYPE_FLEET: str = "fleet"
SESSION_TYPE_SKILL: str = "skill"
HEADLESS_ENV_VAR: str = "AUTOSKILLIT_HEADLESS"
HEADLESS_AUTO_GATE_ENV_VAR: str = "AUTOSKILLIT_HEADLESS_AUTO_GATE"
CAMPAIGN_ID_ENV_VAR: str = "AUTOSKILLIT_CAMPAIGN_ID"
FLEET_MODE_ENV_VAR: str = "AUTOSKILLIT_FLEET_MODE"
FLEET_DISPATCH_MODE: str = "dispatch"
DISPATCH_ID_ENV_VAR: str = "AUTOSKILLIT_DISPATCH_ID"
KITCHEN_SESSION_ID_ENV_VAR: str = "AUTOSKILLIT_KITCHEN_SESSION_ID"
LAUNCH_ID_ENV_VAR: str = "AUTOSKILLIT_LAUNCH_ID"
FOOD_TRUCK_TOOL_TAGS_ENV_VAR: str = "AUTOSKILLIT_FOOD_TRUCK_TOOL_TAGS"
AGENT_BACKEND_ENV_VAR: str = "AUTOSKILLIT_AGENT_BACKEND"
AGENT_BACKEND_CLAUDE_CODE: str = "claude-code"
AGENT_BACKEND_CODEX: str = "codex"
MCP_CLIENT_BACKEND_ENV_VAR: str = "AUTOSKILLIT_MCP_CLIENT_BACKEND"

# AGENT_BACKEND_ENV_VAR is intentionally absent: it is not in
# _HEADLESS_EXCLUSIVE_VARS (_claude_prompt.py) and removing it here
# allows natural propagation through both scrub stages in codex.py's
# build_skill_session_cmd (the _HEADLESS_EXCLUSIVE_VARS filter and
# CodexEnvPolicy.build_env).  Backends inject the canonical value via
# extras regardless.
AUTOSKILLIT_PRIVATE_ENV_VARS: frozenset[str] = frozenset(
    {
        "AUTOSKILLIT_HEADLESS",
        "AUTOSKILLIT_SKIP_STALE_CHECK",
        "AUTOSKILLIT_SKIP_UPDATE_CHECK",
        "AUTOSKILLIT_SKIP_SOURCE_DRIFT_CHECK",
        "AUTOSKILLIT_FORCE_UPDATE_CHECK",
        "AUTOSKILLIT_SESSION_TYPE",
        "AUTOSKILLIT_FLEET_MODE",
        "AUTOSKILLIT_CAMPAIGN_ID",
        "AUTOSKILLIT_DISPATCH_ID",
        "AUTOSKILLIT_KITCHEN_SESSION_ID",
        "AUTOSKILLIT_CAMPAIGN_STATE_PATH",
        "AUTOSKILLIT_PROJECT_DIR",
        FOOD_TRUCK_TOOL_TAGS_ENV_VAR,
        HEADLESS_AUTO_GATE_ENV_VAR,
        MCP_CLIENT_BACKEND_ENV_VAR,
        "AUTOSKILLIT_LAUNCH_ID",
        "AUTOSKILLIT_SKILL_NAME",
        "AUTOSKILLIT_PROVIDER_PROFILE",
        "SCENARIO_STEP_NAME",
        "AUTOSKILLIT_ALLOWED_WRITE_PREFIX",
        "AUTOSKILLIT_ALLOWED_WRITE_PREFIXES",
        "AUTOSKILLIT_CWD",
        "MAX_MCP_OUTPUT_TOKENS",
        "AUTOSKILLIT_SESSION_DEADLINE",
    }
)

FLEET_SESSION_REQUIRED_ENV: frozenset[str] = frozenset(
    {
        "AUTOSKILLIT_SESSION_TYPE",
        "AUTOSKILLIT_FLEET_MODE",
        "AUTOSKILLIT_PROJECT_DIR",
        "AUTOSKILLIT_HEADLESS",
    }
)

SKILL_SESSION_REQUIRED_ENV: frozenset[str] = frozenset(
    {
        "AUTOSKILLIT_HEADLESS",
        "AUTOSKILLIT_SESSION_TYPE",
        "MAX_MCP_OUTPUT_TOKENS",
        "MCP_CONNECTION_NONBLOCKING",
        AGENT_BACKEND_ENV_VAR,
        "AUTOSKILLIT_APPLICABLE_GUARDS",
    }
)

ORCHESTRATOR_SESSION_REQUIRED_ENV: frozenset[str] = frozenset(
    {
        "AUTOSKILLIT_HEADLESS",
        "AUTOSKILLIT_SESSION_TYPE",
        "MAX_MCP_OUTPUT_TOKENS",
        "MCP_CONNECTION_NONBLOCKING",
        AGENT_BACKEND_ENV_VAR,
    }
)

RESUME_SESSION_BASELINE_KEYS: frozenset[str] = frozenset(
    {
        "MAX_MCP_OUTPUT_TOKENS",
    }
)

CODEX_MCP_ENV_FORWARD_VARS: frozenset[str] = frozenset(
    {
        HEADLESS_ENV_VAR,
        HEADLESS_AUTO_GATE_ENV_VAR,
        MCP_CLIENT_BACKEND_ENV_VAR,
    }
)

CODEX_INTERACTIVE_REQUIRED_ENV: frozenset[str] = frozenset(
    {
        MCP_CLIENT_BACKEND_ENV_VAR,
    }
)

CONTEXT_EXHAUSTION_MARKER = "prompt is too long"
CODEX_CONTEXT_EXHAUSTION_MARKER = "context_length_exceeded"

RESERVED_LOG_RECORD_KEYS: frozenset[str] = frozenset(
    {
        "name",
        "msg",
        "args",
        "levelname",
        "levelno",
        "pathname",
        "filename",
        "module",
        "exc_info",
        "exc_text",
        "stack_info",
        "lineno",
        "funcName",
        "created",
        "msecs",
        "relativeCreated",
        "thread",
        "threadName",
        "processName",
        "process",
        "taskName",
        "message",
        "asctime",
    }
)
