"""Guards for resolve-failures SKILL.md: polling-cascade and output-bloat fixes."""
from __future__ import annotations

import re
from pathlib import Path

from autoskillit.core.paths import pkg_root

SKILL_MD = pkg_root() / "skills_extended" / "resolve-failures" / "SKILL.md"


def _skill_text() -> str:
    return SKILL_MD.read_text()


def _extract_step(text: str, step_id: str) -> str:
    """Extract the text of a named Step section up to the next Step heading.

    The lookahead ``(?=[:\\s\\n])`` ensures that ``step_id="Step 2"`` does NOT
    match ``### Step 2a:`` (which sorts before ``### Step 2:`` in SKILL.md).
    """
    pattern = rf"###\s+{re.escape(step_id)}(?=[:\s\n])[\s\S]*?(?=\n###\s+Step|\Z)"
    m = re.search(pattern, text)
    assert m is not None, f"Could not locate '{step_id}' section in resolve-failures/SKILL.md"
    return m.group(0)


# --- Polling-cascade fix ---

def test_resolve_failures_step2_prohibits_bash_test_run() -> None:
    """Step 2 must not instruct running tests via Bash (cd … && {test_command}).

    The Bash tool auto-backgrounds commands that exceed its timeout, forcing the
    LLM into a polling cascade. test_check MCP blocks synchronously.
    """
    text = _skill_text()
    step2 = _extract_step(text, "Step 2")
    assert "cd {worktree_path}" not in step2, (
        "resolve-failures Step 2 still instructs 'cd {worktree_path} && {test_command}' "
        "via Bash. Replace with test_check MCP tool call to prevent polling cascade."
    )


def test_resolve_failures_step2_prescribes_test_check_mcp() -> None:
    """Step 2 must prescribe using the test_check MCP tool, not a raw shell command."""
    text = _skill_text()
    step2 = _extract_step(text, "Step 2")
    assert "test_check" in step2, (
        "resolve-failures Step 2 must instruct use of the test_check MCP tool. "
        "It currently runs tests via Bash, causing auto-backgrounding and polling."
    )


def test_resolve_failures_fix_loop_prohibits_bash_rerun() -> None:
    """Step 3 fix loop must not re-run tests via Bash (cd … && {test_command}).

    Applies the same polling-cascade risk as Step 2.
    """
    text = _skill_text()
    step3 = _extract_step(text, "Step 3")
    assert "cd {worktree_path}" not in step3, (
        "resolve-failures Step 3 fix loop still re-runs tests via "
        "'cd {worktree_path} && {test_command}'. Replace with test_check MCP call."
    )


def test_resolve_failures_fix_loop_prescribes_test_check_mcp() -> None:
    """Step 3 fix loop must use the test_check MCP tool for its re-run step."""
    text = _skill_text()
    step3 = _extract_step(text, "Step 3")
    assert "test_check" in step3, (
        "resolve-failures Step 3 fix loop must instruct use of the test_check MCP "
        "tool for re-running tests. It currently uses a Bash command."
    )


# --- Output-bloat fix ---

def test_resolve_failures_fix_loop_instructs_output_summarization() -> None:
    """Step 3 must instruct the LLM to retain only failure signal, discarding full stdout.

    Without this, 3–10K chars of pytest output accumulate per run (~17K tokens over
    a session), inflating every subsequent API call's cache_read cost.
    """
    text = _skill_text()
    step3 = _extract_step(text, "Step 3")
    # Must mention extracting/retaining only the key failure info
    has_extract = bool(
        re.search(r"extract|retain only|only.{0,40}(fail|count|name)", step3, re.IGNORECASE)
    )
    assert has_extract, (
        "resolve-failures Step 3 must instruct the LLM to extract and retain only "
        "the failure signal (counts, test names, error messages) from each test run, "
        "discarding full pytest stdout to prevent context accumulation."
    )


def test_resolve_failures_fix_loop_instructs_stdout_discard() -> None:
    """Step 3 must explicitly instruct discarding the full test output after extraction."""
    text = _skill_text()
    step3 = _extract_step(text, "Step 3")
    has_discard = bool(
        re.search(r"discard|do not retain|not retain.{0,40}full|full.{0,40}output", step3, re.IGNORECASE)
    )
    assert has_discard, (
        "resolve-failures Step 3 must explicitly instruct discarding full pytest stdout "
        "after extracting failure info. This prevents context bloat across fix iterations."
    )
