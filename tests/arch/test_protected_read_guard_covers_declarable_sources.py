"""Keep protected runtime reads and declarable prose sources in lockstep."""

from __future__ import annotations

import re
from collections.abc import Sequence

import pytest

from autoskillit.hooks._command_classification import (
    DECLARABLE_SOURCE_PATH_PATTERNS,
    PROTECTED_SOURCE_PATH_PATTERNS,
)

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]


def _pattern_texts(patterns: Sequence[re.Pattern[str]]) -> set[str]:
    return {pattern.pattern for pattern in patterns}


def test_protected_reads_and_declarable_source_patterns_cover_the_same_paths() -> None:
    """A source the runtime denies must also be forbidden in authored prose."""
    protected = _pattern_texts(PROTECTED_SOURCE_PATH_PATTERNS)
    declarable = _pattern_texts(DECLARABLE_SOURCE_PATH_PATTERNS)

    assert protected, "protected source-path patterns must not be empty"
    assert declarable, "declarable source-path patterns must not be empty"
    assert protected == declarable, (
        "Runtime protected-read patterns and prose-declarable source patterns drifted:\n"
        f"  denied but authorable: {sorted(protected - declarable)}\n"
        f"  forbidden in prose but allowed at runtime: {sorted(declarable - protected)}"
    )
