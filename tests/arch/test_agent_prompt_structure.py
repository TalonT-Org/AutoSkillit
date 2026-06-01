"""Structural validation: agent definition MUST-gate fallback, quote-verification gates,

inconclusive output format, and empty-array documentation.
"""

import re

from autoskillit.core import pkg_root

_AGENTS_DIR = pkg_root() / "agents"

_MUST_PROCESS_VERBS = re.compile(
    r"\bMUST\b.{0,60}\b(simulate|execute|trace|verify|validate)\b",
    re.IGNORECASE,
)
_INCONCLUSIVE_PATTERNS = re.compile(
    r"\b(inconclusive|uncertain|unable|cannot|can't|may not be able)\b",
    re.IGNORECASE,
)
_VERIFICATION_GATE_PATTERNS = re.compile(
    r"\b(apply this verification|before reporting|before including"
    r"|before flagging|Phase 2.*Senior filter|survive Phase 2)\b",
    re.IGNORECASE,
)
_QUOTE_VERIFICATION = re.compile(
    r"\b(quote|verbatim|re-read|reread|cite the exact|copy the actual)\b",
    re.IGNORECASE,
)
_INCONCLUSIVE_SECTION_HEADING = re.compile(
    r"##\s+When.*Inconclusive|Inconclusive\s+check|Inconclusive\s+trace",
    re.IGNORECASE,
)
_INCONCLUSIVE_OUTPUT_FORMAT = re.compile(
    r"\[INCONCLUSIVE|severity.*info|confidence.*low|prefix.*inconclusive",
    re.IGNORECASE,
)
_JSON_ARRAY_OUTPUT = re.compile(r"```json\s*\[", re.DOTALL)
_EMPTY_ARRAY_DOC = re.compile(
    r"\[\s*\]|\bempty\b.*\barray\b|\barray\b.*\bempty\b|\bno findings\b|\bzero findings\b",
    re.IGNORECASE,
)


def test_agent_definitions_with_must_gate_have_fallback() -> None:
    """Agent definitions with MUST+process-verb gates must document inconclusive outcomes."""
    failures: list[str] = []
    for md_file in sorted(_AGENTS_DIR.glob("*.md")):
        if md_file.name == "CLAUDE.md":
            continue
        content = md_file.read_text()
        parts = content.split("---", 2)
        body = parts[2] if len(parts) >= 3 else content
        if _MUST_PROCESS_VERBS.search(body):
            if not _INCONCLUSIVE_PATTERNS.search(body):
                failures.append(
                    f"{md_file.name}: MUST+process-verb gate with no inconclusive fallback"
                )
    assert not failures, "\n".join(failures)


def test_pr_review_auditor_verification_gates_have_fallback() -> None:
    """PR review auditor agents with verification gates must document inconclusive outcomes."""
    failures: list[str] = []
    for md_file in sorted(_AGENTS_DIR.glob("pr-review-auditor-*.md")):
        content = md_file.read_text()
        parts = content.split("---", 2)
        body = parts[2] if len(parts) >= 3 else content
        has_gate = _MUST_PROCESS_VERBS.search(body) or _VERIFICATION_GATE_PATTERNS.search(body)
        if has_gate and not _INCONCLUSIVE_PATTERNS.search(body):
            failures.append(f"{md_file.name}: verification gate with no inconclusive fallback")
    assert not failures, "\n".join(failures)


def test_agent_definitions_with_gates_have_quote_verification() -> None:
    """Agent definitions with verification gates must require quoting source text."""
    failures: list[str] = []
    for md_file in sorted(_AGENTS_DIR.glob("pr-review-auditor-*.md")):
        content = md_file.read_text()
        parts = content.split("---", 2)
        body = parts[2] if len(parts) >= 3 else content
        has_gate = _MUST_PROCESS_VERBS.search(body) or _VERIFICATION_GATE_PATTERNS.search(body)
        if has_gate and not _QUOTE_VERIFICATION.search(body):
            failures.append(f"{md_file.name}: verification gate with no quote/re-read requirement")
    assert not failures, "\n".join(failures)


def test_agent_definitions_inconclusive_specifies_output_format() -> None:
    """Inconclusive fallback sections must specify how inconclusive findings appear in output."""
    failures: list[str] = []
    for md_file in sorted(_AGENTS_DIR.glob("pr-review-auditor-*.md")):
        content = md_file.read_text()
        parts = content.split("---", 2)
        body = parts[2] if len(parts) >= 3 else content
        if _INCONCLUSIVE_SECTION_HEADING.search(body):
            if not _INCONCLUSIVE_OUTPUT_FORMAT.search(body):
                failures.append(
                    f"{md_file.name}: inconclusive fallback without output format specification"
                )
    assert not failures, "\n".join(failures)


def test_agent_definitions_output_format_mentions_empty_array() -> None:
    """Every agent definition that specifies JSON array output must document what [] means."""
    failures: list[str] = []
    for md_file in sorted(_AGENTS_DIR.glob("*.md")):
        if md_file.name == "CLAUDE.md":
            continue
        content = md_file.read_text()
        parts = content.split("---", 2)
        body = parts[2] if len(parts) >= 3 else content
        if _JSON_ARRAY_OUTPUT.search(body):
            if not _EMPTY_ARRAY_DOC.search(body):
                failures.append(
                    f"{md_file.name}: JSON array output but does not document what [] means"
                )
    assert not failures, "\n".join(failures)
