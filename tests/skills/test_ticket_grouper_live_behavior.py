"""Live-behavior regression test for the Ticket Grouper self-check.

Reproduces the real #4610-producing manifest verbatim and feeds it
through the new Step 7 self-check instructions in a live `claude --print`
session, asserting that neither effort tier (High or Medium) ends up
collapsed into a single ticket group.

Skip-gated on:
  - AUTOSKILLIT_TICKET_GROUPER_LIVE_GATE=1 (opt-in)
  - `claude` CLI on PATH
  - one of ANTHROPIC_API_KEY / CLAUDE_CODE_OAUTH_TOKEN / ~/.claude/.credentials.json

Runs unattended via the weekly `conformance-probes.yml` claude-probe job.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.execution._process_group_helpers import _cleanup_owned_process_group
from tests.skills.conftest import extract_step7_ticket_grouper_block, resolve_skill_text

pytestmark = [pytest.mark.layer("skills"), pytest.mark.large, pytest.mark.smoke]


# Real Group 12 + Group 13 manifest text, verbatim from
# `.autoskillit/temp/validate-audit-2026-08-15_203307/grouping_manifest_tests.md:74-87`.
_BROKEN_MANIFEST = """\
### Ticket Group 12: Server Test Splits — High-Effort Pairs
- **Finding IDs**: C9.37-56 (subset: test_tools_kitchen_envelope.py 1369L, test_tools_issue_lifecycle.py 1352L, test_tools_execution_results.py 1097L, test_tools_integrations.py 1014L, test_recipe_section_pagination.py 1326L, test_factory.py 954L)
- **Rationale**: Six HIGH-effort (>900 line) server test files. Per the effort rule, pair with at most one other file. Proposed pairings:
  - **Pair A** (kitchen tool tests): test_tools_kitchen_envelope.py + test_tools_issue_lifecycle.py (both kitchen-domain, both >1300 lines)
  - **Pair B** (execution tool tests): test_tools_execution_results.py + test_tools_integrations.py (both tool-results, both >1000 lines)
  - **Pair C** (cross-cutting): test_recipe_section_pagination.py + test_factory.py (both factory/pagination infrastructure)
- **Scope**: large
- **File overlap**: none between pairs

### Ticket Group 13: Server Test Splits — Medium-Effort Batch
- **Finding IDs**: C9.37-56 (subset: test_pipeline_tracker.py 883L, test_tools_kitchen_visibility.py 872L, test_tools_execution_input_gates.py 810L, test_tools_load_recipe.py 799L, test_tools_ci.py 775L, test_session_type_visibility.py 759L)
- **Rationale**: Six MEDIUM-effort (750-900 line) server test files. Per the effort rule, these can be batched in small groups in the same package. All in `tests/server/`, all have class boundaries for lift-to-file. Single batch ticket.
- **Scope**: medium
- **File overlap**: none
"""

_HIGH_EFFORT_FILES = (
    "test_tools_kitchen_envelope.py",
    "test_tools_issue_lifecycle.py",
    "test_tools_execution_results.py",
    "test_tools_integrations.py",
    "test_recipe_section_pagination.py",
    "test_factory.py",
)
_MEDIUM_EFFORT_FILES = (
    "test_pipeline_tracker.py",
    "test_tools_kitchen_visibility.py",
    "test_tools_execution_input_gates.py",
    "test_tools_load_recipe.py",
    "test_tools_ci.py",
    "test_session_type_visibility.py",
)
_GROUP_SPLIT_RE = re.compile(r"(?=^### Ticket Group)", re.MULTILINE)


_LIVE_ENV = "AUTOSKILLIT_TICKET_GROUPER_LIVE_GATE"
_SOURCE_CREDENTIALS = Path.home() / ".claude" / ".credentials.json"
_has_authentication = bool(
    os.environ.get("ANTHROPIC_API_KEY")
    or os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")
    or _SOURCE_CREDENTIALS.is_file()
)
_skip_unless_live_gate = pytest.mark.skipif(
    os.environ.get(_LIVE_ENV) != "1" or shutil.which("claude") is None or not _has_authentication,
    reason="Ticket Grouper self-check live gate requires its opt-in, executable, and isolated auth",
)


def _initialize_repository(project: Path) -> None:
    project.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=project, check=True, timeout=10)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"], cwd=project, check=True, timeout=10
    )
    subprocess.run(["git", "config", "user.name", "Test"], cwd=project, check=True, timeout=10)
    (project / ".gitignore").write_text(".autoskillit/\n")
    (project / "README.md").write_text("# Test\n")
    subprocess.run(["git", "add", "."], cwd=project, check=True, timeout=10)
    subprocess.run(
        ["git", "commit", "-q", "-m", "init"],
        cwd=project,
        check=True,
        timeout=10,
        env={**os.environ, "GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "test@example.com"},
    )


def _run_claude(project: Path, home: Path, prompt: str, timeout: float) -> str:
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["CLAUDE_CONFIG_DIR"] = str(home / ".claude")
    env.pop("AUTOSKILLIT_HEADLESS", None)
    output_path = project / ".autoskillit" / "temp" / "claude-live-output.txt"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as output_stream:
        process = subprocess.Popen(
            [
                "claude",
                "--print",
                "--dangerously-skip-permissions",
                "--output-format",
                "json",
                prompt,
            ],
            cwd=project,
            env=env,
            stdout=output_stream,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,
        )
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            _cleanup_owned_process_group(process, timeout=10)
            pytest.fail(
                f"Ticket Grouper self-check live gate timed out: {output_path.read_text()[-4000:]}"
            )
    output = output_path.read_text()
    assert process.returncode == 0, output[-4000:]
    return output


def _extract_result_text(output: str) -> str:
    """`claude --output-format json` wraps the final text under a top-level "result" key —
    same shape already relied on by src/autoskillit/hooks/formatters/pretty_output_hook.py and
    token_summary_hook.py."""
    return json.loads(output)["result"]


@_skip_unless_live_gate
@pytest.mark.timeout(150)
def test_step7_self_check_splits_the_real_broken_manifest(tmp_path: Path) -> None:
    project = tmp_path / "project"
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    if _SOURCE_CREDENTIALS.is_file():
        (home / ".claude" / ".credentials.json").symlink_to(_SOURCE_CREDENTIALS.resolve())
    _initialize_repository(project)

    step7_block = extract_step7_ticket_grouper_block(resolve_skill_text("validate-test-audit"))
    assert step7_block
    prompt = (
        "Apply this validation step to a grouping manifest that was just returned by a Ticket "
        f"Grouper subagent:\n\n{step7_block}\n\nHere is the manifest:\n\n{_BROKEN_MANIFEST}\n\n"
        "Output only the corrected grouping manifest."
    )
    output = _run_claude(project, home, prompt, timeout=120)
    result_text = _extract_result_text(output)

    blocks = [b for b in _GROUP_SPLIT_RE.split(result_text) if b.strip()]
    for tier_files in (_HIGH_EFFORT_FILES, _MEDIUM_EFFORT_FILES):
        max_in_one_block = max((sum(f in b for f in tier_files) for b in blocks), default=0)
        assert max_in_one_block < len(tier_files), (
            "self-check failed to split an effort tier that was crushed into one ticket in the "
            f"real #4610 incident. Response tail:\n{result_text[-4000:]}"
        )
