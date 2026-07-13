"""Contract: has_implementation_progress must NOT appear as an emit line in any SKILL.md.

The token is server-computed via SkillResult.has_implementation_progress
(src/autoskillit/core/types/_type_results.py:526) — it is never parsed from
model text. Including it as an emit line in SKILL.md misleads models into
self-reporting progress as authoritative. This test prevents re-introduction.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest

from autoskillit.core.paths import pkg_root

pytestmark = [pytest.mark.small]

_REQUIRED_ALLOW_LIST_ENTRY = "has_implementation_progress"


def _skills_extended_dir() -> Path:
    return pkg_root() / "skills_extended"


def _load_emit_consistency_module():
    """Load test_skill_emit_consistency.py as a module without it being part of a package."""
    import sys

    tests_root = pkg_root().parents[1] / "tests"
    candidate = tests_root / "recipe" / "test_skill_emit_consistency.py"
    spec = importlib.util.spec_from_file_location("test_skill_emit_consistency", candidate)
    if spec is None or spec.loader is None:
        pytest.skip(f"Could not locate test_skill_emit_consistency at {candidate}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_no_skill_md_contains_has_implementation_progress_token() -> None:
    """No extended SKILL.md may emit has_implementation_progress."""
    token_pattern = re.compile(r"^has_implementation_progress\s*=", re.MULTILINE)

    violations: list[str] = []
    for skill_md in _skills_extended_dir().glob("*/SKILL.md"):
        content = skill_md.read_text()
        if token_pattern.search(content):
            violations.append(str(skill_md.relative_to(pkg_root())))

    assert not violations, (
        "has_implementation_progress must not be emitted in SKILL.md — "
        "the value is server-computed via SkillResult.has_implementation_progress. "
        f"Found emit lines in: {violations}"
    )


def test_server_computed_outputs_allow_list_exempts_has_implementation_progress() -> None:
    """The allow-list must exempt has_implementation_progress."""
    module = _load_emit_consistency_module()
    allow_list = getattr(module, "SERVER_COMPUTED_OUTPUTS", None)
    assert allow_list is not None, (
        "test_skill_emit_consistency.py must define SERVER_COMPUTED_OUTPUTS allow-list"
    )
    assert _REQUIRED_ALLOW_LIST_ENTRY in allow_list, (
        f"SERVER_COMPUTED_OUTPUTS must include {_REQUIRED_ALLOW_LIST_ENTRY!r} "
        f"so the canonical emit-line guard skips server-computed outputs. "
        f"Current allow-list: {sorted(allow_list)}"
    )
