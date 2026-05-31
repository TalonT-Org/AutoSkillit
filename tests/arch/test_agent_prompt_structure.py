"""Structural validation: agent definition MUST-gate fallback and empty-array documentation."""

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
