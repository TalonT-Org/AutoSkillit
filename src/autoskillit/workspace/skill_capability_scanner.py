"""Skill capability scanner: regex catalog and source-line classification pipeline.

Owns the regex catalog (``_STATIC_PATTERNS``, ``_SELF_INITIATED_TOOLS``,
the compiled regexes), the source-line / logical-line classification
pipeline (``_source_lines``, ``_logical_lines``, ``_classify_context``,
``_evidence``), and the uncovered ``_scan_skill_capability_evidence_uncached``
entry point that the facade wraps with caching. The
``_CLASSIFIED_CAPABILITIES != frozenset(SKILL_CAPABILITY_REGISTRY)``
registry cross-check at the bottom of the module enforces the scanner's
own classification invariant.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import cache
from typing import Literal

import regex as re

from autoskillit.core import SKILL_CAPABILITY_REGISTRY

_STATIC_PATTERNS: dict[str, tuple[re.Pattern[str], ...]] = {
    "claude_dir": (re.compile(r"\.claude/"),),
    "commit_files": (re.compile(r"\bcommit_files\s*\("),),
    "write_audit_semantic_result": (re.compile(r"\bwrite_audit_semantic_result\s*\("),),
    "write_standalone_audit_evidence": (re.compile(r"\bwrite_standalone_audit_evidence\s*\("),),
    "write_audit_disposition_bundle": (re.compile(r"\bwrite_audit_disposition_bundle\s*\("),),
    "github_api_write": (
        re.compile(
            r"gh api[^\n]*(?:(?:--method(?:\s+|=)|-X\s+)(?:POST|PATCH|PUT|DELETE))"
            r"|gh pr (?:close|comment|create|edit|lock|merge|ready|reopen|review|unlock)\b"
            r"|gh issue (?:close|comment|create|delete|develop|edit|lock|pin|reopen|"
            r"transfer|unlock|unpin)\b"
            r"|gh release (?:create|delete|edit|upload)\b",
            re.IGNORECASE,
        ),
    ),
}

_SELF_INITIATED_TOOLS: dict[str, tuple[str, ...]] = {
    "open_kitchen": ("open_kitchen", "close_kitchen"),
    "run_skill": ("run_skill",),
    "test_check": ("test_check",),
}

_STEP_HEADING_RE = re.compile(r"(?:Step\s+\d|^\d+[\.\):\s])")
_FENCE_DELIMITER_RE = re.compile(r"^(`{3,}|~{3,})")
_LOGICAL_CONTINUATION_RE = re.compile(r"\\\s*\n\s*")
_GRAPHQL_LINE_RE = re.compile(
    r"\b(?:gh\s+api\s+graphql|mutation)\b",
    re.IGNORECASE,
)
_GRAPHQL_COMMAND_RE = re.compile(r"\bgh\s+api\s+graphql\b")
_MUTATION_RE = re.compile(r"\bmutation\b", re.IGNORECASE)
_ARTIFACT_HEADING_RE = re.compile(
    r"\b(?:example|examples|output|result|response|artifact|frontmatter|"
    r"configuration|generated)\b",
    re.IGNORECASE,
)
_PARENT_TRANSPORT_CONTEXT = re.compile(
    r"\b(?:parent|orchestrator|caller)\b[^\n]{0,80}"
    r"\b(?:calls?|invokes?|launches?|dispatches?|via)\b",
    re.IGNORECASE,
)
_RESULT_CONTEXT = re.compile(
    r"\b(?:returns?|returned|result|output|response|artifact|documents?)\b",
    re.IGNORECASE,
)
_PROHIBITION_CONTEXT = re.compile(
    r"\b(?:do not|don't|never|must not|cannot|can't|without|skip)\b",
    re.IGNORECASE,
)
_EXAMPLE_CONTEXT = re.compile(
    r"\b(?:for example|example|e\.g\.|illustrative|sample|wrong|correct syntax)\b",
    re.IGNORECASE,
)
_CONFIG_CONTEXT = re.compile(
    r"(?:\buses_capabilities\s*:|\btool\s*:|"
    r"\b\w+\.(?:command|commands)\b|\bfrontmatter\b|"
    r"\brun_skill`?\s+steps?\b|\bstores?\b[^\n]{0,80}\b(?:logs?|files?)\b)",
    re.IGNORECASE,
)
_FRONTMATTER_SKILL_NAME_RE = re.compile(
    r"^name:\s*['\"]?([^'\"\n]+)",
    re.MULTILINE,
)


def _frontmatter_skill_name(content: str) -> str:
    match = _FRONTMATTER_SKILL_NAME_RE.search(content)
    return match.group(1).strip() if match else ""


def _normalize_skill_capability_name(content: str, skill_name: str | None) -> str:
    return skill_name or _frontmatter_skill_name(content)


@dataclass(frozen=True, slots=True)
class _SourceLine:
    number: int
    text: str
    executable: bool


CapabilityActor = Literal["self", "parent", "external"]
CapabilityDirection = Literal["outbound", "inbound", "descriptive"]
CapabilitySourceClassification = Literal["executable", "artifact"]


@dataclass(frozen=True, slots=True)
class SkillCapabilityEvidence:
    """One command/context-aware capability occurrence in a skill document."""

    capability: str
    actor: CapabilityActor
    direction: CapabilityDirection
    source_span: tuple[int, int]
    classification: CapabilitySourceClassification
    source: str

    @property
    def is_genuine(self) -> bool:
        """Whether this occurrence represents work initiated by the skill."""
        return (
            self.actor == "self"
            and self.direction == "outbound"
            and self.classification == "executable"
        )

    @property
    def executable(self) -> bool:
        return self.classification == "executable"

    @property
    def artifact(self) -> bool:
        return self.classification == "artifact"


def _source_lines(body: str) -> tuple[_SourceLine, ...]:
    """Mark frontmatter, constraint blocks, and documentary fences non-executable."""
    result: list[_SourceLine] = []
    in_frontmatter = body.startswith("---\n")
    frontmatter_closed = not in_frontmatter
    fence_delimiter: str | None = None
    in_step_section = False
    artifact_section = False
    prohibition_section = False

    for number, text in enumerate(body.splitlines(), start=1):
        stripped = text.strip()
        if in_frontmatter and number > 1 and stripped == "---":
            result.append(_SourceLine(number, text, False))
            in_frontmatter = False
            frontmatter_closed = True
            continue
        if not frontmatter_closed:
            result.append(_SourceLine(number, text, False))
            continue

        if stripped == "**NEVER:**":
            prohibition_section = True
            result.append(_SourceLine(number, text, False))
            continue
        if stripped.startswith("**") and stripped.endswith(":**"):
            prohibition_section = False
            result.append(_SourceLine(number, text, False))
            continue
        if stripped.startswith("#") and fence_delimiter is None:
            prohibition_section = False
            heading = stripped.lstrip("#").strip()
            heading_level = len(stripped) - len(stripped.lstrip("#"))
            is_step_heading = bool(_STEP_HEADING_RE.match(heading))
            if is_step_heading or heading_level <= 3:
                in_step_section = is_step_heading
            artifact_section = bool(_ARTIFACT_HEADING_RE.search(heading))
            result.append(_SourceLine(number, text, False))
            continue

        fence_match = _FENCE_DELIMITER_RE.match(stripped)
        marker = fence_match.group(1) if fence_match else None
        opens_fence = marker is not None and fence_delimiter is None
        closes_fence = (
            marker is not None
            and fence_delimiter is not None
            and marker[0] == fence_delimiter[0]
            and len(marker) >= len(fence_delimiter)
        )
        if opens_fence or closes_fence:
            executable = in_step_section and not artifact_section
            result.append(_SourceLine(number, text, executable))
            fence_delimiter = None if closes_fence else marker
            continue

        executable = not prohibition_section and not (
            fence_delimiter is not None and (not in_step_section or artifact_section)
        )
        result.append(_SourceLine(number, text, executable))
    return tuple(result)


def _classify_context(
    line: _SourceLine,
) -> tuple[CapabilityActor, CapabilityDirection, CapabilitySourceClassification]:
    text = line.text
    if _PARENT_TRANSPORT_CONTEXT.search(text):
        return "parent", "inbound", "artifact"
    prohibition = _PROHIBITION_CONTEXT.search(text)
    operation_positions = [
        position
        for token in (
            "Agent(",
            ".claude/",
            "commit_files",
            "write_audit_semantic_result",
            "write_standalone_audit_evidence",
            "write_audit_disposition_bundle",
            "git ",
            "gh ",
            "open_kitchen",
            "close_kitchen",
            "run_skill",
            "test_check",
            "/autoskillit:",
        )
        if (position := text.find(token)) >= 0
    ]
    if prohibition and (not operation_positions or prohibition.start() < min(operation_positions)):
        return "self", "outbound", "artifact"
    if _RESULT_CONTEXT.search(text):
        return "external", "inbound", "artifact"
    if not line.executable or _EXAMPLE_CONTEXT.search(text) or _CONFIG_CONTEXT.search(text):
        return "external", "descriptive", "artifact"
    return "self", "outbound", "executable"


def _evidence(
    capability: str,
    lines: tuple[_SourceLine, ...],
) -> SkillCapabilityEvidence:
    start = lines[0].number
    end = lines[-1].number
    actor, direction, classification = _classify_context(lines[0])
    if any(not line.executable for line in lines):
        classification = "artifact"
    return SkillCapabilityEvidence(
        capability=capability,
        actor=actor,
        direction=direction,
        source_span=(start, end),
        classification=classification,
        source="\n".join(line.text for line in lines),
    )


def _logical_lines(lines: tuple[_SourceLine, ...]) -> tuple[tuple[_SourceLine, ...], ...]:
    """Collapse shell continuations while preserving their source span."""
    result: list[tuple[_SourceLine, ...]] = []
    pending: list[_SourceLine] = []
    for line in lines:
        pending.append(line)
        if line.text.rstrip().endswith("\\"):
            continue
        result.append(tuple(pending))
        pending = []
    if pending:
        result.append(tuple(pending))
    return tuple(result)


@cache
def _tool_operation_patterns(
    tool_name: str,
) -> tuple[re.Pattern[str], re.Pattern[str]]:
    tool = re.escape(tool_name)
    return (
        re.compile(rf"\b{tool}\s*\("),
        re.compile(
            rf"\b(?:call|run|invoke|use|execute|retry|re-run|test it)\b"
            rf"[^\n]{{0,100}}\b{tool}\b",
            re.IGNORECASE,
        ),
    )


def _has_tool_operation(text: str, tool_name: str) -> bool:
    direct_call, imperative = _tool_operation_patterns(tool_name)
    stripped = text.strip().strip("`")
    return bool(
        stripped
        and not stripped.startswith("#")
        and (direct_call.search(stripped) or imperative.search(stripped))
    )


def _scan_skill_capability_evidence_uncached(
    content: str,
    effective_skill_name: str,
) -> tuple[SkillCapabilityEvidence, ...]:
    lines = _source_lines(content)
    found: list[SkillCapabilityEvidence] = []
    seen: set[tuple[str, tuple[int, int], str]] = set()

    def add(capability: str, source_lines: tuple[_SourceLine, ...]) -> None:
        item = _evidence(capability, source_lines)
        key = (item.capability, item.source_span, item.source)
        if key not in seen:
            found.append(item)
            seen.add(key)

    for logical in _logical_lines(lines):
        text = _LOGICAL_CONTINUATION_RE.sub(
            " ",
            "\n".join(line.text for line in logical),
        )

        for capability, patterns in _STATIC_PATTERNS.items():
            if any(pattern.search(text) for pattern in patterns):
                add(capability, logical)

        for capability, tool_names in _SELF_INITIATED_TOOLS.items():
            if any(_has_tool_operation(text, tool_name) for tool_name in tool_names):
                add(capability, logical)

    executable_text = "\n".join(line.text for line in lines if line.executable)
    graphql_lines = tuple(line for line in lines if _GRAPHQL_LINE_RE.search(line.text))
    if (
        _GRAPHQL_COMMAND_RE.search(executable_text)
        and _MUTATION_RE.search(executable_text)
        and graphql_lines
    ):
        add("github_api_write", graphql_lines)

    return tuple(sorted(found, key=lambda item: (item.source_span, item.capability)))


_CLASSIFIED_CAPABILITIES = frozenset(_STATIC_PATTERNS) | frozenset(_SELF_INITIATED_TOOLS)
if _CLASSIFIED_CAPABILITIES != frozenset(SKILL_CAPABILITY_REGISTRY):
    raise RuntimeError(
        "Semantic capability classifier must cover the capability registry; "
        f"missing={sorted(set(SKILL_CAPABILITY_REGISTRY) - _CLASSIFIED_CAPABILITIES)}, "
        f"extra={sorted(_CLASSIFIED_CAPABILITIES - set(SKILL_CAPABILITY_REGISTRY))}"
    )


__all__ = [
    "CapabilityActor",
    "CapabilityDirection",
    "CapabilitySourceClassification",
    "SkillCapabilityEvidence",
    "_scan_skill_capability_evidence_uncached",
]
