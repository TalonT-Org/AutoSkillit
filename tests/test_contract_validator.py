"""Tests for contract_validator module."""

from __future__ import annotations

from pathlib import Path

from autoskillit.contract_validator import (
    check_contract_staleness,
    compute_skill_hash,
    generate_recipe_card,
    load_bundled_manifest,
    load_recipe_card,
    resolve_skill_name,
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
    """Generates a contract dict and writes the contract file."""
    recipes_dir = tmp_path / ".autoskillit" / "scripts"
    recipes_dir.mkdir(parents=True)
    pipeline = recipes_dir / "test-pipeline.yaml"
    pipeline.write_text(SAMPLE_PIPELINE_YAML)

    result = generate_recipe_card(pipeline, recipes_dir)

    assert isinstance(result, dict), f"Expected dict, got {type(result)}"
    assert "generated_at" in result
    assert "bundled_manifest_version" in result
    assert "skill_hashes" in result
    assert "skills" in result
    assert "dataflow" in result

    # Disk write still happens for caching
    contract_path = recipes_dir / "contracts" / "test-pipeline.yaml"
    assert contract_path.exists()


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

    contract = generate_recipe_card(pipeline, recipes_dir)

    findings = validate_recipe_cards(None, contract)
    assert len(findings) == 0


def test_validate_recipe_cards_missing_input(tmp_path: Path):
    """Pipeline with missing skill input produces finding."""
    recipes_dir = tmp_path / ".autoskillit" / "scripts"
    recipes_dir.mkdir(parents=True)
    pipeline = recipes_dir / "bad.yaml"
    pipeline.write_text(BAD_PIPELINE_YAML)

    contract = generate_recipe_card(pipeline, recipes_dir)

    findings = validate_recipe_cards(None, contract)
    assert len(findings) > 0
    assert any("worktree_path" in f["message"] for f in findings)


# ---------------------------------------------------------------------------
# CV-GF1: make-groups declares group_files output
# ---------------------------------------------------------------------------


def test_skill_contracts_make_groups_declares_group_files():
    """skill_contracts.yaml must declare group_files output for make-groups."""
    from autoskillit.contract_validator import load_bundled_manifest

    manifest = load_bundled_manifest()
    output_names = [o["name"] for o in manifest["skills"]["make-groups"]["outputs"]]
    assert "group_files" in output_names
