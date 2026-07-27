"""TOML serialization and MCP registration for the Codex backend."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal, NamedTuple

import regex as _re

from autoskillit.core import (
    CODEX_MCP_ENV_FORWARD_VARS,
    HEADLESS_AUTO_GATE_ENV_VAR,
    RECIPE_DELIVERY_ATTESTATION_AUDIENCE,
    RECIPE_DELIVERY_SURFACE_REGISTRY,
    RECIPE_DELIVERY_SURFACE_REGISTRY_DIGEST,
    RESPONSE_BACKSTOP_EXEMPTION_REGISTRY,
    RESPONSE_BACKSTOP_EXEMPTION_REGISTRY_DIGEST,
    ReadResult,
    RecipeDeliveryBudgetDef,
    RecipeDeliveryEvidenceDef,
    atomic_write,
    get_logger,
    safe_upsert_section,
)
from autoskillit.execution.backends._codex_config_lock import CodexConfigLock

# Floor value: 14364.0 = max(3600 + 7200, 7200) * 1.33
# Computed from FleetConfig and RunSkillConfig dataclass defaults.
# This is a literal to avoid importing config/ (IL-004 constraint).
CODEX_MCP_TOOL_TIMEOUT_FLOOR: float = 14364.0

CODEX_MCP_STARTUP_TIMEOUT_SEC: float = 30.0

# The configured history-retention requirement is derived from the largest
# measured recipe exemption plus explicit serialized-response headroom.
_MAX_RESPONSE_BACKSTOP_EXEMPTION_BYTES: int = max(
    definition.max_utf8_bytes for definition in RESPONSE_BACKSTOP_EXEMPTION_REGISTRY.values()
)
_CODEX_RECIPE_DELIVERY_HEADROOM_TOKENS: int = 8_000
_CODEX_ORDINARY_RESULT_TOKEN_LIMIT: int = 10_000
_CODEX_ATTESTED_RECIPE_RESULT_TOKEN_LIMIT: int = (
    (_MAX_RESPONSE_BACKSTOP_EXEMPTION_BYTES + 3) // 4
) + _CODEX_RECIPE_DELIVERY_HEADROOM_TOKENS

_CODEX_RECIPE_DELIVERY_CONTRACT_DIGEST = (
    "sha256:"
    + hashlib.sha256(
        json.dumps(
            {
                "attested_result_tokens": _CODEX_ATTESTED_RECIPE_RESULT_TOKEN_LIMIT,
                "contract_version": 1,
                "evidence_version": 1,
                "headroom_tokens": _CODEX_RECIPE_DELIVERY_HEADROOM_TOKENS,
                "history_retention_tokens": _CODEX_ATTESTED_RECIPE_RESULT_TOKEN_LIMIT,
                "measured_recipe_bytes": _MAX_RESPONSE_BACKSTOP_EXEMPTION_BYTES,
                "ordinary_result_tokens": _CODEX_ORDINARY_RESULT_TOKEN_LIMIT,
                "parser_version": 1,
                "response_exemption_registry": RESPONSE_BACKSTOP_EXEMPTION_REGISTRY_DIGEST,
                "surface_registry": RECIPE_DELIVERY_SURFACE_REGISTRY_DIGEST,
            },
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
    ).hexdigest()
)

CODEX_RECIPE_DELIVERY_BUDGET: RecipeDeliveryBudgetDef = RecipeDeliveryBudgetDef(
    ordinary_omitted_result_token_limit=_CODEX_ORDINARY_RESULT_TOKEN_LIMIT,
    authoritative_attested_recipe_result_token_limit=(_CODEX_ATTESTED_RECIPE_RESULT_TOKEN_LIMIT),
    history_retention_token_limit=_CODEX_ATTESTED_RECIPE_RESULT_TOKEN_LIMIT,
    measured_recipe_exemption_max_utf8_bytes=_MAX_RESPONSE_BACKSTOP_EXEMPTION_BYTES,
    headroom_tokens=_CODEX_RECIPE_DELIVERY_HEADROOM_TOKENS,
    contract_version=1,
    parser_version=1,
    evidence_version=1,
    contract_digest=_CODEX_RECIPE_DELIVERY_CONTRACT_DIGEST,
)
CODEX_HISTORY_RETENTION_TOKEN_LIMIT: int = (
    CODEX_RECIPE_DELIVERY_BUDGET.history_retention_token_limit
)

# A protected evidence identity is enabled only with its passing conformance
# report. Writable rollout and trace formats are intentionally absent.
SUPPORTED_CODEX_RECIPE_EVIDENCE_REGISTRY: Mapping[str, RecipeDeliveryEvidenceDef] = (
    MappingProxyType({})
)


def codex_recipe_delivery_calling_contract(*, mcp_prefix: str = "") -> str:
    """Generate the only permitted Codex high-budget recipe calling contract."""
    budget = CODEX_RECIPE_DELIVERY_BUDGET
    full_recipe_tools = ", ".join(
        f"{mcp_prefix}{name}"
        for name in sorted(
            {
                definition.producer_tool
                for definition in RECIPE_DELIVERY_SURFACE_REGISTRY.values()
                if definition.negotiation_eligible
            }
        )
    )
    return "\n".join(
        (
            f"Codex recipe delivery calling contract v{budget.contract_version}:",
            (
                "- Ordinary functions.exec results must not request more than "
                f"{budget.ordinary_omitted_result_token_limit} tokens."
            ),
            (
                "- The sole exception is a full recipe call to "
                f"{full_recipe_tools} with a protected host-provided delivery request."
            ),
            (
                "- The functions.exec cell must start with exactly: // @exec: "
                f'{{"max_output_tokens": '
                f"{budget.authoritative_attested_recipe_result_token_limit}" + "}"
            ),
            "- Pass delivery_request unchanged with exactly these fields:",
            f"  audience={RECIPE_DELIVERY_ATTESTATION_AUDIENCE}",
            "  delivery_call_id=<protected-host value>",
            f"  contract_version={budget.contract_version}",
            f"  contract_digest={budget.contract_digest}",
            (
                "  caller_requested_outer_tokens="
                f"{budget.authoritative_attested_recipe_result_token_limit}"
            ),
            "  code_digest=<protected-host value>",
            (
                "- Never synthesize, infer, alter, or replay delivery_request fields. "
                "If protected host values are unavailable, omit delivery_request and use "
                "the bounded recipe_pull path."
            ),
            ("- ingredients_only calls and recipe resources are not eligible for the exception."),
        )
    )


CODEX_RECIPE_DELIVERY_CALLING_CONTRACT: str = codex_recipe_delivery_calling_contract()
CODEX_RECIPE_DELIVERY_CALLING_CONTRACT_DIGEST: str = hashlib.sha256(
    CODEX_RECIPE_DELIVERY_CALLING_CONTRACT.encode("utf-8")
).hexdigest()

# Disable Codex auto-compaction by setting the limit to an unreachable value.
# Auto-compaction at 90% of 258K context window can destroy recipe content
# loaded by `open_kitchen`, leaving the agent without the recipe steps it
# needs to complete the pipeline. Defense-in-depth is provided by the
# `recipe_read_guard.py` PreToolUse hook which prevents the agent from
# re-reading recipe files via run_cmd/run_python after compaction. See the
# "CODEX_AUTO_COMPACT_LIMIT" entry in CODEX_LIMIT_VERIFICATION_REGISTRY below
# for the upstream-neutralization finding.
CODEX_AUTO_COMPACT_LIMIT: int = 999_999_999

CodexLimitVerificationStatus = Literal[
    "upstream_honored", "upstream_neutralized", "locally_unreachable"
]


class CodexLimitVerificationDef(NamedTuple):
    """Recorded outcome of verifying one governed limit against a pinned CLI revision."""

    governed_symbol: str
    checked_at_cli_version: tuple[int, int, int]
    upstream_revision: str
    upstream_sources: tuple[str, ...]
    status: CodexLimitVerificationStatus
    codex_config_key: str | None
    configured_value: int | None
    upstream_effective_value: int | None
    finding: str


def validate_codex_limit_verification(entry: CodexLimitVerificationDef, *, key: str) -> None:
    """Raise ValueError if entry's declared status contradicts its recorded numbers."""
    if entry.governed_symbol != key:
        raise ValueError(
            f"{key}: governed_symbol {entry.governed_symbol!r} must equal registry key {key!r}"
        )
    if len(entry.checked_at_cli_version) != 3 or not all(
        isinstance(v, int) for v in entry.checked_at_cli_version
    ):
        raise ValueError(f"{key}: checked_at_cli_version must be a 3-tuple of ints")
    if not entry.upstream_revision:
        raise ValueError(f"{key}: upstream_revision must be non-empty")
    if not entry.upstream_sources:
        raise ValueError(f"{key}: upstream_sources must be non-empty")
    if len(entry.finding) < 80:
        raise ValueError(f"{key}: finding is too thin to act on")
    if entry.codex_config_key is not None and entry.configured_value is None:
        raise ValueError(f"{key}: codex_config_key is set but configured_value is None")
    if entry.status == "upstream_honored":
        honored = (
            entry.configured_value is not None
            and entry.upstream_effective_value == entry.configured_value
        )
        if not honored:
            raise ValueError(
                f"{key}: status upstream_honored requires "
                "upstream_effective_value == configured_value"
            )
    elif entry.status == "upstream_neutralized":
        neutralized = (
            entry.configured_value is not None
            and entry.upstream_effective_value is not None
            and entry.upstream_effective_value != entry.configured_value
        )
        if not neutralized:
            raise ValueError(
                f"{key}: status upstream_neutralized requires configured_value and "
                "upstream_effective_value to both be set and differ"
            )
    elif entry.status == "locally_unreachable":
        unreachable = (
            entry.upstream_effective_value is None
            and entry.configured_value is None
            and entry.codex_config_key is None
        )
        if not unreachable:
            raise ValueError(
                f"{key}: status locally_unreachable requires upstream_effective_value, "
                "configured_value, and codex_config_key to all be None"
            )


CODEX_LIMIT_VERIFICATION_REGISTRY: Mapping[str, CodexLimitVerificationDef] = MappingProxyType(
    {
        "CODEX_HISTORY_RETENTION_TOKEN_LIMIT": CodexLimitVerificationDef(
            governed_symbol="CODEX_HISTORY_RETENTION_TOKEN_LIMIT",
            checked_at_cli_version=(0, 145, 0),
            upstream_revision="25af12f7e61572b0bc18ddb1008be543b91519b0",
            upstream_sources=(
                "codex-rs/models-manager/src/model_info.rs::with_config_overrides",
                "codex-rs/utils/string/src/truncate.rs::APPROX_BYTES_PER_TOKEN",
                "codex-rs/core/src/tools/mod.rs::format_exec_output_for_model",
                "codex-rs/core/src/context_manager/history.rs::record_items",
            ),
            status="upstream_honored",
            codex_config_key="tool_output_token_limit",
            configured_value=CODEX_HISTORY_RETENTION_TOKEN_LIMIT,
            upstream_effective_value=CODEX_HISTORY_RETENTION_TOKEN_LIMIT,
            finding=(
                "tool_output_token_limit is applied verbatim: with_config_overrides replaces "
                "ModelInfo.truncation_policy with no clamp (unlike model_context_window, which "
                "is min()-clamped to max_context_window). APPROX_BYTES_PER_TOKEN is still 4. "
                "The gpt-5.6-sol catalog default is {mode: tokens, limit: 10000}, so this "
                "override is the only thing keeping recipe payloads intact. The same "
                "truncation_policy field governs BOTH the current-turn exec output sent to "
                "the model and retained history -- the earlier '(later history only)' "
                "qualifier was factually wrong and is removed."
            ),
        ),
        "CODEX_AUTO_COMPACT_LIMIT": CodexLimitVerificationDef(
            governed_symbol="CODEX_AUTO_COMPACT_LIMIT",
            checked_at_cli_version=(0, 145, 0),
            upstream_revision="25af12f7e61572b0bc18ddb1008be543b91519b0",
            upstream_sources=(
                "codex-rs/protocol/src/openai_models.rs::ModelInfo::auto_compact_token_limit",
                "codex-rs/protocol/src/config_types.rs::AutoCompactTokenLimitScope",
                "codex-rs/core/src/session/context_window.rs",
            ),
            status="upstream_neutralized",
            codex_config_key="model_auto_compact_token_limit",
            configured_value=CODEX_AUTO_COMPACT_LIMIT,
            upstream_effective_value=244_800,
            finding=(
                "ModelInfo::auto_compact_token_limit() returns "
                "min(config, resolved_context_window * 9 / 10). AutoCompactTokenLimitScope "
                "defaults to Total, under which the clamped accessor is used, and AutoSkillit "
                "never writes model_auto_compact_token_limit_scope. gpt-5.6-sol's "
                "resolved_context_window is 272_000, so the effective threshold is 244_800, "
                "not the configured 999_999_999. Measured peak last_token_usage.total_tokens "
                "before the first compaction across 26 compacted 0.145.0 rollouts was "
                "244_865 (0.027 percent over 244_800); 34 compaction events occurred under "
                "0.145.0. The sentinel does not disable auto-compaction and ADR-0004's "
                "'primary defense' framing no longer holds; the recovery-path replacement is "
                "owned by #4271."
            ),
        ),
        "CODEX_RECIPE_DELIVERY_BUDGET": CodexLimitVerificationDef(
            governed_symbol="CODEX_RECIPE_DELIVERY_BUDGET",
            checked_at_cli_version=(0, 145, 0),
            upstream_revision="25af12f7e61572b0bc18ddb1008be543b91519b0",
            upstream_sources=(
                "autoskillit: core/_delivery_bounds.py::resolve_recipe_delivery_decision",
                "autoskillit: execution/backends/_codex_config.py"
                "::SUPPORTED_CODEX_RECIPE_EVIDENCE_REGISTRY",
            ),
            status="locally_unreachable",
            codex_config_key=None,
            configured_value=None,
            upstream_effective_value=None,
            finding=(
                "SUPPORTED_CODEX_RECIPE_EVIDENCE_REGISTRY is empty, so every caller reaches "
                "resolve_recipe_delivery_decision with supported_evidence=None and the "
                "ATTESTED_INLINE terminal branch is unreachable in the live call graph. The "
                '// @exec: {"max_output_tokens": 56750} cell contract was emitted in 0 of '
                "552 0.145.0 rollouts; zero AutoSkillit-launched Codex sessions have run "
                "under 0.145.0. Positive verification requires the CODEX_SMOKE_TEST=1 live "
                "probe suite; this pin does not certify upstream parser behavior for this "
                "surface."
            ),
        ),
    }
)


def _validate_codex_limit_verification_registry() -> tuple[int, int, int]:
    if not CODEX_LIMIT_VERIFICATION_REGISTRY:
        raise ValueError(
            "CODEX_LIMIT_VERIFICATION_REGISTRY must not be empty: "
            "CODEX_LIMITS_LAST_VERIFIED_VERSION is derived from it"
        )
    for key, entry in CODEX_LIMIT_VERIFICATION_REGISTRY.items():
        validate_codex_limit_verification(entry, key=key)
    return min(e.checked_at_cli_version for e in CODEX_LIMIT_VERIFICATION_REGISTRY.values())


def _codex_limit_verification_registry_digest() -> str:
    canonical = [
        (key, entry._asdict()) for key, entry in sorted(CODEX_LIMIT_VERIFICATION_REGISTRY.items())
    ]
    payload = json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(payload.encode("ascii")).hexdigest()


# Codex CLI version against which the numeric limits above were last verified,
# derived as the minimum checked_at_cli_version over CODEX_LIMIT_VERIFICATION_REGISTRY --
# the pin cannot be bumped without updating the evidence recorded alongside it. The
# doctor check `codex_limits_verified` warns when the installed CLI is newer.
CODEX_LIMITS_LAST_VERIFIED_VERSION: tuple[int, int, int] = (
    _validate_codex_limit_verification_registry()
)

CODEX_LIMIT_VERIFICATION_REGISTRY_DIGEST: str = _codex_limit_verification_registry_digest()

# Keys that must be present in the autoskillit MCP server entry for the Codex
# backend. Validated against the entry dict actually written by
# `ensure_codex_mcp_registered` via the arch test
# `test_required_keys_coverage` in `tests/execution/backends/test_codex_config.py`.
CODEX_MCP_REQUIRED_KEYS: frozenset[str] = frozenset(
    {"command", "env_vars", "startup_timeout_sec", "tool_timeout_sec"}
)

logger = get_logger(__name__)


def _format_toml_value(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, str):
        escaped = (
            v.replace("\\", "\\\\")
            .replace('"', '\\"')
            .replace("\n", "\\n")
            .replace("\r", "\\r")
            .replace("\t", "\\t")
        )
        return f'"{escaped}"'
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        return str(v)
    if isinstance(v, list):
        items = ", ".join(_format_toml_value(item) for item in v)
        return f"[{items}]"
    msg = f"Unsupported TOML value type: {type(v).__name__}"
    raise TypeError(msg)


_BARE_KEY_RE = _re.compile(r"^[A-Za-z0-9_-]+$")


_CONTROL_CHAR_MAP = {
    "\b": "\\b",
    "\t": "\\t",
    "\n": "\\n",
    "\f": "\\f",
    "\r": "\\r",
}


def _quote_key(k: str) -> str:
    if _BARE_KEY_RE.match(k):
        return k
    escaped = k.replace("\\", "\\\\").replace('"', '\\"')
    for ch, seq in _CONTROL_CHAR_MAP.items():
        escaped = escaped.replace(ch, seq)
    return f'"{escaped}"'


def _format_inline_table(d: dict[str, Any]) -> str:
    pairs = [f"{_quote_key(k)} = {_format_toml_value(v)}" for k, v in d.items()]
    return "{" + ", ".join(pairs) + "}"


def _classify_list(key: str, lst: list) -> bool:
    """Return True if list is all-dicts (array-of-tables), False if all-scalars.

    Raises TypeError for mixed lists containing both dict and non-dict items.
    """
    if not lst:
        return False
    has_dicts = any(isinstance(item, dict) for item in lst)
    has_non_dicts = any(not isinstance(item, dict) for item in lst)
    if has_dicts and has_non_dicts:
        msg = f"TOML array {key!r} contains both dict and non-dict items"
        raise TypeError(msg)
    return has_dicts


def _emit_aot_entry(d: dict[str, Any], path: list[str], lines: list[str]) -> None:
    """Emit a single [[path]] array-of-tables entry."""
    lines.append(f"\n[[{'.'.join(_quote_key(p) for p in path)}]]")
    nested_aot: list[tuple[str, list[dict]]] = []
    for k, v in d.items():
        if isinstance(v, dict):
            lines.append(f"{_quote_key(k)} = {_format_inline_table(v)}")
        elif isinstance(v, list) and _classify_list(k, v):
            nested_aot.append((k, v))
        else:
            lines.append(f"{_quote_key(k)} = {_format_toml_value(v)}")
    for k, entries in nested_aot:
        for entry in entries:
            _emit_aot_entry(entry, [*path, k], lines)


def _emit_toml_table(d: dict[str, Any], path: list[str], lines: list[str]) -> None:
    header = f"[{'.'.join(_quote_key(p) for p in path)}]"

    scalars: list[tuple[str, Any]] = []
    tables: list[tuple[str, dict]] = []
    aot_keys: list[tuple[str, list[dict]]] = []
    for k, v in d.items():
        if isinstance(v, dict):
            tables.append((k, v))
        elif isinstance(v, list) and _classify_list(k, v):
            aot_keys.append((k, v))
        else:
            scalars.append((k, v))

    has_scalars = bool(scalars)

    inline_tables: list[tuple[str, dict[str, Any]]] = []
    recurse_tables: list[tuple[str, dict[str, Any]]] = []
    for k, v in tables:
        if not v:
            inline_tables.append((k, v))
            continue
        is_leaf = not any(isinstance(sv, dict) for sv in v.values())
        if is_leaf and has_scalars:
            inline_tables.append((k, v))
        else:
            recurse_tables.append((k, v))

    if scalars or inline_tables:
        lines.append(f"\n{header}")
        for k, v in scalars:
            lines.append(f"{_quote_key(k)} = {_format_toml_value(v)}")
        for k, v in inline_tables:
            lines.append(f"{_quote_key(k)} = {_format_inline_table(v)}")

    for k, v in recurse_tables:
        _emit_toml_table(v, [*path, k], lines)

    for k, entries in aot_keys:
        for entry in entries:
            _emit_aot_entry(entry, [*path, k], lines)


def _serialize_toml(data: dict[str, Any]) -> str:
    lines: list[str] = []
    for k, v in data.items():
        if isinstance(v, dict):
            continue
        if isinstance(v, list) and _classify_list(k, v):
            continue
        lines.append(f"{_quote_key(k)} = {_format_toml_value(v)}")
    for k, v in data.items():
        if isinstance(v, dict):
            _emit_toml_table(v, [k], lines)
    for k, v in data.items():
        if isinstance(v, list) and _classify_list(k, v):
            for entry in v:
                _emit_aot_entry(entry, [k], lines)
    text = "\n".join(lines).lstrip("\n")
    return text + "\n" if text else ""


def _read_codex_config(path: Path) -> ReadResult:
    import tomllib

    try:
        raw_bytes = path.read_bytes()
    except FileNotFoundError:
        return ReadResult.missing({})
    try:
        data = tomllib.loads(raw_bytes.decode("utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError):
        logger.warning("corrupt_codex_config", path=str(path))
        return ReadResult.corrupt(raw_bytes)
    return ReadResult.ok(data)


def _write_codex_config(path: Path, data: dict[str, Any], *, source: ReadResult) -> None:
    if source.is_corrupt:
        raise ValueError(
            "_write_codex_config does not handle corrupt sources — "
            "callers must route corrupt paths before calling"
        )
    atomic_write(path, _serialize_toml(data))


def _serialize_mcp_autoskillit_section(entry: dict[str, Any]) -> str:
    return _serialize_toml({"mcp_servers": {"autoskillit": entry}})


def _is_autoskillit_registered(
    config: dict[str, Any],
    *,
    headless_auto_gate: bool,
    expected_tool_timeout: float = CODEX_MCP_TOOL_TIMEOUT_FLOOR,
) -> bool:
    entry = config.get("mcp_servers", {}).get("autoskillit")
    if not isinstance(entry, dict):
        return False
    if entry.get("command") != "autoskillit":
        return False
    env_vars = entry.get("env_vars", [])
    if not isinstance(env_vars, list):
        return False
    required = CODEX_MCP_ENV_FORWARD_VARS
    if not headless_auto_gate:
        required = required - {HEADLESS_AUTO_GATE_ENV_VAR}
    if not required.issubset(env_vars):
        return False
    if entry.get("tool_timeout_sec") != expected_tool_timeout:
        return False
    if entry.get("startup_timeout_sec") != CODEX_MCP_STARTUP_TIMEOUT_SEC:
        return False
    if config.get("tool_output_token_limit") != CODEX_HISTORY_RETENTION_TOKEN_LIMIT:
        return False
    if config.get("model_auto_compact_token_limit", 0) < CODEX_AUTO_COMPACT_LIMIT:
        return False
    return True


def _ensure_top_level_key(path: Path, *, key: str, value: int) -> None:
    """Ensure a bare top-level integer scalar is at least ``value``.

    `safe_upsert_section` only writes `[section]` blocks; it cannot write bare
    top-level scalars. This helper handles the bare-scalar case for the
    corrupt-file path while preserving higher values.
    """
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    lines = existing.splitlines(keepends=True)
    assignment = _re.compile(rf"^\s*{_re.escape(key)}\s*=\s*(?P<value>[+-]?[0-9][0-9_]*)")
    insert_at = len(lines)
    for i, line in enumerate(lines):
        if line.lstrip().startswith("["):
            insert_at = i
            break
        match = assignment.match(line)
        if match:
            current = int(match.group("value").replace("_", ""))
            if current >= value:
                return
            newline = "\r\n" if line.endswith("\r\n") else "\n" if line.endswith("\n") else ""
            lines[i] = f"{key} = {value}{newline}"
            atomic_write(path, "".join(lines))
            return
    lines.insert(insert_at, f"{key} = {value}\n")
    atomic_write(path, "".join(lines))


def _upsert_top_level_key_exact(path: Path, *, key: str, value: int) -> None:
    """Set a bare top-level scalar to exactly ``value`` using text-level edits.

    This is deliberately separate from ``_ensure_top_level_key``: the Codex
    tool-output setting is exact, while the auto-compact setting retains its
    independent minimum semantics.
    """
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    lines = existing.splitlines(keepends=True)
    assignment = _re.compile(rf"^\s*{_re.escape(key)}\s*=")
    insert_at = len(lines)
    for i, line in enumerate(lines):
        if line.lstrip().startswith("["):
            insert_at = i
            break
        if assignment.match(line):
            newline = "\r\n" if line.endswith("\r\n") else "\n" if line.endswith("\n") else ""
            lines[i] = f"{key} = {value}{newline}"
            atomic_write(path, "".join(lines))
            return
    lines.insert(insert_at, f"{key} = {value}\n")
    atomic_write(path, "".join(lines))


def _ensure_codex_mcp_registered_unlocked(
    *,
    config_path: Path,
    headless_auto_gate: bool = True,
    tool_timeout_sec: float | None = None,
) -> bool:
    """Mutate a Codex config whose caller already owns its config lock.

    For corrupt files, always returns True (safe_upsert_section is unconditional).
    """
    effective_timeout = (
        tool_timeout_sec if tool_timeout_sec is not None else CODEX_MCP_TOOL_TIMEOUT_FLOOR
    )
    result = _read_codex_config(config_path)
    base = CODEX_MCP_ENV_FORWARD_VARS
    if not headless_auto_gate:
        base = base - {HEADLESS_AUTO_GATE_ENV_VAR}
    env_vars: list[str] = sorted(base)
    entry: dict[str, Any] = {
        "command": "autoskillit",
        "env_vars": env_vars,
        "startup_timeout_sec": CODEX_MCP_STARTUP_TIMEOUT_SEC,
        "tool_timeout_sec": effective_timeout,
    }

    if result.is_corrupt:
        section_text = _serialize_mcp_autoskillit_section(entry)
        safe_upsert_section(config_path, "[mcp_servers.autoskillit]", section_text)
        _upsert_top_level_key_exact(
            config_path,
            key="tool_output_token_limit",
            value=CODEX_HISTORY_RETENTION_TOKEN_LIMIT,
        )
        _ensure_top_level_key(
            config_path,
            key="model_auto_compact_token_limit",
            value=CODEX_AUTO_COMPACT_LIMIT,
        )
        return True
    else:
        config = result.data
        if _is_autoskillit_registered(
            config,
            headless_auto_gate=headless_auto_gate,
            expected_tool_timeout=effective_timeout,
        ):
            return False
        config.setdefault("mcp_servers", {})["autoskillit"] = entry
        config["tool_output_token_limit"] = CODEX_HISTORY_RETENTION_TOKEN_LIMIT
        existing_compact_limit = config.get("model_auto_compact_token_limit", 0)
        if not isinstance(existing_compact_limit, int):
            existing_compact_limit = 0
        config["model_auto_compact_token_limit"] = max(
            existing_compact_limit, CODEX_AUTO_COMPACT_LIMIT
        )
        _write_codex_config(config_path, config, source=result)
        return True


def ensure_codex_mcp_registered(
    *,
    config_path: Path | None = None,
    headless_auto_gate: bool = True,
    tool_timeout_sec: float | None = None,
) -> bool:
    """Return True if the shared Codex MCP entry was written.

    The public facade owns serialization for the complete read-modify-write
    transaction. Composed transactions must use the private unlocked primitive
    while holding the same lock.
    """
    resolved_config_path = (
        (Path.home() / ".codex" / "config.toml" if config_path is None else Path(config_path))
        .expanduser()
        .resolve(strict=False)
    )
    with CodexConfigLock(resolved_config_path):
        return _ensure_codex_mcp_registered_unlocked(
            config_path=resolved_config_path,
            headless_auto_gate=headless_auto_gate,
            tool_timeout_sec=tool_timeout_sec,
        )
