"""Live-behavior regression test for the Ticket Grouper self-check.

Reproduces the real #4610-producing manifest verbatim and feeds it through the
new Step 7 self-check instructions in a live ``claude --print`` session,
asserting that neither effort tier (High or Medium) ends up collapsed into a
single ticket group.

Gating
------
The test uses the same opt-in mechanism as the sibling live-behavior probes
in ``tests/execution/backends/test_cli_conformance_probes.py`` and
``tests/server/test_output_budget_e2e.py``: the weekly ``conformance-probes.yml``
``claude-probe`` job sets ``CLAUDE_CODE_SMOKE_TEST=1`` and the test only runs
when (a) that flag is set, (b) ``claude`` is on ``PATH``, and (c) one of
``ANTHROPIC_API_KEY`` / ``CLAUDE_CODE_OAUTH_TOKEN`` /
``~/.claude/.credentials.json`` is present.

Subprocess safety
-----------------
* The test invokes ``claude --dangerously-skip-permissions`` with permission
  prompts suppressed. This is intentional: the probe exercises the bundled
  skill instructions exactly as written, and any tool-use permission prompt
  would defeat the point. The subprocess env is built from an explicit
  allowlist (see ``_build_subprocess_env``) rather than ``os.environ.copy()``
  to avoid leaking incidental CI/developer credentials.
* The test copies the user's credentials file into a tmp ``HOME`` (rather
  than symlinking) so a crash or interrupted cleanup cannot leave a live
  pointer back into the user's real credential store.
* The new file is set ``chmod 0o600`` to mirror the source file's permissions.

Runs unattended via the weekly ``conformance-probes.yml`` ``claude-probe`` job.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.process_group_helpers import _cleanup_owned_process_group
from tests.skills._fixtures import broken_ticket_grouper_manifest
from tests.skills._skill_text_helpers import (
    CANONICAL_TICKET_GROUPER_SKILL,
    extract_step7_grouper_block,
    resolve_skill_text,
)

pytestmark = [pytest.mark.layer("skills"), pytest.mark.large, pytest.mark.smoke]


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


# Shared opt-in env var for live-behavior probes. The weekly conformance
# claude-probe job sets ``CLAUDE_CODE_SMOKE_TEST=1`` for every smoke-gated
# probe in the same Python invocation (see
# tests/execution/backends/test_cli_conformance_probes.py and
# tests/server/test_output_budget_e2e.py).
_LIVE_ENV = "CLAUDE_CODE_SMOKE_TEST"
_SOURCE_CREDENTIALS = Path.home() / ".claude" / ".credentials.json"


def _has_authentication() -> bool:
    """Re-evaluated on every skipif call so env mutations after import are seen."""
    return bool(
        os.environ.get("ANTHROPIC_API_KEY")
        or os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")
        or _SOURCE_CREDENTIALS.is_file()
    )


_skip_unless_live_gate = pytest.mark.skipif(
    os.environ.get(_LIVE_ENV) != "1"
    or shutil.which("claude") is None
    or not _has_authentication(),
    reason=(
        "Ticket Grouper self-check live gate requires its opt-in, executable, and isolated auth"
    ),
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


# Allowlist of env vars the subprocess is permitted to inherit. Anything else
# (incidental CI secrets, unrelated *_TOKEN vars) is dropped so an
# --dangerously-skip-permissions child can't see them.
_CLAUDE_SUBPROCESS_ENV_ALLOWLIST = (
    "HOME",
    "CLAUDE_CONFIG_DIR",
    "PATH",
    "ANTHROPIC_API_KEY",
    "CLAUDE_CODE_OAUTH_TOKEN",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
)


def _build_subprocess_env(home: Path) -> dict[str, str]:
    return {
        **{k: os.environ[k] for k in _CLAUDE_SUBPROCESS_ENV_ALLOWLIST if k in os.environ},
        "HOME": str(home),
        "CLAUDE_CONFIG_DIR": str(home / ".claude"),
    }


def _run_claude(project: Path, home: Path, prompt: str, timeout: float) -> str:
    env = _build_subprocess_env(home)
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


def _copy_credentials(home: Path) -> None:
    """Copy the user's credentials file into ``home/.claude/.credentials.json``.

    The copy is a real file (not a symlink) so a crash or interrupted cleanup
    cannot leave a live pointer back into the user's real credential store.
    """
    if not _SOURCE_CREDENTIALS.is_file():
        return
    target = home / ".claude" / ".credentials.json"
    target.write_bytes(_SOURCE_CREDENTIALS.read_bytes())
    try:
        target.chmod(0o600)
    except OSError:
        pass


def _extract_result_text(output: str) -> str:
    """``claude --output-format json`` wraps the final text under a top-level ``"result"`` key —
    same shape already relied on by ``src/autoskillit/hooks/formatters/pretty_output_hook.py`` and
    ``token_summary_hook.py``.
    """
    return json.loads(output)["result"]


@_skip_unless_live_gate
@pytest.mark.timeout(150)
def test_step7_self_check_splits_the_real_broken_manifest(tmp_path: Path) -> None:
    project = tmp_path / "project"
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    _copy_credentials(home)
    _initialize_repository(project)

    step7_block = extract_step7_grouper_block(resolve_skill_text(CANONICAL_TICKET_GROUPER_SKILL))
    assert step7_block
    broken_manifest = broken_ticket_grouper_manifest.BROKEN_MANIFEST
    prompt = (
        "Apply this validation step to a grouping manifest that was just returned by a Ticket "
        f"Grouper subagent:\n\n{step7_block}\n\nHere is the manifest:\n\n{broken_manifest}\n\n"
        "Output only the corrected grouping manifest."
    )
    output = _run_claude(project, home, prompt, timeout=120)
    result_text = _extract_result_text(output)

    blocks = [b for b in _GROUP_SPLIT_RE.split(result_text) if b.strip()]
    assert blocks, (
        "Ticket Grouper self-check returned no parseable ticket groups — "
        f"the Step 7 instructions may not be honoured.\nResponse tail:\n{result_text[-4000:]}"
    )
    for tier_files in (_HIGH_EFFORT_FILES, _MEDIUM_EFFORT_FILES):
        max_in_one_block = max((sum(f in b for f in tier_files) for b in blocks), default=0)
        assert max_in_one_block < len(tier_files), (
            "self-check failed to split an effort tier that was crushed into one ticket in the "
            f"real #4610 incident. Response tail:\n{result_text[-4000:]}"
        )
        # The "small groups in the same package" wording in the Group 13
        # rationale and the "Pair A/B/C" guidance in Group 12 both imply a
        # balanced split; a 5+1 / 4+2 / 3+3 imbalance all satisfy the
        # "anything but one block" check above but only the latter is
        # consistent with the rationale.
        assert max_in_one_block <= len(tier_files) // 2, (
            "self-check produced an unbalanced split: a single block has "
            f"{max_in_one_block} of {len(tier_files)} tier files. "
            "The rationale's effort-tier rules imply a max block size of at "
            f"most half the tier. Response tail:\n{result_text[-4000:]}"
        )
