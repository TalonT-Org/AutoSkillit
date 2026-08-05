"""Standing enforcement for ADR-0006's Provenance Rule.

"Every residual hook message uses a typed policy event rendered by a shared
formatter." No string literal carrying the AutoSkillit provenance token —
bracketed or bare — may exist outside hooks/_policy_event.py in the capture
cleanup/control diagnostic call chain; every such message must be built via
PolicyEvent + render_provenance_prefix instead. This is what permanently
blocks regression to the ad-hoc "cleanup failed"/"managed launch controls
missing" wording this rule replaced.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

_REPO_ROOT = Path(__file__).resolve().parents[2]
_HOOKS_DIR = _REPO_ROOT / "src" / "autoskillit" / "hooks"
_PROVENANCE_TOKEN_RE = re.compile(r"AutoSkillit")

_SCANNED_FILES = (
    _HOOKS_DIR / "_capture" / "_reconcile.py",
    _HOOKS_DIR / "shell_capture_hook.py",
    _HOOKS_DIR / "capture_lifecycle_hook.py",
)

# Pre-existing, out-of-scope for this rule: a shell-embedded emergency
# literal baked into the generated harness itself (runs via `printf ... >&2`
# inside the *wrapped command*, not emitted as a hook policy/diagnostic
# message by the hook process) — not one of the four sites this rule closed.
_EXEMPT_LITERALS = frozenset(
    {
        "AutoSkillit shell capture request rejected before execution",
    }
)


def _string_literals(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    ]


@pytest.mark.parametrize("path", _SCANNED_FILES, ids=lambda p: p.name)
def test_no_ad_hoc_provenance_literal_outside_policy_event(path: Path) -> None:
    offending = [
        literal
        for literal in _string_literals(path)
        if _PROVENANCE_TOKEN_RE.search(literal) and literal not in _EXEMPT_LITERALS
    ]
    assert not offending, (
        f"{path.relative_to(_REPO_ROOT)} contains raw AutoSkillit-branded string "
        f"literal(s) outside hooks/_policy_event.py: {offending!r} — construct these "
        "via PolicyEvent + render_provenance_prefix instead"
    )


def test_exempt_literals_are_still_present_and_narrow() -> None:
    """The exemption list must track real content, not accumulate stale entries."""
    all_literals = {
        literal
        for path in _SCANNED_FILES
        for literal in _string_literals(path)
        if _PROVENANCE_TOKEN_RE.search(literal)
    }
    stale = _EXEMPT_LITERALS - all_literals
    assert not stale, f"stale provenance-scan exemptions no longer present in source: {stale!r}"


def test_policy_event_module_has_a_production_importer() -> None:
    """hooks/_policy_event.py must not be an orphaned component."""
    importer_re = re.compile(r"\b_policy_event\b")
    importers = [
        path
        for path in _HOOKS_DIR.rglob("*.py")
        if path.name != "_policy_event.py" and importer_re.search(path.read_text(encoding="utf-8"))
    ]
    assert importers, "hooks/_policy_event.py has zero production importers"
