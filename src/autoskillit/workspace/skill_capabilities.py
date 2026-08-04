"""Semantic capability evidence classification for skill documents.

Capability declarations are an execution contract, not a keyword inventory.
This module therefore classifies command evidence by actor, direction, and
whether the source is executable instruction or merely documentary artifact.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from threading import Event, RLock
from typing import TYPE_CHECKING, Any, Literal

import regex as re

from autoskillit.core import (
    CODEX_VALID_MODEL_IDS,
    SKILL_CAPABILITY_REGISTRY,
    SKILL_SEMANTIC_SCHEMA_VERSION,
    ChildModelPolicySpec,
    ChildSpawnSpec,
    ConcurrencySpec,
    EvidenceSpec,
    GitMetadataWriteSpec,
    JoinSpec,
    LogicalRoleSpec,
    SiblingSkillSpec,
    SkillContractError,
    SkillSemanticPlan,
)

if TYPE_CHECKING:
    from autoskillit.workspace.skills import SkillInfo

CapabilityActor = Literal["self", "parent", "external"]
CapabilityDirection = Literal["outbound", "inbound", "descriptive"]
CapabilitySourceClassification = Literal["executable", "artifact"]
_SkillCapabilityEvidenceKey = tuple[str, str]

# Accounted resident payload includes exact key strings, evidence source strings,
# and a stable policy charge per immutable evidence record. Entry count bounds
# the remaining fixed per-entry overhead.
_SKILL_CAPABILITY_EVIDENCE_RECORD_WEIGHT_BYTES = 192
_SKILL_CAPABILITY_EVIDENCE_CACHE_MAX_ENTRIES = 256
_SKILL_CAPABILITY_EVIDENCE_CACHE_MAX_BYTES = 16 * 1024 * 1024
_SKILL_CAPABILITY_EVIDENCE_CACHE_MAX_INPUT_BYTES = 512 * 1024


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


@dataclass(frozen=True, slots=True)
class SkillCapabilityValidation:
    """Bidirectional comparison of declarations and genuine semantic evidence."""

    declared: frozenset[str]
    detected: frozenset[str]
    evidence: tuple[SkillCapabilityEvidence, ...]
    missing: frozenset[str]
    unsupported: frozenset[str]

    @property
    def valid(self) -> bool:
        return not self.missing and not self.unsupported


@dataclass(frozen=True, slots=True)
class _SkillCapabilityEvidenceCacheEntry:
    evidence: tuple[SkillCapabilityEvidence, ...]
    weight_bytes: int


@dataclass(frozen=True, slots=True)
class _SkillCapabilityEvidenceCacheInfo:
    max_entries: int
    max_bytes: int
    max_input_bytes: int
    entry_count: int
    weight_bytes: int
    inflight_builds: int
    inflight_waiters: int


@dataclass(slots=True)
class _SkillCapabilityEvidenceBuildState:
    event: Event = field(default_factory=Event)
    result: tuple[SkillCapabilityEvidence, ...] | None = None
    error: BaseException | None = None


class _SkillCapabilityEvidenceCache:
    """Thread-safe weighted LRU with generation-scoped single-flight state."""

    def __init__(
        self,
        *,
        max_entries: int,
        max_bytes: int,
        max_input_bytes: int,
    ) -> None:
        for field_name, value in (
            ("max_entries", max_entries),
            ("max_bytes", max_bytes),
            ("max_input_bytes", max_input_bytes),
        ):
            if value <= 0:
                raise ValueError(f"{field_name} must be positive")

        self._max_entries = max_entries
        self._max_bytes = max_bytes
        self._max_input_bytes = max_input_bytes
        self._entries: OrderedDict[
            _SkillCapabilityEvidenceKey,
            _SkillCapabilityEvidenceCacheEntry,
        ] = OrderedDict()
        self._inflight: dict[
            _SkillCapabilityEvidenceKey,
            _SkillCapabilityEvidenceBuildState,
        ] = {}
        self._weight_bytes = 0
        self._inflight_waiters = 0
        self._lock = RLock()

    @property
    def max_input_bytes(self) -> int:
        return self._max_input_bytes

    def info(self) -> _SkillCapabilityEvidenceCacheInfo:
        with self._lock:
            return _SkillCapabilityEvidenceCacheInfo(
                max_entries=self._max_entries,
                max_bytes=self._max_bytes,
                max_input_bytes=self._max_input_bytes,
                entry_count=len(self._entries),
                weight_bytes=self._weight_bytes,
                inflight_builds=len(self._inflight),
                inflight_waiters=self._inflight_waiters,
            )

    def _new_build_state(self) -> _SkillCapabilityEvidenceBuildState:
        return _SkillCapabilityEvidenceBuildState()

    def _lookup_or_register(
        self,
        key: _SkillCapabilityEvidenceKey,
    ) -> tuple[
        tuple[SkillCapabilityEvidence, ...] | None,
        _SkillCapabilityEvidenceBuildState | None,
        bool,
    ]:
        with self._lock:
            entry = self._entries.get(key)
            if entry is not None:
                self._entries.move_to_end(key)
                return entry.evidence, None, False

            state = self._inflight.get(key)
            if state is not None:
                self._inflight_waiters += 1
                return None, state, False

            state = self._new_build_state()
            self._inflight[key] = state
            return None, state, True

    def _wait_for_build(
        self,
        key: _SkillCapabilityEvidenceKey,
        state: _SkillCapabilityEvidenceBuildState,
    ) -> tuple[SkillCapabilityEvidence, ...]:
        try:
            state.event.wait()
        except BaseException:
            with self._lock:
                self._inflight_waiters -= 1
            raise

        with self._lock:
            self._inflight_waiters -= 1
            if state.error is not None:
                raise RuntimeError(
                    "Capability evidence build failed in another thread"
                ) from state.error
            result = state.result
            if result is None:
                raise RuntimeError("Capability evidence build completed without a result")
            entry = self._entries.get(key)
            if entry is not None and entry.evidence is result:
                self._entries.move_to_end(key)
            return result

    def _evict_if_needed_locked(self) -> None:
        while len(self._entries) > self._max_entries or self._weight_bytes > self._max_bytes:
            _, entry = self._entries.popitem(last=False)
            self._weight_bytes -= entry.weight_bytes

    def _publish_failure(
        self,
        key: _SkillCapabilityEvidenceKey,
        state: _SkillCapabilityEvidenceBuildState,
        error: BaseException,
    ) -> None:
        with self._lock:
            state.result = None
            state.error = error
            if self._inflight.get(key) is state:
                del self._inflight[key]
            state.event.set()

    def _complete_build(
        self,
        key: _SkillCapabilityEvidenceKey,
        state: _SkillCapabilityEvidenceBuildState,
        result: tuple[SkillCapabilityEvidence, ...],
        weight_bytes: int,
    ) -> tuple[SkillCapabilityEvidence, ...]:
        with self._lock:
            resident_mutated = False
            try:
                if weight_bytes <= self._max_bytes:
                    resident_mutated = True
                    previous = self._entries.pop(key, None)
                    if previous is not None:
                        self._weight_bytes -= previous.weight_bytes
                    self._entries[key] = _SkillCapabilityEvidenceCacheEntry(
                        evidence=result,
                        weight_bytes=weight_bytes,
                    )
                    self._weight_bytes += weight_bytes
                    self._evict_if_needed_locked()

                state.result = result
                state.error = None
                if self._inflight.get(key) is state:
                    del self._inflight[key]
                state.event.set()
            except BaseException as error:
                if resident_mutated:
                    self._entries.clear()
                    self._weight_bytes = 0
                state.result = None
                state.error = error
                if self._inflight.get(key) is state:
                    del self._inflight[key]
                state.event.set()
                raise
        return result


@dataclass(frozen=True, slots=True)
class _SourceLine:
    number: int
    text: str
    executable: bool


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
_FRONTMATTER_SKILL_NAME_RE = re.compile(
    r"^name:\s*['\"]?([^'\"\n]+)",
    re.MULTILINE,
)
_SKILL_WORD_RE = re.compile(r"(?:\bskill\b|/skill|skill\s*`)")
_CROSS_SKILL_CALL_RE = re.compile(r"\b(?:run_skill|Skill)\s*\(\s*['\"]/autoskillit:")
_LOGICAL_CONTINUATION_RE = re.compile(r"\\\s*\n\s*")
_SLASH_COMMAND_LINE_RE = re.compile(r"^`?/autoskillit:")
_GRAPHQL_LINE_RE = re.compile(
    r"\b(?:gh\s+api\s+graphql|mutation)\b",
    re.IGNORECASE,
)
_GRAPHQL_COMMAND_RE = re.compile(r"\bgh\s+api\s+graphql\b")
_MUTATION_RE = re.compile(r"\bmutation\b", re.IGNORECASE)
_EXCLUDED_SECTION_HEADINGS = frozenset({"related skills", "see also"})
_ARTIFACT_HEADING_RE = re.compile(
    r"\b(?:example|examples|output|result|response|artifact|frontmatter|"
    r"configuration|generated)\b",
    re.IGNORECASE,
)
_NON_OPERATION_CONTEXT = re.compile(
    r"\b(?:"
    r"called by|calls this via|invoked by|launched by|"
    r"do not|don't|never|must not|cannot|can't|without|skip|"
    r"returns?|returned|result|output|response|warning|denied|blocked|"
    r"configuration|config key|frontmatter|documentation|artifact|"
    r"gated behind|generated recipe"
    r")\b",
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
_EXCLUDED_PROSE_PHRASES = (
    "consider running",
    "you may want to",
    "you could run",
    "produced by",
    "consumed by",
    "called by",
    "written by",
)
_NAMING_EXCLUSION_WORDS = frozenset({"prefix", "convention", "when", "format", "syntax", "naming"})
_IMPERATIVE_VERBS = (
    "use",
    "run",
    "invoke",
    "load",
    "spawn",
    "call",
    "dispatch",
    "execute",
    "launch",
    "trigger",
)


def _frontmatter_skill_name(content: str) -> str:
    match = _FRONTMATTER_SKILL_NAME_RE.search(content)
    return match.group(1).strip() if match else ""


def _normalize_skill_capability_name(content: str, skill_name: str | None) -> str:
    return skill_name or _frontmatter_skill_name(content)


def _retained_string_weight_bytes(value: str) -> int:
    return len(value.encode("utf-8", errors="surrogatepass"))


def _skill_capability_evidence_input_weight_bytes(
    content: str,
    effective_skill_name: str,
) -> int:
    return _retained_string_weight_bytes(content) + _retained_string_weight_bytes(
        effective_skill_name
    )


def _skill_capability_evidence_entry_weight_bytes(
    input_weight_bytes: int,
    evidence: tuple[SkillCapabilityEvidence, ...],
) -> int:
    return (
        input_weight_bytes
        + sum(_retained_string_weight_bytes(item.source) for item in evidence)
        + len(evidence) * _SKILL_CAPABILITY_EVIDENCE_RECORD_WEIGHT_BYTES
    )


_SKILL_CAPABILITY_EVIDENCE_CACHE = _SkillCapabilityEvidenceCache(
    max_entries=_SKILL_CAPABILITY_EVIDENCE_CACHE_MAX_ENTRIES,
    max_bytes=_SKILL_CAPABILITY_EVIDENCE_CACHE_MAX_BYTES,
    max_input_bytes=_SKILL_CAPABILITY_EVIDENCE_CACHE_MAX_INPUT_BYTES,
)


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


def _has_imperative_cross_skill_invocation(stripped: str) -> bool:
    if "subagent_type:" in stripped:
        return False
    core = stripped.lstrip("-* ").strip()
    lower = core.lower()
    if "skill tool" in lower:
        return False
    has_skill_word = bool(_SKILL_WORD_RE.search(lower))
    has_run_skill_invocation = "run_skill" in lower and "/autoskillit:" in lower
    for verb in _IMPERATIVE_VERBS:
        prefixes = (verb + " ", verb + " the ", verb + " all ")
        if not lower.startswith(prefixes):
            continue
        if "/autoskillit:" not in lower:
            continue
        if has_skill_word or has_run_skill_invocation:
            rest = lower.split("/autoskillit:", 1)[1]
            first_word = rest.lstrip("` ").split()[0].rstrip(",.;:`'\"") if rest.split() else ""
            if first_word in _NAMING_EXCLUSION_WORDS:
                return False
            return True
        if verb not in {"use", "run", "invoke", "spawn", "call", "execute"}:
            continue
        after_prefix = lower.split(verb + " ", 1)[1] if verb + " " in lower else ""
        if not (
            after_prefix.startswith("`/autoskillit:")
            or after_prefix.startswith("the `/autoskillit:")
        ):
            continue
        rest = after_prefix.split("`/autoskillit:", 1)[-1]
        if rest.startswith(("open-", "close-")):
            return False
        first_word = rest.lstrip("` ").split()[0].rstrip(",.;:`'\"") if rest.split() else ""
        return first_word not in _NAMING_EXCLUSION_WORDS
    return False


def _has_slash_command_invocation(stripped: str) -> bool:
    if not stripped.startswith("/autoskillit:") or stripped.startswith("/autoskillit:{"):
        return False
    lower = stripped.lower()
    if _EXAMPLE_CONTEXT.search(lower):
        return False
    if "wrong" in lower or "correct:" in lower or "right:" in lower:
        return False
    return not (stripped.startswith("`") and stripped.rstrip(",.;)").endswith("`"))


def _is_cross_skill_ref(text: str, skill_name: str) -> bool:
    stripped = text.strip()
    if (
        not stripped
        or stripped.startswith("#")
        or "autoskillit:" not in stripped
        or f"autoskillit:{skill_name}" in stripped
        or "Agent(subagent_type=" in stripped
    ):
        return False
    lower = stripped.lower()
    if any(phrase in lower for phrase in _EXCLUDED_PROSE_PHRASES):
        return False
    if "skill tool" in lower and any(verb in lower for verb in ("load", "call", "use", "invoke")):
        return True
    if _CROSS_SKILL_CALL_RE.search(stripped):
        return True
    if _has_imperative_cross_skill_invocation(stripped):
        return True
    if _has_slash_command_invocation(stripped):
        return True
    return "run_skill" in lower and "/autoskillit:" in lower


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


def classify_skill_capability_evidence(
    content: str,
    skill_name: str | None = None,
) -> tuple[SkillCapabilityEvidence, ...]:
    """Classify all recognizable capability occurrences in ``content``.

    Documentary occurrences are retained as ``artifact`` evidence so callers
    can explain why a declaration was rejected without treating it as genuine.
    """
    effective_skill_name = _normalize_skill_capability_name(content, skill_name)
    evidence_cache = _SKILL_CAPABILITY_EVIDENCE_CACHE
    scanner = _scan_skill_capability_evidence_uncached
    if len(content) + len(effective_skill_name) > evidence_cache.max_input_bytes:
        return scanner(content, effective_skill_name)

    input_weight_bytes = _skill_capability_evidence_input_weight_bytes(
        content,
        effective_skill_name,
    )
    if input_weight_bytes > evidence_cache.max_input_bytes:
        return scanner(content, effective_skill_name)

    hash(content)
    hash(effective_skill_name)
    key = (content, effective_skill_name)
    resident, state, is_builder = evidence_cache._lookup_or_register(key)
    if resident is not None:
        return resident
    if state is None:
        raise RuntimeError("Capability evidence cache returned no build state")
    if not is_builder:
        return evidence_cache._wait_for_build(key, state)

    try:
        result = scanner(content, effective_skill_name)
        completed_weight_bytes = _skill_capability_evidence_entry_weight_bytes(
            input_weight_bytes,
            result,
        )
    except BaseException as error:
        evidence_cache._publish_failure(key, state, error)
        raise
    return evidence_cache._complete_build(
        key,
        state,
        result,
        completed_weight_bytes,
    )


def detect_skill_capabilities(
    content: str,
    skill_name: str | None = None,
) -> frozenset[str]:
    """Return capabilities backed by genuine self-outbound executable evidence."""
    return frozenset(
        evidence.capability
        for evidence in classify_skill_capability_evidence(content, skill_name)
        if evidence.is_genuine
    )


def validate_skill_capability_declarations(
    body: str,
    skill_name: str,
    declared_capabilities: frozenset[str] | set[str] | tuple[str, ...] | list[str],
) -> SkillCapabilityValidation:
    """Compare declared capabilities with genuine evidence in both directions."""
    declared = frozenset(declared_capabilities)
    evidence = classify_skill_capability_evidence(body, skill_name)
    detected = frozenset(item.capability for item in evidence if item.is_genuine)
    return SkillCapabilityValidation(
        declared=declared,
        detected=detected,
        evidence=evidence,
        missing=detected - declared,
        unsupported=declared - detected,
    )


def validate_skill_capability_authenticity(
    skill_info: SkillInfo,
) -> tuple[str, ...]:
    """Return stable diagnostics for declaration/evidence mismatches."""
    validation = validate_skill_capability_declarations(
        skill_info.canonical_content,
        skill_info.name,
        skill_info.uses_capabilities,
    )
    diagnostics: list[str] = []
    for capability in sorted(validation.missing):
        genuine = next(
            item
            for item in validation.evidence
            if item.capability == capability and item.is_genuine
        )
        diagnostics.append(
            f"{skill_info.name}: missing declaration for {capability!r}; "
            f"lines {genuine.source_span[0]}-{genuine.source_span[1]}: "
            f"{genuine.source.strip()!r}"
        )
    for capability in sorted(validation.unsupported):
        artifact = next(
            (item for item in validation.evidence if item.capability == capability),
            None,
        )
        evidence_detail = (
            f"only artifact evidence at lines "
            f"{artifact.source_span[0]}-{artifact.source_span[1]}: "
            f"{artifact.source.strip()!r}"
            if artifact is not None
            else "no source span: no recognizable evidence"
        )
        diagnostics.append(
            f"{skill_info.name}: declaration {capability!r} lacks genuine evidence; "
            f"{evidence_detail}"
        )
    return tuple(diagnostics)


_RETIRED_SEMANTIC_CAPABILITIES: dict[str, str] = {
    "agent_model": "semantic_requirements.child_model_policies",
    "agent_subagent": "semantic_requirements.child_spawns",
    "cross_skill_ref": "semantic_requirements.sibling_skills",
    "git_metadata_write": "semantic_requirements.git_metadata_writes",
}
_RETIRED_SEMANTIC_DECLARATIONS: dict[str, str] = {
    **_RETIRED_SEMANTIC_CAPABILITIES,
    "backend_requirements": "backend selection outside skill declarations",
    "required_backends": "backend selection outside skill declarations",
}
_RAW_PORTABLE_TOKEN_REPLACEMENTS: tuple[tuple[str, str], ...] = (
    ("Agent(", "semantic_requirements.child_spawns"),
    ("Task(", "semantic_requirements.child_spawns"),
    ("spawn_agent", "semantic_requirements.child_spawns"),
    ("send_message", "semantic_requirements.join"),
    ("wait_agent", "semantic_requirements.join"),
    ("subagent_type=", "semantic_requirements.logical_roles"),
)
_SEMANTIC_REQUIREMENT_KEYS = frozenset(
    {
        "child_spawns",
        "concurrency",
        "join",
        "evidence",
        "child_model_policies",
        "logical_roles",
        "sibling_skills",
        "git_metadata_writes",
    }
)


def _semantic_body(content: str) -> str:
    if not content.startswith("---"):
        return content
    parts = content.split("---", maxsplit=2)
    return parts[2] if len(parts) == 3 else content


def _semantic_error(
    path: Path,
    *,
    schema_version: object,
    offending: str,
    replacement: str,
) -> str:
    return (
        f"{path}: skill semantic schema version {schema_version!r} rejects offending token "
        f"{offending!r}; replace with {replacement}"
    )


def _mapping_list(value: Any, field_name: str) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise SkillContractError(f"semantic_requirements.{field_name} must be a list of mappings")
    return value


def parse_skill_semantic_plan(
    data: dict[str, Any],
    *,
    path: Path,
    content: str,
    uses_capabilities: frozenset[str],
) -> tuple[SkillSemanticPlan | None, tuple[str, ...]]:
    """Parse one source declaration without granting it backend authority."""
    diagnostics: list[str] = []
    schema_version = data.get("semantic_version", SKILL_SEMANTIC_SCHEMA_VERSION)
    retired_caps = sorted(uses_capabilities & _RETIRED_SEMANTIC_CAPABILITIES.keys())
    for capability in retired_caps:
        diagnostics.append(
            _semantic_error(
                path,
                schema_version=schema_version,
                offending=capability,
                replacement=_RETIRED_SEMANTIC_CAPABILITIES[capability],
            )
        )

    body = _semantic_body(content)
    raw_tokens = (
        *_RAW_PORTABLE_TOKEN_REPLACEMENTS,
        *(
            (model_id, "semantic_requirements.child_model_policies.model_class")
            for model_id in sorted(CODEX_VALID_MODEL_IDS)
        ),
    )
    for token, replacement in raw_tokens:
        if token in body:
            diagnostics.append(
                _semantic_error(
                    path,
                    schema_version=schema_version,
                    offending=token,
                    replacement=replacement,
                )
            )

    has_declaration = "semantic_version" in data or "semantic_requirements" in data
    if not has_declaration:
        return None, tuple(diagnostics)
    if "semantic_version" not in data:
        diagnostics.append(
            _semantic_error(
                path,
                schema_version="missing",
                offending="semantic_requirements",
                replacement=f"semantic_version: {SKILL_SEMANTIC_SCHEMA_VERSION}",
            )
        )
        return None, tuple(diagnostics)
    if schema_version != SKILL_SEMANTIC_SCHEMA_VERSION:
        diagnostics.append(
            _semantic_error(
                path,
                schema_version=schema_version,
                offending=f"semantic_version: {schema_version}",
                replacement=f"semantic_version: {SKILL_SEMANTIC_SCHEMA_VERSION}",
            )
        )
        return None, tuple(diagnostics)

    raw_requirements = data.get("semantic_requirements", {})
    if not isinstance(raw_requirements, dict):
        diagnostics.append(
            _semantic_error(
                path,
                schema_version=schema_version,
                offending="semantic_requirements",
                replacement="a mapping of version-1 semantic requirement fields",
            )
        )
        return None, tuple(diagnostics)

    unknown = sorted(set(raw_requirements) - _SEMANTIC_REQUIREMENT_KEYS)
    for token in unknown:
        replacement = _RETIRED_SEMANTIC_DECLARATIONS.get(
            token, f"one of {sorted(_SEMANTIC_REQUIREMENT_KEYS)}"
        )
        diagnostics.append(
            _semantic_error(
                path,
                schema_version=schema_version,
                offending=token,
                replacement=replacement,
            )
        )
    if diagnostics:
        return None, tuple(diagnostics)

    try:
        logical_roles = tuple(
            LogicalRoleSpec(
                name=str(item.get("name", "")),
                purpose=str(item.get("purpose", "")),
            )
            for item in _mapping_list(raw_requirements.get("logical_roles", []), "logical_roles")
        )
        child_spawns = tuple(
            ChildSpawnSpec(role=str(item.get("role", "")), count=int(item.get("count", 1)))
            for item in _mapping_list(raw_requirements.get("child_spawns", []), "child_spawns")
        )
        child_model_policies = tuple(
            ChildModelPolicySpec(
                role=str(item.get("role", "")),
                model_class=(
                    str(item["model_class"]) if item.get("model_class") is not None else None
                ),
                reasoning_effort=(
                    str(item["reasoning_effort"])
                    if item.get("reasoning_effort") is not None
                    else None
                ),
            )
            for item in _mapping_list(
                raw_requirements.get("child_model_policies", []),
                "child_model_policies",
            )
        )
        sibling_skills = tuple(
            SiblingSkillSpec(name=str(item.get("name", "")))
            for item in _mapping_list(raw_requirements.get("sibling_skills", []), "sibling_skills")
        )
        git_metadata_writes = tuple(
            GitMetadataWriteSpec(purpose=str(item.get("purpose", "")))
            for item in _mapping_list(
                raw_requirements.get("git_metadata_writes", []),
                "git_metadata_writes",
            )
        )

        def optional_spec(field_name: str, spec_type: type[Any]) -> Any:
            raw = raw_requirements.get(field_name)
            if raw is None:
                return None
            if not isinstance(raw, dict):
                raise SkillContractError(f"semantic_requirements.{field_name} must be a mapping")
            return spec_type(**raw)

        plan = SkillSemanticPlan(
            schema_version=schema_version,
            child_spawns=child_spawns,
            concurrency=optional_spec("concurrency", ConcurrencySpec),
            join=optional_spec("join", JoinSpec),
            evidence=optional_spec("evidence", EvidenceSpec),
            child_model_policies=child_model_policies,
            logical_roles=logical_roles,
            sibling_skills=sibling_skills,
            git_metadata_writes=git_metadata_writes,
        )
    except (SkillContractError, TypeError, ValueError) as exc:
        diagnostics.append(
            _semantic_error(
                path,
                schema_version=schema_version,
                offending="semantic_requirements",
                replacement=f"a valid version-{SKILL_SEMANTIC_SCHEMA_VERSION} plan ({exc})",
            )
        )
        return None, tuple(diagnostics)
    return plan, ()


# Concise aliases for consumers that already carry skill-capability context.
classify_capability_evidence = classify_skill_capability_evidence
detect_capabilities = detect_skill_capabilities
validate_capability_declarations = validate_skill_capability_declarations


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
    "SkillCapabilityValidation",
    "classify_capability_evidence",
    "classify_skill_capability_evidence",
    "detect_capabilities",
    "detect_skill_capabilities",
    "parse_skill_semantic_plan",
    "validate_capability_declarations",
    "validate_skill_capability_authenticity",
    "validate_skill_capability_declarations",
]
