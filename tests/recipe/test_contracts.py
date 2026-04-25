"""Tests for contract card generation and staleness validation."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from autoskillit.recipe.contracts import (
    check_contract_staleness,
    compute_skill_hash,
    generate_recipe_card,
    get_skill_contract,
    load_bundled_manifest,
    load_recipe_card,
    validate_recipe_cards,
)
from autoskillit.workspace import bundled_skills_extended_dir

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]

# ---------------------------------------------------------------------------
# Bundled manifest tests
# ---------------------------------------------------------------------------


def test_load_bundled_manifest() -> None:
    manifest = load_bundled_manifest()
    assert manifest["version"] == "0.1.0"
    assert len(manifest["skills"]) >= 39
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
        if "expected_output_patterns" in skill:
            assert isinstance(skill["expected_output_patterns"], list), (
                f"{skill_name}: expected_output_patterns must be a list"
            )


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


def test_resolve_skill_name_bash_placeholder_truncation() -> None:
    """Skill names truncated by a bash {placeholder} suffix must be treated as dynamic."""
    from autoskillit.recipe.contracts import resolve_skill_name

    # "/autoskillit:exp-lens-{slug}" — regex extracts "exp-lens-" (stops at {)
    # but the true name is dynamic; must return None to skip contract validation.
    assert resolve_skill_name("/autoskillit:exp-lens-{slug} {ctx} ${{ context.plan }}") is None


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
    retries: 0
    on_context_limit: retry
    on_success: done
    on_failure: done
  retry:
    tool: run_skill
    with:
      skill_command: >-
        /autoskillit:retry-worktree
        ${{ inputs.plan_path }}
        ${{ context.worktree_path }}
      cwd: "${{ context.worktree_path }}"
    on_success: done
    on_failure: done
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
    retries: 0
    on_context_limit: retry
    on_success: done
    on_failure: done
  retry:
    tool: run_skill
    with:
      skill_command: "/autoskillit:retry-worktree ${{ inputs.plan_path }}"
    on_success: done
    on_failure: done
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


def test_load_recipe_card_missing(tmp_path: Path) -> None:
    contract = load_recipe_card("nonexistent", tmp_path / "no-scripts")
    assert contract is None


# ---------------------------------------------------------------------------
# check_contract_staleness tests
# ---------------------------------------------------------------------------


def test_check_staleness_clean() -> None:
    contract = {
        "bundled_manifest_version": "0.1.0",
        "skill_hashes": {
            "investigate": compute_skill_hash(
                "investigate", skills_dir=bundled_skills_extended_dir()
            )
        },
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


def test_sc2_resolve_failures_declares_verdict_output() -> None:
    """T_SC2: resolve-failures declares a verdict output with allowed_values."""
    manifest = load_bundled_manifest()
    outputs = manifest["skills"]["resolve-failures"]["outputs"]
    output_names = {o["name"] for o in outputs}
    assert "verdict" in output_names, (
        "resolve-failures must declare a 'verdict' output after Part B implementation"
    )
    verdict_out = next(o for o in outputs if o["name"] == "verdict")
    assert "allowed_values" in verdict_out, "verdict output must have allowed_values"
    assert "real_fix" in verdict_out["allowed_values"]


def test_sc3_dry_walkthrough_has_empty_outputs() -> None:
    """T_SC3: dry-walkthrough has outputs: []."""
    manifest = load_bundled_manifest()
    assert manifest["skills"]["dry-walkthrough"]["outputs"] == []


def test_investigate_declares_investigation_path_output() -> None:
    """T_SC4 (replaced): investigate declares investigation_path in outputs."""
    manifest = load_bundled_manifest()
    output_names = {o["name"] for o in manifest["skills"]["investigate"]["outputs"]}
    assert "investigation_path" in output_names, (
        "investigate skill must declare investigation_path as an output so "
        "capture: blocks in recipes can reference it and the implicit-handoff "
        "semantic rule can enforce that the step has a capture block."
    )


def test_rectify_investigation_path_is_required() -> None:
    """T_SC_NEW: rectify.investigation_path input must be required: true."""
    manifest = load_bundled_manifest()
    rectify = manifest["skills"]["rectify"]
    inv_input = next((i for i in rectify["inputs"] if i["name"] == "investigation_path"), None)
    assert inv_input is not None, "rectify must have an investigation_path input"
    assert inv_input["required"] is True, (
        "rectify.investigation_path must be required: true — "
        "the pipeline contract is the only supported input path"
    )


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


# ---------------------------------------------------------------------------
# Contract coverage: file-producing skills must have output patterns
# ---------------------------------------------------------------------------

FILE_PRODUCING_SKILLS_WITH_CONTRACTS: list[str] = [
    "investigate",
    "make-plan",
    "rectify",
    "diagnose-ci",
    "review-approach",
    "audit-impl",
    "write-recipe",
    "make-groups",
    "triage-issues",
    "analyze-prs",
    "merge-pr",
    "prepare-pr",
    "compose-pr",
    "open-integration-pr",
    "implement-worktree",
    "implement-worktree-no-merge",
    "resolve-merge-conflicts",
    "retry-worktree",
    "review-pr",
    "arch-lens-c4-container",
    "arch-lens-concurrency",
    "arch-lens-data-lineage",
    "arch-lens-deployment",
    "arch-lens-development",
    "arch-lens-error-resilience",
    "arch-lens-module-dependency",
    "arch-lens-operational",
    "arch-lens-process-flow",
    "arch-lens-repository-access",
    "arch-lens-scenarios",
    "arch-lens-security",
    "arch-lens-state-lifecycle",
]


@pytest.mark.parametrize("skill_name", FILE_PRODUCING_SKILLS_WITH_CONTRACTS)
def test_file_producing_skill_has_output_patterns(skill_name: str) -> None:
    """Every skill with file_path outputs must have non-empty expected_output_patterns."""
    manifest = load_bundled_manifest()
    contract = get_skill_contract(skill_name, manifest)
    assert contract is not None, f"Skill '{skill_name}' is missing from skill_contracts.yaml"
    file_outputs = [o for o in contract.outputs if o.type == "file_path"]
    if file_outputs:
        assert contract.expected_output_patterns, (
            f"Skill '{skill_name}' has {len(file_outputs)} file_path output(s) "
            f"but no expected_output_patterns."
        )


def test_generate_recipe_card_includes_output_patterns(tmp_path: Path) -> None:
    """Recipe card serialization must preserve expected_output_patterns."""
    manifest = load_bundled_manifest()
    contract = get_skill_contract("compose-pr", manifest)
    assert contract is not None
    assert contract.expected_output_patterns, "Precondition: compose-pr must have patterns"

    from autoskillit.core.paths import pkg_root

    recipe_path = pkg_root() / "recipes" / "implementation.yaml"
    if not recipe_path.exists():
        pytest.skip("implementation recipe not found")

    recipes_dir = tmp_path / "recipes"
    recipes_dir.mkdir()
    card = generate_recipe_card(recipe_path, recipes_dir)
    card_skills = card.get("skills", {})
    compose_pr_card = card_skills.get("compose-pr")
    assert compose_pr_card is not None, "compose-pr must be used in implementation recipe"

    assert "expected_output_patterns" in compose_pr_card, (
        "generate_recipe_card() must include expected_output_patterns"
    )
    assert compose_pr_card["expected_output_patterns"], (
        "expected_output_patterns must be non-empty in the card"
    )


# ---------------------------------------------------------------------------
# write_behavior contract tests
# ---------------------------------------------------------------------------


def test_write_behavior_always_loaded() -> None:
    """make-plan contract declares write_behavior='always' with no patterns."""
    manifest = load_bundled_manifest()
    contract = get_skill_contract("make-plan", manifest)
    assert contract is not None
    assert contract.write_behavior == "always"
    assert contract.write_expected_when == []


def test_write_behavior_conditional_loaded() -> None:
    """resolve-merge-conflicts declares conditional write_behavior with patterns."""
    manifest = load_bundled_manifest()
    contract = get_skill_contract("resolve-merge-conflicts", manifest)
    assert contract is not None
    assert contract.write_behavior == "conditional"
    assert len(contract.write_expected_when) > 0
    assert any("conflict_report_path" in p for p in contract.write_expected_when)


def test_write_behavior_defaults_to_none() -> None:
    """investigate has no write_behavior — defaults to None."""
    manifest = load_bundled_manifest()
    contract = get_skill_contract("investigate", manifest)
    assert contract is not None
    assert contract.write_behavior is None


ALWAYS_WRITE_SKILLS = {
    "build-execution-map",
    "compose-research-pr",
    "design-guards",
    "dry-walkthrough",
    "generate-report",
    "implement-experiment",
    "implement-worktree",
    "make-campaign",
    "make-plan",
    "plan-experiment",
    "plan-visualization",
    "prepare-research-pr",
    "report-bug",
    "resolve-design-review",
    "review-design",
    "run-experiment",
    "scope",
    "stage-data",
    "troubleshoot-experiment",
    "write-recipe",
}


@pytest.mark.parametrize("skill_name", sorted(ALWAYS_WRITE_SKILLS))
def test_every_always_write_skill_has_contract(skill_name: str) -> None:
    """Every skill that should always write must declare write_behavior='always'."""
    manifest = load_bundled_manifest()
    contract = get_skill_contract(skill_name, manifest)
    assert contract is not None, f"Skill '{skill_name}' missing from skill_contracts.yaml"
    assert contract.write_behavior == "always", (
        f"Skill '{skill_name}' expected write_behavior='always', got '{contract.write_behavior}'"
    )


def test_always_write_skills_matches_yaml() -> None:
    """ALWAYS_WRITE_SKILLS test set must equal the set from skill_contracts.yaml."""
    manifest = load_bundled_manifest()
    yaml_always = {
        name
        for name, data in manifest.get("skills", {}).items()
        if data.get("write_behavior") == "always"
    }
    assert ALWAYS_WRITE_SKILLS == yaml_always, (
        f"ALWAYS_WRITE_SKILLS is out of sync with skill_contracts.yaml.\n"
        f"In test but not YAML: {ALWAYS_WRITE_SKILLS - yaml_always}\n"
        f"In YAML but not test: {yaml_always - ALWAYS_WRITE_SKILLS}"
    )


# Skills that write conditionally — write expected only when the completion
# token indicates actual work was performed.
CONDITIONAL_WRITE_SKILLS: dict[str, str] = {
    # skill_name → substring that must appear in write_expected_when patterns
    "compose-pr": "pr_url",
    "diagnose-ci": "diagnosis_path",
    "implement-worktree-no-merge": "worktree_path",
    "rectify": "plan_path",
    "resolve-failures": "verdict",
    "resolve-merge-conflicts": "conflict_report_path",
    "resolve-review": "verdict",
    "retry-worktree": "phases_implemented",
    "resolve-claims-review": "verdict",
    "resolve-research-review": "verdict",
}


@pytest.mark.parametrize("skill_name,pattern_substring", sorted(CONDITIONAL_WRITE_SKILLS.items()))
def test_every_conditional_write_skill_has_correct_contract(
    skill_name: str, pattern_substring: str
) -> None:
    """Skills with legitimate no-write exits must declare write_behavior='conditional'.

    Each conditional skill must have at least one write_expected_when pattern
    containing the expected token substring. This prevents regression to 'always'.
    """
    manifest = load_bundled_manifest()
    contract = get_skill_contract(skill_name, manifest)
    assert contract is not None, f"Skill '{skill_name}' missing from skill_contracts.yaml"
    assert contract.write_behavior == "conditional", (
        f"Skill '{skill_name}' must use write_behavior='conditional'. "
        f"It has a legitimate no-write success path. Got: '{contract.write_behavior}'"
    )
    assert len(contract.write_expected_when) > 0, (
        f"Skill '{skill_name}': conditional mode requires non-empty write_expected_when"
    )
    assert any(pattern_substring in p for p in contract.write_expected_when), (
        f"Skill '{skill_name}': write_expected_when must contain a pattern with "
        f"'{pattern_substring}' (the structured completion token)"
    )


# ---------------------------------------------------------------------------
# REQ-C4-02: DataFlowEntry rename
# ---------------------------------------------------------------------------


def test_prepare_pr_contract_is_conditional() -> None:
    """prepare-pr must be conditional — it has a documented no-write exit path."""
    manifest = load_bundled_manifest()
    contract = get_skill_contract("prepare-pr", manifest)
    assert contract is not None
    assert contract.write_behavior == "conditional"
    assert contract.write_expected_when


def test_bundle_local_report_contract_is_conditional() -> None:
    """bundle-local-report must be conditional — it has a documented no-write exit path."""
    manifest = load_bundled_manifest()
    contract = get_skill_contract("bundle-local-report", manifest)
    assert contract is not None
    assert contract.write_behavior == "conditional"
    assert contract.write_expected_when


def test_dataflow_entry_uppercase_f() -> None:
    """DataFlowEntry (uppercase F) must be importable; old DataflowEntry must be gone."""
    import autoskillit.recipe.contracts as m
    from autoskillit.recipe.contracts import DataFlowEntry  # must not raise

    assert not hasattr(m, "DataflowEntry"), "DataflowEntry (lowercase f) must be removed"
    entry = DataFlowEntry(step="s", available=[], required=[], produced=[])
    assert entry.step == "s"
