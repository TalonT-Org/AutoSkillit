"""Deterministic, deliberately narrow requirement extraction from issue Markdown."""

from __future__ import annotations

from dataclasses import dataclass

import regex as re

from ..closure_hashing import compute_bytes_hash
from ..types._type_plan_set_authority import InventoryMode, RequirementDef, RequirementKind

__all__ = ["InventoryExtraction", "extract_requirement_inventory"]

_SCOPED_HEADING = re.compile(
    r"^(#{1,6})\s+(hard\s+)?(requirements?|required\s+outcomes?|remediation(?:\s+items)?|items)\s*$",
    re.IGNORECASE,
)
_ANY_HEADING = re.compile(r"^(#{1,6})\s+")
_ORDERED = re.compile(r"^\s*(\d+)[.)]\s+(.*)$")
_NESTED_ORDERED = re.compile(r"^\s{2,}([a-z]|\d+)[.)]\s+(.*)$", re.IGNORECASE)
_CHECKBOX = re.compile(r"^\s*[-*]\s+\[[ xX]\]\s+(.*)$")
_BULLET = re.compile(r"^\s*[-*]\s+")
_INLINE_CHILD = re.compile(r"\((\d+[a-z])\)\s*", re.IGNORECASE)
_LABEL = r"(?:R\d+|REQ-[A-Z]+-\d{3})"
_LIST_LABEL = re.compile(
    rf"^\s*[-*]\s+(?:\*\*)?({_LABEL})(?:\*\*)?(?=\s*(?::|\||-|$))\s*(?::|\||-)?\s*(.*)$",
    re.IGNORECASE,
)
_TABLE_LABEL = re.compile(
    rf"^\s*\|\s*(?:\*\*)?({_LABEL})(?:\*\*)?(?=\s*\|)\s*\|\s*(.*)$",
    re.IGNORECASE,
)
_HEADING_LABEL = re.compile(
    rf"^\s*#{1, 6}\s+(?:\*\*)?({_LABEL})(?:\*\*)?(?=\s*(?::|-|$))\s*(?::|-)?\s*(.*)$",
    re.IGNORECASE,
)
_MALFORMED_LABEL = re.compile(r"\b(?:R\d+|REQ-[A-Z]+-\d{3})(?:\([^)]*\)|[-A-Z0-9])", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class InventoryExtraction:
    mode: InventoryMode
    requirements: tuple[RequirementDef, ...]
    unparsed_marker_lines: tuple[int, ...]


def _normalise(text: str) -> str:
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"\*{1,3}([^*]+)\*{1,3}", r"\1", text)
    return " ".join(text.split())


def _append_requirement(
    found: list[RequirementDef],
    seen: set[str],
    *,
    label: str,
    kind: RequirementKind,
    parent_label: str | None,
    text: str,
    source_line: int,
    raw: str,
) -> None:
    if label in seen:
        return
    seen.add(label)
    found.append(
        RequirementDef(
            requirement_id=label,
            label=label,
            kind=kind,
            parent_label=parent_label,
            text=_normalise(text),
            source_line=source_line,
            text_digest=compute_bytes_hash(raw.encode("utf-8")),
        )
    )


def _span(lines: list[str], index: int) -> tuple[str, str, int]:
    """Return marker text, exact raw item span, and the next unconsumed index."""
    marker = lines[index]
    values = [marker]
    next_index = index + 1
    while next_index < len(lines):
        next_line = lines[next_index]
        if not next_line.strip() or _ANY_HEADING.match(next_line) or not next_line[:1].isspace():
            break
        if _NESTED_ORDERED.match(next_line):
            break
        values.append(next_line)
        next_index += 1
    return marker, "".join(values), next_index


def _label_match(line: str) -> tuple[str, str] | None:
    for expression in (_LIST_LABEL, _TABLE_LABEL, _HEADING_LABEL):
        matched = expression.match(line)
        if matched:
            return matched.group(1).upper(), matched.group(2)
    return None


def extract_requirement_inventory(
    issue_markdown: str,
    *,
    issue_number: int | None,
) -> InventoryExtraction:
    """Extract only grammar this authority can prove; record unsupported markers."""
    if issue_number is None:
        return InventoryExtraction(InventoryMode.NO_ISSUE, (), ())

    lines = issue_markdown.splitlines(keepends=True)
    requirements: list[RequirementDef] = []
    seen: set[str] = set()
    unparsed: list[int] = []
    scoped_level: int | None = None
    saw_scoped_heading = False
    checkbox_index = 0
    parent_label: str | None = None
    index = 0

    while index < len(lines):
        line = lines[index]
        heading = _ANY_HEADING.match(line)
        scoped = _SCOPED_HEADING.match(line)
        if scoped:
            scoped_level = len(scoped.group(1))
            saw_scoped_heading = True
            parent_label = None
            index += 1
            continue
        if heading and scoped_level is not None and len(heading.group(1)) <= scoped_level:
            scoped_level = None
            parent_label = None

        label_match = _label_match(line)
        if label_match is not None:
            label, text = label_match
            _append_requirement(
                requirements,
                seen,
                label=label,
                kind=RequirementKind.ITEM,
                parent_label=None,
                text=text,
                source_line=index + 1,
                raw=line,
            )
            index += 1
            continue

        if scoped_level is None:
            index += 1
            continue

        nested = _NESTED_ORDERED.match(line)
        if nested and parent_label is not None:
            suffix, text = nested.groups()
            label = f"{parent_label}{suffix.lower() if suffix.isalpha() else f'.{suffix}'}"
            _, raw, next_index = _span(lines, index)
            _append_requirement(
                requirements,
                seen,
                label=label,
                kind=RequirementKind.ITEM,
                parent_label=parent_label,
                text=text,
                source_line=index + 1,
                raw=raw,
            )
            index = next_index
            continue

        ordered = _ORDERED.match(line)
        if ordered:
            label, text = ordered.groups()
            marker, raw, next_index = _span(lines, index)
            inline_children = tuple(_INLINE_CHILD.finditer(text))
            if inline_children:
                preamble = text[: inline_children[0].start()].strip()
                continuation = " ".join(
                    candidate.strip() for candidate in raw.splitlines()[1:] if candidate.strip()
                )
                _append_requirement(
                    requirements,
                    seen,
                    label=label,
                    kind=RequirementKind.CONTAINER,
                    parent_label=None,
                    text=preamble,
                    source_line=index + 1,
                    raw=raw,
                )
                for child_index, child in enumerate(inline_children):
                    end = (
                        inline_children[child_index + 1].start()
                        if child_index + 1 < len(inline_children)
                        else len(text)
                    )
                    child_label = child.group(1).lower()
                    child_text = text[child.end() : end]
                    if child_index == len(inline_children) - 1 and continuation:
                        child_text = f"{child_text} {continuation}"
                    _append_requirement(
                        requirements,
                        seen,
                        label=child_label,
                        kind=RequirementKind.ITEM,
                        parent_label=label,
                        text=child_text,
                        source_line=index + 1,
                        raw=raw,
                    )
            else:
                _append_requirement(
                    requirements,
                    seen,
                    label=label,
                    kind=RequirementKind.ITEM,
                    parent_label=None,
                    text=text,
                    source_line=index + 1,
                    raw=raw,
                )
            parent_label = label
            index = next_index
            continue

        checkbox = _CHECKBOX.match(line)
        if checkbox:
            checkbox_index += 1
            _append_requirement(
                requirements,
                seen,
                label=f"cb-{checkbox_index}",
                kind=RequirementKind.ITEM,
                parent_label=None,
                text=checkbox.group(1),
                source_line=index + 1,
                raw=line,
            )
        elif _BULLET.match(line) or _MALFORMED_LABEL.search(line):
            unparsed.append(index + 1)
        index += 1

    return InventoryExtraction(
        InventoryMode.ENUMERATED
        if requirements or saw_scoped_heading
        else InventoryMode.UNENUMERATED,
        tuple(requirements),
        tuple(unparsed),
    )
