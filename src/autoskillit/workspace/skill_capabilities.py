"""Semantic capability evidence classification for skill documents.

Capability declarations are an execution contract, not a keyword inventory.
This module therefore classifies command evidence by actor, direction, and
whether the source is executable instruction or merely documentary artifact.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

import regex as re

from autoskillit.core import SKILL_CAPABILITY_REGISTRY

if TYPE_CHECKING:
    from autoskillit.workspace.skills import SkillInfo

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
class _SourceLine:
    number: int
    text: str
    executable: bool


_STATIC_PATTERNS: dict[str, tuple[re.Pattern[str], ...]] = {
    "agent_subagent": (re.compile(r"Agent\(\s*subagent_type\s*="),),
    "agent_model": (re.compile(r"Agent\(\s*model\s*="),),
    "claude_dir": (re.compile(r"\.claude/"),),
    "commit_files": (re.compile(r"\bcommit_files\s*\("),),
    "git_metadata_write": (
        re.compile(r"create_impl_worktree\.sh|git worktree add\b[ \t]+\S|git checkout -b"),
        re.compile(r"git\s+(?:-C\s+\S+\s+)?commit\s+-m"),
        re.compile(r'\bgit\s+(?:-C\s+\S+\s+)?rebase\s+(?:--\w|[$"\{])'),
    ),
    "github_api_write": (
        re.compile(
            r"gh api[^\n]*(?:--method\s+(?:POST|PATCH|PUT|DELETE))"
            r"|gh pr (?:review|create|merge)\b"
            r"|gh issue (?:create|edit|close)\b"
            r"|gh release create\b"
        ),
    ),
}

_SELF_INITIATED_TOOLS: dict[str, tuple[str, ...]] = {
    "open_kitchen": ("open_kitchen", "close_kitchen"),
    "run_skill": ("run_skill",),
    "test_check": ("test_check",),
}

_STEP_HEADING_RE = re.compile(r"(?:Step\s+\d|^\d+[\.\):\s])")
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
    r"\b\w+\.(?:command|commands)\b|\bfrontmatter\b)",
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
    match = re.search(r"^name:\s*['\"]?([^'\"\n]+)", content, re.MULTILINE)
    return match.group(1).strip() if match else ""


def _source_lines(body: str) -> tuple[_SourceLine, ...]:
    """Mark lines in frontmatter and documentary fences as non-executable."""
    result: list[_SourceLine] = []
    in_frontmatter = body.startswith("---\n")
    frontmatter_closed = not in_frontmatter
    in_fence = False
    in_step_section = False
    artifact_section = False

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

        if stripped.startswith("#") and not in_fence:
            heading = stripped.lstrip("#").strip()
            heading_level = len(stripped) - len(stripped.lstrip("#"))
            is_step_heading = bool(_STEP_HEADING_RE.match(heading))
            if is_step_heading or heading_level <= 3:
                in_step_section = is_step_heading
            artifact_section = bool(_ARTIFACT_HEADING_RE.search(heading))

        if stripped.startswith("```"):
            was_in_fence = in_fence
            in_fence = not in_fence
            executable = in_step_section and not artifact_section
            result.append(_SourceLine(number, text, executable))
            if was_in_fence:
                in_fence = False
            continue

        executable = not (in_fence and (not in_step_section or artifact_section))
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


def _has_tool_operation(text: str, tool_name: str) -> bool:
    tool = re.escape(tool_name)
    direct_call = re.compile(rf"\b{tool}\s*\(")
    imperative = re.compile(
        rf"\b(?:call|run|invoke|use|execute|retry|re-run|test it)\b"
        rf"[^\n]{{0,100}}\b{tool}\b",
        re.IGNORECASE,
    )
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
    has_skill_word = bool(re.search(r"(?:\bskill\b|/skill|skill\s*`)", lower))
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
    if re.search(r"\b(?:run_skill|Skill)\s*\(\s*['\"]/autoskillit:", stripped):
        return True
    if _has_imperative_cross_skill_invocation(stripped):
        return True
    if _has_slash_command_invocation(stripped):
        return True
    return "run_skill" in lower and "/autoskillit:" in lower


def classify_skill_capability_evidence(
    content: str,
    skill_name: str | None = None,
) -> tuple[SkillCapabilityEvidence, ...]:
    """Classify all recognizable capability occurrences in ``content``.

    Documentary occurrences are retained as ``artifact`` evidence so callers
    can explain why a declaration was rejected without treating it as genuine.
    """
    effective_skill_name = skill_name or _frontmatter_skill_name(content)
    lines = _source_lines(content)
    found: list[SkillCapabilityEvidence] = []
    seen: set[tuple[str, tuple[int, int], str]] = set()

    def add(capability: str, source_lines: tuple[_SourceLine, ...]) -> None:
        item = _evidence(capability, source_lines)
        key = (item.capability, item.source_span, item.source)
        if key not in seen:
            found.append(item)
            seen.add(key)

    excluded_cross_skill_section = False
    previous_logical: tuple[_SourceLine, ...] | None = None
    previous_text = ""
    for logical in _logical_lines(lines):
        text = re.sub(r"\\\s*\n\s*", " ", "\n".join(line.text for line in logical))
        stripped = text.strip()
        if stripped.startswith("#"):
            heading = stripped.lstrip("#").strip().lower()
            excluded_cross_skill_section = heading in _EXCLUDED_SECTION_HEADINGS

        for capability, patterns in _STATIC_PATTERNS.items():
            if any(pattern.search(text) for pattern in patterns):
                add(capability, logical)

        for capability, tool_names in _SELF_INITIATED_TOOLS.items():
            if any(_has_tool_operation(text, tool_name) for tool_name in tool_names):
                add(capability, logical)

        if not excluded_cross_skill_section and _is_cross_skill_ref(text, effective_skill_name):
            add("cross_skill_ref", logical)
        if (
            not excluded_cross_skill_section
            and previous_logical is not None
            and re.match(r"^`?/autoskillit:", stripped)
            and "skill tool" in previous_text.lower()
            and any(verb in previous_text.lower() for verb in ("load", "call", "use", "invoke"))
        ):
            add("cross_skill_ref", previous_logical + logical)
        previous_logical = logical
        previous_text = text

    executable_text = "\n".join(line.text for line in lines if line.executable)
    graphql_lines = tuple(
        line
        for line in lines
        if re.search(r"\b(?:gh\s+api\s+graphql|mutation)\b", line.text, re.IGNORECASE)
    )
    if (
        re.search(r"\bgh\s+api\s+graphql\b", executable_text)
        and re.search(r"\bmutation\b", executable_text, re.IGNORECASE)
        and graphql_lines
    ):
        add("github_api_write", graphql_lines)

    return tuple(sorted(found, key=lambda item: (item.source_span, item.capability)))


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


# Concise aliases for consumers that already carry skill-capability context.
classify_capability_evidence = classify_skill_capability_evidence
detect_capabilities = detect_skill_capabilities
validate_capability_declarations = validate_skill_capability_declarations


_CLASSIFIED_CAPABILITIES = (
    frozenset(_STATIC_PATTERNS) | frozenset(_SELF_INITIATED_TOOLS) | {"cross_skill_ref"}
)
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
    "validate_capability_declarations",
    "validate_skill_capability_authenticity",
    "validate_skill_capability_declarations",
]
