"""Meta-test: every InvariantDef must have a resolvable gate_target,
non-empty required fields, and a source_doc that still contains the
original prohibition prose anchor.
"""

from __future__ import annotations

import importlib.resources
import re
from pathlib import Path

import pytest

from autoskillit.core.types import INVARIANT_REGISTRY

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_SRC_ROOT = _PROJECT_ROOT / "src" / "autoskillit"


def _resolve_gate_target(target: str) -> bool:
    if "/" in target or target.endswith(".py"):
        pkg = Path(str(importlib.resources.files("autoskillit")))
        if (pkg / target).is_file():
            return True
        if (pkg / "hooks" / target).is_file():
            return True
        return False
    if "." in target:
        try:
            importlib.import_module(target)
            return True
        except ImportError:
            return False
    try:
        importlib.import_module(target)
        return True
    except ImportError:
        return False


def _extract_prose_anchor(prohibition: str) -> str:
    # 1. Parenthetical content — first item
    m = re.search(r"\(([^)]+)\)", prohibition)
    if m:
        first_item = m.group(1).split(",")[0].strip().strip("`")
        if len(first_item) > 2:
            return first_item

    # 2. CLI flag (--word)
    m = re.search(r"(--\w+)", prohibition)
    if m:
        return m.group(1)

    # 3. Underscore identifier
    m = re.search(r"(\w+_\w+)", prohibition)
    if m:
        return m.group(1)

    # 4. Slash-separated path
    m = re.search(r"(\w+/\w+)", prohibition)
    if m:
        return m.group(1)

    # 5. Fallback: extract subject before verb, take last 2+ words.
    #    When the second-to-last word ends with ":" (a YAML field token like "cmd:"),
    #    return that token alone — it is more precise than "token: next_word".
    for verb in (
        "is prohibited",
        "are prohibited",
        "is blocked",
        "are blocked",
        "are banned",
        "cannot be called",
        "cannot be",
    ):
        idx = prohibition.lower().find(verb)
        if idx > 0:
            subject = prohibition[:idx].strip()
            words = subject.split()
            if len(words) >= 2:
                tail = words[-2:]
                if tail[0].endswith(":"):
                    return tail[0]
                return " ".join(tail)
            return subject

    return prohibition[:30]


def _collect_source_doc_files(source_doc: str) -> list[Path]:
    if "/" in source_doc:
        return [_PROJECT_ROOT / source_doc]
    if source_doc == "AGENTS.md":
        candidates = [_PROJECT_ROOT / "AGENTS.md"]
        candidates.extend(p for p in _SRC_ROOT.rglob("AGENTS.md") if "__pycache__" not in str(p))
        return candidates
    if source_doc == "SKILL.md":
        candidates = []
        for subdir in ("skills", "skills_extended"):
            d = _SRC_ROOT / subdir
            if d.is_dir():
                candidates.extend(p for p in d.rglob("SKILL.md") if "__pycache__" not in str(p))
        # Guard prohibitions that map to source_doc="SKILL.md" are also
        # documented in AGENTS.md files (e.g. hooks/guards/AGENTS.md).
        candidates.extend(p for p in _SRC_ROOT.rglob("AGENTS.md") if "__pycache__" not in str(p))
        return candidates
    return [_PROJECT_ROOT / source_doc]


def test_every_gate_target_resolves():
    for key, inv in INVARIANT_REGISTRY.items():
        assert _resolve_gate_target(inv.gate_target), (
            f"{key}: gate_target {inv.gate_target!r} "
            "does not resolve to an existing file or module"
        )


def test_every_prohibition_appears_in_registry():
    assert INVARIANT_REGISTRY, "INVARIANT_REGISTRY must not be empty"
    for key, inv in INVARIANT_REGISTRY.items():
        assert inv.id, f"{key}: id is empty"
        assert inv.prohibition, f"{key}: prohibition is empty"
        assert inv.source_doc, f"{key}: source_doc is empty"
        assert inv.gate_target, f"{key}: gate_target is empty"
        assert inv.enforcement_layer, f"{key}: enforcement_layer is empty"
        assert inv.backends, f"{key}: backends is empty"


def test_no_orphaned_registry_entries():
    for key, inv in INVARIANT_REGISTRY.items():
        candidates = _collect_source_doc_files(inv.source_doc)
        existing = [p for p in candidates if p.is_file()]
        assert existing, f"{key}: source_doc {inv.source_doc!r} has no matching files on disk"

        combined = "\n".join(p.read_text(encoding="utf-8") for p in existing)
        anchor = _extract_prose_anchor(inv.prohibition)
        assert anchor.lower() in combined.lower(), (
            f"{key}: prose anchor {anchor!r} (from prohibition "
            f"{inv.prohibition!r}) not found in any {inv.source_doc} file"
        )
