"""Tests for contract card generation and staleness validation."""

from __future__ import annotations

from pathlib import Path

import yaml

from autoskillit.recipe.contracts import (
    check_contract_staleness,
    compute_skill_hash,
    generate_recipe_card,
    load_bundled_manifest,
    load_recipe_card,
    validate_recipe_cards,
)

# ---------------------------------------------------------------------------
# Bundled manifest tests
# ---------------------------------------------------------------------------


def test_load_bundled_manifest() -> None:
    manifest = load_bundled_manifest()
    assert manifest["version"] == "0.1.0"
    assert len(manifest["skills"]) == 19
    assert "implement-worktree" in manifest["skills"]
    assert "investigate" in manifest["skills"]
    assert "write-recipe" in manifest["skills"]


def test_load_bundled_manifest_skill_inputs_typed() -> None:
    manifest = load_bundled_manifest()
    for skill_name, skill in manifest["skills"].items():
        assert "inputs" in skill
        assert "outputs" in skill
        for inp in skill["inputs"]:
            assert "name" in inp, f"{skill_name}: input missing 'name'"
            assert "type" in inp, f"{skill_name}: input {inp['name']} missing 'type'"
            assert "required" in inp, f"{skill_name}: input {inp['name']} missing 'required'"


# ---------------------------------------------------------------------------
# resolve_skill_name tests
# ---------------------------------------------------------------------------


def test_resolve_skill_name_standard() -> None:
    from autoskillit.recipe.contracts import resolve_skill_name

    assert (
        resolve_skill_name("/autoskillit:retry-worktree ${{ context.plan_path }}")
        == "retry-worktree"
    )


def test_resolve_skill_name_with_use_prefix() -> None:
    from autoskillit.recipe.contracts import resolve_skill_name

    assert (
        resolve_skill_name("Use /autoskillit:implement-worktree plan.md") == "implement-worktree"
    )


def test_resolve_skill_name_no_prefix() -> None:
    from autoskillit.recipe.contracts import resolve_skill_name

    assert resolve_skill_name("/do-stuff") is None


def test_resolve_skill_name_dynamic() -> None:
    from autoskillit.recipe.contracts import resolve_skill_name

    assert resolve_skill_name("/audit-${{ inputs.audit_type }}") is None


# ---------------------------------------------------------------------------
# Shared YAML fixtures
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


# ---------------------------------------------------------------------------
# generate_recipe_card tests
# ---------------------------------------------------------------------------


def test_generate_recipe_card(tmp_path: Path) -> None:
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


def test_generate_recipe_card_returns_dict(tmp_path: Path) -> None:
    """generate_recipe_card returns a dict directly (not a Path)."""
    recipes_dir = tmp_path / ".autoskillit" / "scripts"
    recipes_dir.mkdir(parents=True)
    pipeline = recipes_dir / "test-dict-pipeline.yaml"
    pipeline.write_text(SAMPLE_PIPELINE_YAML)

    result = generate_recipe_card(pipeline, recipes_dir)

    assert isinstance(result, dict)
    assert "skill_hashes" in result


def test_generate_recipe_card_accepts_string_paths(tmp_path: Path) -> None:
    """generate_recipe_card must accept str paths (the JSON round-trip from run_python)."""
    recipes_dir = tmp_path / ".autoskillit" / "scripts"
    recipes_dir.mkdir(parents=True)
    pipeline = recipes_dir / "test-str-pipeline.yaml"
    pipeline.write_text(SAMPLE_PIPELINE_YAML)

    # Pass str arguments as run_python would after JSON round-trip
    result = generate_recipe_card(str(pipeline), str(recipes_dir))

    assert isinstance(result, dict)
    assert "skill_hashes" in result


# ---------------------------------------------------------------------------
# load_recipe_card tests
# ---------------------------------------------------------------------------


def test_load_recipe_card(tmp_path: Path) -> None:
    recipes_dir = tmp_path / ".autoskillit" / "scripts"
    recipes_dir.mkdir(parents=True)
    pipeline = recipes_dir / "test-pipeline.yaml"
    pipeline.write_text(SAMPLE_PIPELINE_YAML)

    generate_recipe_card(pipeline, recipes_dir)

    contract = load_recipe_card("test-pipeline", recipes_dir)
    assert contract is not None
    assert contract["bundled_manifest_version"] == "0.1.0"


def test_load_recipe_card_missing() -> None:
    contract = load_recipe_card("nonexistent", Path("/tmp/no-scripts"))
    assert contract is None


# ---------------------------------------------------------------------------
# check_contract_staleness tests
# ---------------------------------------------------------------------------


def test_check_staleness_clean() -> None:
    contract = {
        "bundled_manifest_version": "0.1.0",
        "skill_hashes": {"investigate": compute_skill_hash("investigate")},
    }
    stale = check_contract_staleness(contract)
    assert len(stale) == 0


def test_check_staleness_version_mismatch() -> None:
    contract = {"bundled_manifest_version": "0.0.1", "skill_hashes": {}}
    stale = check_contract_staleness(contract)
    assert any(s.reason == "version_mismatch" for s in stale)


def test_check_staleness_hash_mismatch() -> None:
    contract = {
        "bundled_manifest_version": "0.1.0",
        "skill_hashes": {"investigate": "sha256:0000000000"},
    }
    stale = check_contract_staleness(contract)
    assert any(s.skill == "investigate" and s.reason == "hash_mismatch" for s in stale)


def test_check_staleness_preserves_triage_result_on_repeated_stale_hit(
    tmp_path: Path,
) -> None:
    """When hash+version are unchanged and is_stale=True, triage_result is carried forward."""
    from datetime import UTC, datetime

    from autoskillit.recipe.staleness_cache import (
        StalenessEntry,
        read_staleness_cache,
        write_staleness_cache,
    )

    recipes_dir = tmp_path / ".autoskillit" / "recipes"
    recipes_dir.mkdir(parents=True)
    recipe_path = recipes_dir / "my-recipe.yaml"
    recipe_path.write_text("name: my-recipe\n")
    cache_path = tmp_path / "cache.json"

    contract = {
        "bundled_manifest_version": "0.1.0",
        "skill_hashes": {"investigate": "sha256:stale_hash"},
    }

    # First call: populates cache with triage_result=None
    check_contract_staleness(contract, recipe_path=recipe_path, cache_path=cache_path)

    # Manually set triage_result="cosmetic" (as _apply_triage_gate would)
    existing = read_staleness_cache(cache_path, "my-recipe")
    assert existing is not None
    write_staleness_cache(
        cache_path,
        "my-recipe",
        StalenessEntry(
            recipe_hash=existing.recipe_hash,
            manifest_version=existing.manifest_version,
            is_stale=True,
            triage_result="cosmetic",
            checked_at=datetime.now(UTC).isoformat(),
        ),
    )

    # Second call: must NOT destroy triage_result
    check_contract_staleness(contract, recipe_path=recipe_path, cache_path=cache_path)

    after = read_staleness_cache(cache_path, "my-recipe")
    assert after is not None
    assert after.triage_result == "cosmetic", (
        f"triage_result was destroyed. Expected 'cosmetic', got {after.triage_result!r}"
    )


def test_check_staleness_resets_triage_result_when_content_changes(
    tmp_path: Path,
) -> None:
    """When recipe content changes (hash mismatch), triage_result must be reset to None."""
    from datetime import UTC, datetime

    from autoskillit.recipe.staleness_cache import (
        StalenessEntry,
        read_staleness_cache,
        write_staleness_cache,
    )

    recipes_dir = tmp_path / ".autoskillit" / "recipes"
    recipes_dir.mkdir(parents=True)
    recipe_path = recipes_dir / "my-recipe.yaml"
    recipe_path.write_text("name: my-recipe\nversion: 1\n")
    cache_path = tmp_path / "cache.json"

    contract = {
        "bundled_manifest_version": "0.1.0",
        "skill_hashes": {"investigate": "sha256:stale_hash"},
    }

    # First call with old content
    check_contract_staleness(contract, recipe_path=recipe_path, cache_path=cache_path)
    existing = read_staleness_cache(cache_path, "my-recipe")
    assert existing is not None
    write_staleness_cache(
        cache_path,
        "my-recipe",
        StalenessEntry(
            recipe_hash=existing.recipe_hash,
            manifest_version=existing.manifest_version,
            is_stale=True,
            triage_result="cosmetic",
            checked_at=datetime.now(UTC).isoformat(),
        ),
    )

    # Mutate recipe file → hash changes
    recipe_path.write_text("name: my-recipe\nversion: 2\n")

    # Second call must reset triage_result
    check_contract_staleness(contract, recipe_path=recipe_path, cache_path=cache_path)
    after = read_staleness_cache(cache_path, "my-recipe")
    assert after is not None
    assert after.triage_result is None, (
        f"triage_result must be reset on content change. Got {after.triage_result!r}"
    )


# ---------------------------------------------------------------------------
# validate_recipe_cards tests
# ---------------------------------------------------------------------------


def test_validate_recipe_cards_clean(tmp_path: Path) -> None:
    recipes_dir = tmp_path / ".autoskillit" / "scripts"
    recipes_dir.mkdir(parents=True)
    pipeline = recipes_dir / "clean.yaml"
    pipeline.write_text(CLEAN_PIPELINE_YAML)

    contract = generate_recipe_card(pipeline, recipes_dir)

    findings = validate_recipe_cards(None, contract)
    assert len(findings) == 0


def test_validate_recipe_cards_missing_input(tmp_path: Path) -> None:
    recipes_dir = tmp_path / ".autoskillit" / "scripts"
    recipes_dir.mkdir(parents=True)
    pipeline = recipes_dir / "bad.yaml"
    pipeline.write_text(BAD_PIPELINE_YAML)

    contract = generate_recipe_card(pipeline, recipes_dir)

    findings = validate_recipe_cards(None, contract)
    assert len(findings) > 0
    assert any("worktree_path" in f["message"] for f in findings)


# ---------------------------------------------------------------------------
# T_SC1–T_SC5 contract assertion tests
# ---------------------------------------------------------------------------


def test_sc1_audit_impl_has_real_inputs_and_outputs() -> None:
    """T_SC1: audit-impl has non-empty inputs and declares verdict/remediation_path outputs."""
    manifest = load_bundled_manifest()
    audit_impl = manifest["skills"]["audit-impl"]
    assert audit_impl["inputs"], "audit-impl should have non-empty inputs"
    output_names = {o["name"] for o in audit_impl["outputs"]}
    assert "verdict" in output_names
    assert "remediation_path" in output_names
    verdict_out = next(o for o in audit_impl["outputs"] if o["name"] == "verdict")
    assert verdict_out["type"] == "string"
    remediation_out = next(o for o in audit_impl["outputs"] if o["name"] == "remediation_path")
    assert remediation_out["type"] == "file_path"


def test_sc2_resolve_failures_has_empty_outputs() -> None:
    """T_SC2: resolve-failures has outputs: []."""
    manifest = load_bundled_manifest()
    assert manifest["skills"]["resolve-failures"]["outputs"] == []


def test_sc3_dry_walkthrough_has_empty_outputs() -> None:
    """T_SC3: dry-walkthrough has outputs: []."""
    manifest = load_bundled_manifest()
    assert manifest["skills"]["dry-walkthrough"]["outputs"] == []


def test_sc4_investigate_has_empty_outputs() -> None:
    """T_SC4: investigate has outputs: []."""
    manifest = load_bundled_manifest()
    assert manifest["skills"]["investigate"]["outputs"] == []


def test_sc5_make_groups_outputs_include_group_files() -> None:
    """T_SC5: make-groups outputs list contains an entry with name='group_files'."""
    manifest = load_bundled_manifest()
    output_names = {o["name"] for o in manifest["skills"]["make-groups"]["outputs"]}
    assert "group_files" in output_names


def test_pipeline_summary_contract_declared() -> None:
    from autoskillit.core.paths import pkg_root

    contracts_path = pkg_root() / "recipe" / "skill_contracts.yaml"
    contracts = yaml.safe_load(contracts_path.read_text())
    assert "pipeline-summary" in contracts["skills"]
    skill = contracts["skills"]["pipeline-summary"]
    required_inputs = [i["name"] for i in skill["inputs"] if i.get("required", False)]
    assert "bug_report_path" in required_inputs
    assert "feature_branch" in required_inputs
    assert "target_branch" in required_inputs
    assert "workspace" in required_inputs
