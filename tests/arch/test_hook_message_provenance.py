"""Standing enforcement for ADR-0006's Provenance Rule.

"Every residual hook message uses a typed policy event rendered by a shared
formatter." No string literal carrying the AutoSkillit provenance token —
bracketed or bare — may exist outside hooks/_policy_event.py in the capture
cleanup/control diagnostic call chain; every such message must be built via
PolicyEvent + render_provenance_prefix instead.
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

# A shell-embedded emergency literal baked into the generated harness itself
# runs inside the wrapped command, not as a hook policy/diagnostic message.
_EXEMPT_LITERALS = frozenset(
    {
        "AutoSkillit shell capture request rejected before execution",
        # Pre-existing non-capture control messages remain exact-value
        # exemptions; scanning the full hook tree still rejects any new or
        # altered branded literal outside the typed policy-event formatter.
        "Use post_pr_review for pull-request review publication, or the appropriate "
        "structured AutoSkillit mutation tool.",
        "AutoSkillit MCP server appears disconnected — all registered server PIDs "
        "for this project are dead. Kitchen state has been lost. Ask the user to run "
        "/MCP to reconnect, then re-open the kitchen with open_kitchen.",
        "RESUME REMINDER: You are resuming a previous AutoSkillit session. MCP tool "
        "access (kitchen) is not automatically restored on resume. ",
        "Call /autoskillit:open-kitchen first to regain access to all AutoSkillit MCP "
        "tools before continuing your work.",
        "') to regain access to all AutoSkillit MCP tools before continuing your work.",
        # Stop completion gate docstring describes the platform's success/completion
        # marker by name; the gate itself emits PolicyEvent-rendered messages.
        "Stop completion gate — block success/Stop until the active wave is complete.\n\n"
        "When the session flag (or ``AUTOSKILLIT_JOIN_REQUIRED=1``) reports\n"
        "``join_required=true``, the Stop event may only release Claude when the\n"
        "ledger shows a fully-complete wave. Partial, failed, cancelled,\n"
        "interrupted, missing, or unresolved waves block Stop with a\n"
        "deterministic reason so the existing AutoSkillit success/completion marker\n"
        "cannot be emitted prematurely.\n\n"
        "In a clean session (no join-bearing skill loaded) this guard is a no-op.\n\n"
        "``Stop`` is the correct gate surface — per official documentation it\n"
        "fires once per turn and exit code 2 prevents Claude from stopping while\n"
        "continuing the conversation. This blocks premature completion between\n"
        "waves as well as at the end of the whole conversation.\n\n"
        "Stdlib-only — no autoskillit imports.\n",
    }
)


def _string_literals(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    ]


def _imports_policy_event(path: Path) -> bool:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import) and any(
            alias.name.rsplit(".", 1)[-1] == "_policy_event" for alias in node.names
        ):
            return True
        if isinstance(node, ast.ImportFrom):
            if node.module and node.module.rsplit(".", 1)[-1] == "_policy_event":
                return True
            if any(alias.name == "_policy_event" for alias in node.names):
                return True
    return False


def _policy_event_importers() -> tuple[Path, ...]:
    return tuple(
        path
        for path in sorted(_HOOKS_DIR.rglob("*.py"))
        if path.name != "_policy_event.py" and _imports_policy_event(path)
    )


def _provenance_scan_paths() -> tuple[Path, ...]:
    return tuple(
        path
        for path in sorted(_HOOKS_DIR.rglob("*.py"))
        if path.name not in {"__init__.py", "_policy_event.py"}
    )


def test_no_ad_hoc_provenance_literal_outside_policy_event() -> None:
    violations: list[str] = []
    for path in _provenance_scan_paths():
        offending = [
            literal
            for literal in _string_literals(path)
            if _PROVENANCE_TOKEN_RE.search(literal) and literal not in _EXEMPT_LITERALS
        ]
        if offending:
            violations.append(f"{path.relative_to(_REPO_ROOT)}: {offending!r}")
    assert not violations, (
        "production hook sources contain raw AutoSkillit-branded "
        "literals outside hooks/_policy_event.py — construct these via "
        "PolicyEvent + render_provenance_prefix instead:\n" + "\n".join(violations)
    )


def test_exempt_literals_are_still_present_and_narrow() -> None:
    """The exemption list must track real content, not accumulate stale entries."""
    all_literals = {
        literal
        for path in _provenance_scan_paths()
        for literal in _string_literals(path)
        if _PROVENANCE_TOKEN_RE.search(literal)
    }
    stale = _EXEMPT_LITERALS - all_literals
    assert not stale, f"stale provenance-scan exemptions no longer present in source: {stale!r}"


def test_policy_event_module_has_a_production_importer() -> None:
    """hooks/_policy_event.py must not be an orphaned component."""
    importers = _policy_event_importers()
    assert importers, "hooks/_policy_event.py has zero production importers"


def test_policy_event_comment_is_not_an_import(tmp_path: Path) -> None:
    source = tmp_path / "comment_only.py"
    source.write_text("# _policy_event is not imported here\n", encoding="utf-8")
    assert not _imports_policy_event(source)
