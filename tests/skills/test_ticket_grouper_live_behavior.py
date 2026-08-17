"""Live-behavior regression test for the Ticket Grouper self-check.

Reproduces the real #4610-producing manifest verbatim and feeds it through the
new Step 7 self-check instructions in a live ``claude --print`` session,
asserting that neither effort tier (High or Medium) ends up collapsed into a
single ticket group.

Gated by the weekly ``conformance-probes.yml`` ``claude-probe`` job — see
``_require_live_gate`` for the opt-in / executable / auth predicate and
``_build_subprocess_env`` for the explicit subprocess env allowlist.
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

# Cross-check that every tier-file name actually appears in the fixture
# manifest. If ``broken_ticket_grouper_manifest.md`` drifts (file rename,
# removal, re-tiering), this fails fast instead of silently degrading the
# tier-bound assertions below.
for _name in _HIGH_EFFORT_FILES + _MEDIUM_EFFORT_FILES:
    assert _name in broken_ticket_grouper_manifest.BROKEN_MANIFEST, (
        f"Tier file {_name!r} missing from broken_ticket_grouper_manifest fixture"
    )


# Shared opt-in env var for live-behavior probes. The weekly conformance
# claude-probe job sets ``CLAUDE_CODE_SMOKE_TEST=1`` for every smoke-gated
# probe in the same Python invocation (see
# tests/execution/backends/test_cli_conformance_probes.py and
# tests/server/test_output_budget_e2e.py).
_LIVE_ENV = "CLAUDE_CODE_SMOKE_TEST"
_SOURCE_CREDENTIALS = Path.home() / ".claude" / ".credentials.json"


# Runtime live-gate predicate. A pytest fixture is used instead of a
# module-level ``pytest.mark.skipif`` so the env-var check re-runs at test
# collection time rather than at module-import time, picking up any late
# ``os.environ`` mutation made by a session-level fixture or conftest.
def _live_gate_active() -> bool:
    has_auth = bool(
        os.environ.get("ANTHROPIC_API_KEY")
        or os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")
        or _SOURCE_CREDENTIALS.is_file()
    )
    return bool(
        os.environ.get(_LIVE_ENV) == "1" and shutil.which("claude") is not None and has_auth
    )


@pytest.fixture(autouse=True)
def _require_live_gate() -> None:
    if not _live_gate_active():
        pytest.skip(
            "Ticket Grouper self-check live gate requires its opt-in, "
            "executable, and isolated auth"
        )


def _initialize_repository(project: Path) -> None:
    project.mkdir(parents=True)
    init_env = {k: os.environ[k] for k in _GIT_SUBPROCESS_ENV_ALLOWLIST if k in os.environ}
    subprocess.run(["git", "init", "-q"], cwd=project, check=True, timeout=10, env=init_env)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=project,
        check=True,
        timeout=10,
        env=init_env,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=project,
        check=True,
        timeout=10,
        env=init_env,
    )
    (project / ".gitignore").write_text(".autoskillit/\n")
    (project / "README.md").write_text("# Test\n")
    subprocess.run(["git", "add", "."], cwd=project, check=True, timeout=10, env=init_env)
    subprocess.run(
        ["git", "commit", "-q", "-m", "init"],
        cwd=project,
        check=True,
        timeout=10,
        env={**init_env, "GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "test@example.com"},
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
# Allowlist of env vars the local git subprocess is permitted to inherit.
# Kept narrower than the Claude allowlist above because git only needs PATH,
# locale vars, and the agent identity env vars the test sets explicitly.
_GIT_SUBPROCESS_ENV_ALLOWLIST = (
    "PATH",
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
            try:
                process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                tail = output_path.read_text()[-4000:]
                pytest.fail(f"Ticket Grouper self-check live gate timed out: {tail}")
        finally:
            _cleanup_owned_process_group(process, timeout=10)
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
    target.chmod(0o600)


def _extract_result_text(output: str) -> str:
    """``claude --output-format json`` wraps the final text under a top-level ``"result"`` key —
    same shape already relied on by ``src/autoskillit/hooks/formatters/pretty_output_hook.py`` and
    ``token_summary_hook.py``.
    """
    try:
        return json.loads(output)["result"]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        pytest.fail(
            f"Ticket Grouper self-check live gate returned non-JSON or malformed output: "
            f"{exc}\nResponse tail:\n{output[-4000:]}"
        )


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
    assert len(blocks) >= 2, (
        "Ticket Grouper self-check returned no parseable ticket groups — "
        "expected at least 2 (Group 12 + Group 13) but the response "
        f"split into {len(blocks)} block(s).\nResponse tail:\n{result_text[-4000:]}"
    )
    # Group 12 (HIGH) names "Pair A/B/C" — each ticket should pair at most one
    # other file, so max files per block must be <= len / 3 (i.e. at most 2 of
    # 6). Group 13 (MEDIUM) accepts "small groups" — at most half the tier
    # (3 of 6). The joint bound catches both 6+0 (no split at all) and
    # structurally unbalanced splits (5+1, 4+2, 3+3 for Group 12).
    tier_bounds = (
        (_HIGH_EFFORT_FILES, len(_HIGH_EFFORT_FILES) // 3),  # pairs
        (_MEDIUM_EFFORT_FILES, len(_MEDIUM_EFFORT_FILES) // 2),  # small groups
    )
    for tier_files, max_per_block in tier_bounds:
        max_in_one_block = max((sum(f in b for f in tier_files) for b in blocks), default=0)
        assert max_in_one_block < len(tier_files), (
            "self-check failed to split an effort tier that was crushed into one ticket in the "
            f"real #4610 incident. Response tail:\n{result_text[-4000:]}"
        )
        assert max_in_one_block <= max_per_block, (
            f"self-check produced an unbalanced split for an effort tier that explicitly "
            f"names per-file pairings / small batches: a single block has "
            f"{max_in_one_block} of {len(tier_files)} tier files (max allowed "
            f"{max_per_block}). Response tail:\n{result_text[-4000:]}"
        )
