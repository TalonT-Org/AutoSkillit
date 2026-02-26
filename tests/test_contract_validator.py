"""Tests for contract_validator module."""

from __future__ import annotations

from pathlib import Path

import yaml

from autoskillit.contract_validator import (
    StaleItem,
    check_contract_staleness,
    compute_skill_hash,
    generate_recipe_card,
    load_bundled_manifest,
    load_recipe_card,
    resolve_skill_name,
    triage_staleness,
    validate_recipe_cards,
)

# ---------------------------------------------------------------------------
# T1: Manifest Loading
# ---------------------------------------------------------------------------


def test_load_bundled_manifest():
    """Bundled manifest loads successfully and contains all 14 skills."""
    manifest = load_bundled_manifest()
    assert manifest["version"] == "0.1.0"
    assert len(manifest["skills"]) == 14


def test_load_bundled_manifest_skill_inputs_typed():
    """Each input in the manifest has name, type, and required fields."""
    manifest = load_bundled_manifest()
    for skill_name, skill in manifest["skills"].items():
        assert "inputs" in skill
        assert "outputs" in skill
        for inp in skill["inputs"]:
            assert "name" in inp, f"{skill_name}: input missing 'name'"
            assert "type" in inp, f"{skill_name}: input {inp['name']} missing 'type'"
            assert "required" in inp, f"{skill_name}: input {inp['name']} missing 'required'"


# ---------------------------------------------------------------------------
# T2: Skill Name Resolution
# ---------------------------------------------------------------------------


def test_resolve_skill_name_standard():
    assert (
        resolve_skill_name("/autoskillit:retry-worktree ${{ context.plan_path }}")
        == "retry-worktree"
    )


def test_resolve_skill_name_with_use_prefix():
    assert (
        resolve_skill_name("Use /autoskillit:implement-worktree plan.md") == "implement-worktree"
    )


def test_resolve_skill_name_no_prefix():
    assert resolve_skill_name("/do-stuff") is None


def test_resolve_skill_name_dynamic():
    """Dynamic skill commands like /audit-${{ inputs.audit_type }} return None."""
    assert resolve_skill_name("/audit-${{ inputs.audit_type }}") is None


# ---------------------------------------------------------------------------
# T4: Pipeline Contract Generation and Loading
# ---------------------------------------------------------------------------

SAMPLE_PIPELINE_YAML = """\
name: test-pipeline
description: A test pipeline
summary: "Test flow"
inputs:
  plan_path:
    description: Plan file
    required: true
steps:
  implement:
    tool: run_skill
    with:
      skill_command: "/autoskillit:implement-worktree-no-merge ${{ inputs.plan_path }}"
    capture:
      worktree_path: "${{ result.worktree_path }}"
    on_success: test
  test:
    tool: test_check
    with:
      worktree_path: "${{ context.worktree_path }}"
    on_success: done
    on_failure: done
  done:
    action: stop
    message: "Done."
constraints:
  - test
"""


def test_generate_recipe_card(tmp_path: Path):
    """Generates a contract file with expected structure."""
    recipes_dir = tmp_path / ".autoskillit" / "scripts"
    recipes_dir.mkdir(parents=True)
    pipeline = recipes_dir / "test-pipeline.yaml"
    pipeline.write_text(SAMPLE_PIPELINE_YAML)

    generate_recipe_card(pipeline, recipes_dir)

    contract_path = recipes_dir / "contracts" / "test-pipeline.yaml"
    assert contract_path.exists()
    contract = yaml.safe_load(contract_path.read_text())
    assert "generated_at" in contract
    assert "bundled_manifest_version" in contract
    assert "skill_hashes" in contract
    assert "skills" in contract
    assert "dataflow" in contract


def test_load_recipe_card(tmp_path: Path):
    """Loads a previously generated contract."""
    recipes_dir = tmp_path / ".autoskillit" / "scripts"
    recipes_dir.mkdir(parents=True)
    pipeline = recipes_dir / "test-pipeline.yaml"
    pipeline.write_text(SAMPLE_PIPELINE_YAML)

    generate_recipe_card(pipeline, recipes_dir)

    contract = load_recipe_card("test-pipeline", recipes_dir)
    assert contract is not None
    assert contract["bundled_manifest_version"] == "0.1.0"


def test_load_recipe_card_missing():
    """Returns None when no contract file exists."""
    contract = load_recipe_card("nonexistent", Path("/tmp/no-scripts"))
    assert contract is None


# ---------------------------------------------------------------------------
# T5: Staleness Detection
# ---------------------------------------------------------------------------


def test_check_staleness_clean():
    """No staleness when version and hashes match."""
    contract = {
        "bundled_manifest_version": "0.1.0",
        "skill_hashes": {"investigate": compute_skill_hash("investigate")},
    }
    stale = check_contract_staleness(contract)
    assert len(stale) == 0


def test_check_staleness_version_mismatch():
    """Detects bundled manifest version drift."""
    contract = {
        "bundled_manifest_version": "0.0.1",
        "skill_hashes": {},
    }
    stale = check_contract_staleness(contract)
    assert any(s.reason == "version_mismatch" for s in stale)


def test_check_staleness_hash_mismatch():
    """Detects SKILL.md content change."""
    contract = {
        "bundled_manifest_version": "0.1.0",
        "skill_hashes": {"investigate": "sha256:0000000000"},
    }
    stale = check_contract_staleness(contract)
    assert any(s.skill == "investigate" and s.reason == "hash_mismatch" for s in stale)


# ---------------------------------------------------------------------------
# T6: Dataflow Validation
# ---------------------------------------------------------------------------

CLEAN_PIPELINE_YAML = """\
name: clean-pipeline
description: Pipeline with correct dataflow
summary: "Clean flow"
inputs:
  plan_path:
    description: Plan file
    required: true
steps:
  implement:
    tool: run_skill
    with:
      skill_command: "/autoskillit:implement-worktree-no-merge ${{ inputs.plan_path }}"
    capture:
      worktree_path: "${{ result.worktree_path }}"
    on_success: retry
  retry:
    tool: run_skill_retry
    with:
      skill_command: >-
        /autoskillit:retry-worktree
        ${{ inputs.plan_path }}
        ${{ context.worktree_path }}
    retry:
      on: needs_retry
      max_attempts: 3
      on_exhausted: done
    on_success: done
  done:
    action: stop
    message: "Done."
constraints:
  - test
"""

BAD_PIPELINE_YAML = """\
name: bad-pipeline
description: Pipeline with missing skill input
summary: "Bad flow"
inputs:
  plan_path:
    description: Plan file
    required: true
steps:
  implement:
    tool: run_skill
    with:
      skill_command: "/autoskillit:implement-worktree-no-merge ${{ inputs.plan_path }}"
    capture:
      worktree_path: "${{ result.worktree_path }}"
    on_success: retry
  retry:
    tool: run_skill_retry
    with:
      skill_command: "/autoskillit:retry-worktree ${{ inputs.plan_path }}"
    retry:
      on: needs_retry
      max_attempts: 3
      on_exhausted: done
    on_success: done
  done:
    action: stop
    message: "Done."
constraints:
  - test
"""


def test_validate_recipe_cards_clean(tmp_path: Path):
    """Pipeline with correct dataflow produces no findings."""
    recipes_dir = tmp_path / ".autoskillit" / "scripts"
    recipes_dir.mkdir(parents=True)
    pipeline = recipes_dir / "clean.yaml"
    pipeline.write_text(CLEAN_PIPELINE_YAML)

    contract_path = generate_recipe_card(pipeline, recipes_dir)
    contract = yaml.safe_load(contract_path.read_text())

    findings = validate_recipe_cards(None, contract)
    assert len(findings) == 0


def test_validate_recipe_cards_missing_input(tmp_path: Path):
    """Pipeline with missing skill input produces finding."""
    recipes_dir = tmp_path / ".autoskillit" / "scripts"
    recipes_dir.mkdir(parents=True)
    pipeline = recipes_dir / "bad.yaml"
    pipeline.write_text(BAD_PIPELINE_YAML)

    contract_path = generate_recipe_card(pipeline, recipes_dir)
    contract = yaml.safe_load(contract_path.read_text())

    findings = validate_recipe_cards(None, contract)
    assert len(findings) > 0
    assert any("worktree_path" in f["message"] for f in findings)


# ---------------------------------------------------------------------------
# Structural: subprocess I/O uses temp files, not PIPE
# ---------------------------------------------------------------------------


class TestContractValidatorSubprocess:
    """triage_staleness must use temp file I/O instead of asyncio.subprocess.PIPE."""

    def test_contract_validator_uses_temp_file_not_pipe(self):
        """Structural assertion: triage_staleness must not use PIPE for subprocess I/O.

        The rest of the codebase was explicitly redesigned to avoid pipe-buffer
        deadlock when child subprocesses inherit the write-end FD. This test
        ensures the contract_validator subprocess call follows the same pattern.
        """
        import inspect

        from autoskillit.contract_validator import triage_staleness

        source = inspect.getsource(triage_staleness)
        assert "asyncio.subprocess.PIPE" not in source, (
            "triage_staleness must not use asyncio.subprocess.PIPE for subprocess I/O; "
            "use create_temp_io from process_lifecycle instead"
        )
        assert "create_temp_io" in source, (
            "triage_staleness must use create_temp_io for subprocess stdout/stderr"
        )


# ---------------------------------------------------------------------------
# T7: triage_staleness — subprocess lifecycle, logging, and error paths
# ---------------------------------------------------------------------------


class TestTriageStaleness:
    """Executable test coverage for triage_staleness failure paths."""

    async def test_triage_staleness_timeout_kills_subprocess(self, tmp_path: Path):
        """On TimeoutError, proc.kill() is called and proc.wait() is called in the finally."""
        import asyncio
        from unittest.mock import AsyncMock, MagicMock, patch

        skill_dir = tmp_path / "test-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text("# Test Skill\nDummy content.")

        proc_mock = MagicMock()
        proc_mock.returncode = None
        proc_mock.wait = AsyncMock(return_value=None)
        proc_mock.kill = MagicMock()

        item = StaleItem(
            skill="test-skill",
            reason="hash_mismatch",
            stored_value="abc123",
            current_value="def456",
        )

        with (
            patch("autoskillit.contract_validator.bundled_skills_dir", return_value=tmp_path),
            patch(
                "autoskillit.contract_validator.asyncio.create_subprocess_exec",
                new_callable=AsyncMock,
                return_value=proc_mock,
            ),
            patch(
                "autoskillit.contract_validator.asyncio.wait_for",
                side_effect=asyncio.TimeoutError,
            ),
        ):
            result = await triage_staleness([item])

        assert proc_mock.kill.called, "proc.kill() must be called on TimeoutError"
        assert proc_mock.wait.call_count >= 1, "proc.wait() must be called in finally block"
        assert len(result) == 1
        assert result[0]["meaningful"] is True
        assert result[0]["skill"] == "test-skill"

    async def test_triage_staleness_timeout_is_logged(self, tmp_path: Path):
        """On TimeoutError, a warning log is emitted."""
        import asyncio
        from unittest.mock import AsyncMock, MagicMock, patch

        import structlog

        skill_dir = tmp_path / "test-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text("# Test Skill\nDummy content.")

        proc_mock = MagicMock()
        proc_mock.returncode = None
        proc_mock.wait = AsyncMock(return_value=None)
        proc_mock.kill = MagicMock()

        item = StaleItem(
            skill="test-skill",
            reason="hash_mismatch",
            stored_value="abc123",
            current_value="def456",
        )

        with (
            patch("autoskillit.contract_validator.bundled_skills_dir", return_value=tmp_path),
            patch(
                "autoskillit.contract_validator.asyncio.create_subprocess_exec",
                new_callable=AsyncMock,
                return_value=proc_mock,
            ),
            patch(
                "autoskillit.contract_validator.asyncio.wait_for",
                side_effect=asyncio.TimeoutError,
            ),
            structlog.testing.capture_logs() as logs,
        ):
            await triage_staleness([item])

        assert any(log["log_level"] == "warning" for log in logs), (
            "A warning log must be emitted on TimeoutError"
        )
        assert any(
            "triage" in log.get("event", "").lower() or "failed" in log.get("event", "").lower()
            for log in logs
        ), "Log event must mention triage or failed"

    async def test_triage_staleness_json_decode_error_is_logged(self, tmp_path: Path):
        """On JSONDecodeError, a warning log is emitted and meaningful=True is returned."""
        from unittest.mock import AsyncMock, MagicMock, patch

        import structlog

        skill_dir = tmp_path / "test-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text("# Test Skill\nDummy content.")

        proc_mock = MagicMock()
        proc_mock.returncode = 0
        proc_mock.wait = AsyncMock(return_value=None)
        proc_mock.kill = MagicMock()

        item = StaleItem(
            skill="test-skill",
            reason="hash_mismatch",
            stored_value="abc123",
            current_value="def456",
        )

        with (
            patch("autoskillit.contract_validator.bundled_skills_dir", return_value=tmp_path),
            patch(
                "autoskillit.contract_validator.asyncio.create_subprocess_exec",
                new_callable=AsyncMock,
                return_value=proc_mock,
            ),
            patch(
                "autoskillit.contract_validator.asyncio.wait_for",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "autoskillit.contract_validator.read_temp_output",
                return_value=("not json at all", ""),
            ),
            structlog.testing.capture_logs() as logs,
        ):
            result = await triage_staleness([item])

        assert result[0]["meaningful"] is True
        assert any(log["log_level"] == "warning" for log in logs), (
            "A warning log must be emitted on JSONDecodeError"
        )

    async def test_triage_staleness_success_does_not_kill_running_proc(self, tmp_path: Path):
        """On success (proc.returncode == 0), proc.kill() is NOT called."""
        from unittest.mock import AsyncMock, MagicMock, patch

        skill_dir = tmp_path / "test-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text("# Test Skill\nDummy content.")

        proc_mock = MagicMock()
        proc_mock.returncode = 0
        proc_mock.wait = AsyncMock(return_value=None)
        proc_mock.kill = MagicMock()

        item = StaleItem(
            skill="test-skill",
            reason="hash_mismatch",
            stored_value="abc123",
            current_value="def456",
        )

        with (
            patch("autoskillit.contract_validator.bundled_skills_dir", return_value=tmp_path),
            patch(
                "autoskillit.contract_validator.asyncio.create_subprocess_exec",
                new_callable=AsyncMock,
                return_value=proc_mock,
            ),
            patch(
                "autoskillit.contract_validator.asyncio.wait_for",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "autoskillit.contract_validator.read_temp_output",
                return_value=('{"meaningful_change": false, "summary": "ok"}', ""),
            ),
        ):
            result = await triage_staleness([item])

        assert result[0]["meaningful"] is False
        assert result[0]["summary"] == "ok"
        assert not proc_mock.kill.called, (
            "proc.kill() must NOT be called when process exits cleanly"
        )

    async def test_triage_staleness_missing_skill_md_returns_meaningful_true(self, tmp_path: Path):
        """When SKILL.md is absent, returns meaningful=True without spawning a subprocess."""
        from unittest.mock import AsyncMock, patch

        # Do NOT create SKILL.md — the directory doesn't exist
        item = StaleItem(
            skill="test-skill",
            reason="hash_mismatch",
            stored_value="abc123",
            current_value="def456",
        )

        with (
            patch("autoskillit.contract_validator.bundled_skills_dir", return_value=tmp_path),
            patch(
                "autoskillit.contract_validator.asyncio.create_subprocess_exec",
                new_callable=AsyncMock,
            ) as mock_exec,
        ):
            result = await triage_staleness([item])

        assert len(result) == 1
        assert result[0]["meaningful"] is True
        assert "not found" in result[0]["summary"].lower()
        assert not mock_exec.called, (
            "create_subprocess_exec must NOT be called when SKILL.md is missing"
        )
