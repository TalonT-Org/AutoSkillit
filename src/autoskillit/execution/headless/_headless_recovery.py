"""Recovery helpers for headless Claude session result reconstruction."""

from __future__ import annotations

import dataclasses
import json
import os
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple

import regex as re

from autoskillit.core import (
    CliSubtype,
    SkillContractView,
    extract_bash_write_targets,
    get_logger,
)
from autoskillit.execution.headless._headless_path_tokens import (
    _RECOVERABLE_PATH_TOKENS,
    _is_path_outside_cwd,
)
from autoskillit.execution.process import _marker_is_standalone
from autoskillit.execution.session import (
    ClaudeSessionResult,
    _check_expected_patterns,
)
from autoskillit.execution.session._session_content import _normalize_model_output
from autoskillit.execution.session._session_model import _is_parent_assistant_record

if TYPE_CHECKING:
    from autoskillit.core import ResultParser

logger = get_logger(__name__)

# Drain-race recovery subtypes: TIMEOUT and UNKNOWN excluded (time-limit breach; unrecognised CLI).
_CHANNEL_B_RECOVERABLE_SUBTYPES: frozenset[CliSubtype] = frozenset(
    {CliSubtype.UNPARSEABLE, CliSubtype.EMPTY_OUTPUT}
)

_TOKEN_NAME_RE: re.Pattern[str] = re.compile(r"^(\w+)")

# Matches "token = value" (or "token[ \t]*=[ \t]*value") write_expected_when patterns
# whose value segment is a bare literal — no alternation/character-class, i.e. not
# "(a|b)". This is the structural soundness gate for deterministic enum inference.
_ENUM_BINDING_RE: re.Pattern[str] = re.compile(r"^(\w+)(?:\[[^\]]*\]\*)?=(?:\[[^\]]*\]\*)?(\w+)$")

_CANONICAL_TO_LEGACY: dict[str, str | None] = {
    "input_tokens": None,
    "output_tokens": None,
    "cache_write_tokens": "cache_creation_input_tokens",
    "cache_read_tokens": "cache_read_input_tokens",
}


class _PathHint(NamedTuple):
    """Missing path-capture token: dictate the exact observed write path."""

    token: str
    path: str


class _EnumHint(NamedTuple):
    """Missing enum-typed token: ask the session to choose from allowed_values."""

    token: str
    allowed_values: tuple[str, ...]


def _is_path_capture_pattern(pattern: str) -> str | None:
    """Return the token name if pattern is a path-capture pattern, else None.

    Classification uses outputs[].type metadata from skill_contracts.yaml rather than
    the pattern string suffix format, so all path-capture patterns are covered regardless
    of whether they end in /.+, \\S+, .+, or any other suffix.
    """
    m = _TOKEN_NAME_RE.match(pattern)
    if not m:
        return None
    token_name = m.group(1)
    if token_name not in _RECOVERABLE_PATH_TOKENS:
        return None
    if "=" not in pattern[m.end() :]:
        return None
    return token_name


def _scan_jsonl_write_paths(
    stdout: str,
    cwd: str,
    *,
    write_tool_names: frozenset[str] = frozenset({"Write", "Edit"}),
    bash_tool_name: str = "Bash",
) -> list[str]:
    """Scan raw JSONL stdout for Write/Edit/Bash tool calls outside cwd."""
    if not stdout.strip() or not cwd or not os.path.isabs(cwd):
        return []

    warnings: list[str] = []
    for raw_line in stdout.strip().splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict) or not _is_parent_assistant_record(obj):
            continue
        msg = obj.get("message")
        if not isinstance(msg, dict):
            continue
        content = msg.get("content", [])
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            tool_name = block.get("name", "")
            inputs = block.get("input") or {}
            if not isinstance(inputs, dict):
                continue
            if tool_name == bash_tool_name:
                command = inputs.get("command", "")
                if isinstance(command, str):
                    for path in extract_bash_write_targets(command, cwd):
                        if _is_path_outside_cwd(path, cwd):
                            normalized = os.path.normpath(path)
                            warnings.append(
                                f"Bash command contained write target '{normalized}'"
                                f" outside session cwd '{cwd}'"
                            )
            elif tool_name in write_tool_names:
                file_path = inputs.get("file_path", "")
                if isinstance(file_path, str) and _is_path_outside_cwd(file_path, cwd):
                    warnings.append(
                        f"{tool_name} tool targeted '{file_path}' outside session cwd '{cwd}'"
                    )
    return warnings


def _recover_from_separate_marker(
    session: ClaudeSessionResult,
    completion_marker: str,
) -> ClaudeSessionResult | None:
    """Attempt recovery when the model emitted the completion marker as a standalone
    final message rather than inline with its substantive output.

    Returns a reconstructed ClaudeSessionResult whose result field contains the
    combined assistant message content (including the marker), or None if recovery
    is not possible (no assistant content, or no substantive content beyond the marker).
    """
    if not session.assistant_messages:
        return None
    if not any(
        _marker_is_standalone(msg, completion_marker) for msg in session.assistant_messages
    ):
        return None
    combined = "\n\n".join(session.assistant_messages)
    stripped = combined.replace(completion_marker, "").strip()
    if not stripped:
        return None  # only the marker exists — genuine failure, do not recover
    logger.warning(
        "completion_marker_in_separate_message",
        recovery="rebuilding result from assistant_messages",
    )
    return dataclasses.replace(session, result=combined)


def _recover_block_from_assistant_messages(
    session: ClaudeSessionResult,
    expected_output_patterns: Sequence[str],
) -> ClaudeSessionResult | None:
    """When session.result lacks expected_output_patterns (drain-race condition
    on either channel), attempt to find the patterns in session.assistant_messages.
    If found, return a new ClaudeSessionResult with result reconstructed from
    assistant_messages. Return None if patterns cannot be found there either.
    """
    if not session.assistant_messages or not expected_output_patterns:
        return None
    combined = "\n\n".join(session.assistant_messages)
    if not _check_expected_patterns(combined, expected_output_patterns):
        return None
    logger.warning(
        "pattern_recovered_from_assistant_messages",
        patterns=list(expected_output_patterns),
    )
    # Preserve any content already drained into session.result.
    recovered = (session.result + "\n\n" + combined) if session.result else combined
    return dataclasses.replace(session, result=recovered)


def _synthesize_from_write_artifacts(
    session: ClaudeSessionResult,
    expected_output_patterns: list[str],
    write_call_count: int,
    fs_writes_detected: bool = False,
    write_tool_names: frozenset[str] = frozenset({"Write", "Edit"}),
    file_changes: Sequence[str] = (),
) -> ClaudeSessionResult | None:
    """Synthesize missing structured output tokens from write tool_use file_path data.

    When the session has write evidence (write_call_count >= 1) and expected_output_patterns
    contain path-capture patterns (e.g., ``plan_path\\s*=\\s*/.+``), scan tool_uses for
    entries matching write_tool_names with absolute file_path values. For each pattern whose
    token name can be extracted, inject ``{token_name} = {file_path}`` into session.result so
    that _compute_outcome sees the token as if the model had emitted it.

    Returns a new ClaudeSessionResult with the injected line prepended to result, or None if
    synthesis is not possible (no matching file_path, no path-capture patterns, or pattern
    already satisfied)."""
    if write_call_count == 0 and not file_changes:
        return None

    # Only synthesize path-capture patterns; non-path patterns must remain text-compliance-only.
    synthesized_lines: list[str] = []
    for pattern in expected_output_patterns:
        token_name = _is_path_capture_pattern(pattern)
        if not token_name:
            continue
        # Skip if the pattern is already satisfied in the current result.
        if re.search(pattern, _normalize_model_output(session.result)):
            continue
        # Collect ALL absolute Write/Edit paths; use the LAST one (final deliverable).
        # Multi-artifact skills write intermediate files first, final deliverable last.
        candidate_paths = [
            t.get("file_path", "")
            for t in session.tool_uses
            if t.get("name") in write_tool_names and t.get("file_path", "").startswith("/")
        ]
        if not candidate_paths and file_changes:
            candidate_paths = list(file_changes)
        if candidate_paths:
            synthesized_lines.append(f"{token_name} = {candidate_paths[-1]}")

    if not synthesized_lines:
        return None

    injected = "\n".join(synthesized_lines) + "\n" + session.result
    return dataclasses.replace(session, result=injected)


def _parse_single_enum_binding(
    skill_contract: SkillContractView | None,
) -> tuple[str, str] | None:
    """Return (token_name, literal_value) iff write_expected_when soundly binds one enum value.

    Structural soundness gate — fires only when ALL hold:
    - ``write_behavior == "conditional"``
    - exactly one ``write_expected_when`` pattern
    - that pattern parses as ``token = literal`` with no alternation/character-class
      in the value segment (rejects e.g. ``verdict[ \\t]*=[ \\t]*(a|b)``)
    - the literal is a declared ``allowed_values`` member of the same-named output

    Encodes the SOUND class from the contract soundness audit structurally — no
    skill-name allowlist — so any future contract with this shape is covered for free.
    """
    if skill_contract is None:
        return None
    if skill_contract.write_behavior != "conditional":
        return None
    if len(skill_contract.write_expected_when) != 1:
        return None
    match = _ENUM_BINDING_RE.match(skill_contract.write_expected_when[0])
    if not match:
        return None
    token_name, literal_value = match.group(1), match.group(2)
    for output in skill_contract.outputs:
        if output.name == token_name:
            if output.allowed_values and literal_value in output.allowed_values:
                return (token_name, literal_value)
            return None
    return None


def _infer_enum_token_from_write_contract(
    session: ClaudeSessionResult,
    expected_output_patterns: Sequence[str],
    skill_contract: SkillContractView | None,
    write_call_count: int,
    file_changes: Sequence[str] = (),
) -> ClaudeSessionResult | None:
    """Deterministically synthesize an enum-typed output token from write-contract evidence.

    Unlike ``_synthesize_from_write_artifacts`` (which fabricates a token the agent never
    produced and is therefore gated to UNMONITORED-only), this derives the token from
    evidence the agent DID observably produce: an emitted companion path-token line
    (in a confirmed channel) whose extracted path exists on disk, combined with the
    contract's own declared write-expected-when implication. Runs for all channels.

    Fires only when: (1) ``_parse_single_enum_binding`` finds a sound single binding;
    (2) an expected pattern for that same token remains unsatisfied; (3) write evidence
    exists; (4) a companion output typed ``file_path*``/``directory_path`` has a token
    line present in ``session.result`` whose path(s) exist on disk.
    """
    binding = _parse_single_enum_binding(skill_contract)
    if binding is None:
        return None
    token_name, literal_value = binding

    if write_call_count == 0 and not file_changes:
        return None

    normalized_result = _normalize_model_output(session.result)
    target_unsatisfied = False
    for pattern in expected_output_patterns:
        m = _TOKEN_NAME_RE.match(pattern)
        if not m or m.group(1) != token_name:
            continue
        if re.search(pattern, normalized_result):
            return None  # already satisfied — nothing to infer
        target_unsatisfied = True
    if not target_unsatisfied:
        return None

    assert skill_contract is not None  # narrowed by _parse_single_enum_binding above
    companion_path = None
    for output in skill_contract.outputs:
        if output.name == token_name:
            continue
        if not (output.type.startswith("file_path") or output.type == "directory_path"):
            continue
        companion_match = re.search(
            rf"^{re.escape(output.name)}\s*=\s*(.+)$", normalized_result, re.MULTILINE
        )
        if not companion_match:
            continue
        value_str = companion_match.group(1).strip()
        if output.type == "file_path_list":
            candidate_paths = [p.strip() for p in value_str.split(",") if p.strip()]
        else:
            candidate_paths = [value_str] if value_str else []
        if candidate_paths and all(Path(p).is_file() or Path(p).is_dir() for p in candidate_paths):
            companion_path = candidate_paths[-1]
            break
    if companion_path is None:
        return None

    logger.info(
        "enum_inference_applied",
        field_name=token_name,
        value=literal_value,
        companion_path=companion_path,
    )
    injected = f"{token_name} = {literal_value}\n" + session.result
    return dataclasses.replace(session, result=injected)


def _extract_missing_token_hints(
    stdout: str,
    expected_output_patterns: Sequence[str],
    result_parser: ResultParser,
    write_tool_names: frozenset[str],
    skill_contract: SkillContractView | None = None,
) -> list[_PathHint | _EnumHint]:
    """Extract missing-token hints for patterns missing from the result.

    Parses raw NDJSON stdout to find write tool_use file_path entries, then matches
    them against path-capture patterns that are NOT satisfied in the result text
    (producing ``_PathHint``). When ``skill_contract`` is provided, unsatisfied
    patterns whose leading token names an enum-typed output (non-empty
    ``allowed_values``) produce an ``_EnumHint`` instead. Returns the hints needed
    to build the nudge prompt.
    """
    try:
        session = result_parser.parse_stdout(stdout)
    except Exception:
        logger.warning("nudge_parse_stdout_failed", exc_info=True)
        return []
    hints: list[_PathHint | _EnumHint] = []
    contract_outputs_by_name = (
        {output.name: output for output in skill_contract.outputs}
        if skill_contract is not None
        else {}
    )
    normalized_output = _normalize_model_output(session.output)

    for pattern in expected_output_patterns:
        token_name = _is_path_capture_pattern(pattern)
        if token_name:
            # Skip if already satisfied
            if re.search(pattern, normalized_output):
                continue
            # Collect absolute Write/Edit paths; use the LAST one (final deliverable).
            # tool_uses and token_usage live in .raw (not promoted to typed attrs) because
            # they vary by backend and are absent from non-Claude AgentSessionResult payloads.
            candidate_paths = [
                t.get("file_path", "")
                for t in session.raw.get("tool_uses", [])
                if t.get("name") in write_tool_names and t.get("file_path", "").startswith("/")
            ]
            if candidate_paths:
                hints.append(_PathHint(token_name, candidate_paths[-1]))
            continue

        m = _TOKEN_NAME_RE.match(pattern)
        if not m:
            continue
        enum_token = m.group(1)
        output = contract_outputs_by_name.get(enum_token)
        if output is None or not output.allowed_values:
            continue
        if re.search(pattern, normalized_output):
            continue
        hints.append(_EnumHint(enum_token, tuple(output.allowed_values)))

    return hints


# Group B migration target: token aggregation to backend-agnostic layer.
def _merge_token_usage(
    base: dict[str, object] | None,
    nudge: dict[str, object] | None,
) -> dict[str, object] | None:
    """Additively merge token usage dicts from main session and nudge."""
    if base is None:
        return nudge
    if nudge is None:
        return base
    merged = dict(base)
    for canonical, legacy in _CANONICAL_TO_LEGACY.items():
        b = base.get(canonical) if canonical in base else base.get(legacy) if legacy else None
        n = nudge.get(canonical) if canonical in nudge else nudge.get(legacy) if legacy else None
        if b is None and n is None:
            continue
        bv = b if b is not None else 0
        nv = n if n is not None else 0
        if isinstance(bv, (int, float)) and isinstance(nv, (int, float)):
            merged[canonical] = bv + nv
    for legacy in _CANONICAL_TO_LEGACY.values():
        if legacy and legacy in merged:
            del merged[legacy]
    return merged
