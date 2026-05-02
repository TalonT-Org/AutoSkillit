"""General structural invariants for all bundled recipe YAML files."""

from __future__ import annotations

from pathlib import Path

import pytest
import structlog.testing

from autoskillit.core import SKILL_TOOLS
from autoskillit.recipe._analysis import build_recipe_graph
from autoskillit.recipe.contracts import load_bundled_manifest
from autoskillit.recipe.io import builtin_recipes_dir, load_recipe
from autoskillit.recipe.rules_merge import _is_commit_guard
from autoskillit.recipe.validator import run_semantic_rules

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
SMOKE_RECIPE = PROJECT_ROOT / ".autoskillit" / "recipes" / "smoke-test.yaml"


def _resolve_recipe_path(name: str) -> Path:
    """Resolve recipe name to path, handling project-local smoke-test."""
    if name == "smoke-test":
        return SMOKE_RECIPE
    return builtin_recipes_dir() / f"{name}.yaml"


_ALL_CLONE_RECIPE_PATHS: list[Path] = []
for _dir in (builtin_recipes_dir(), PROJECT_ROOT / ".autoskillit" / "recipes"):
    if _dir.is_dir():
        for _p in sorted(_dir.glob("*.yaml")):
            _wf = load_recipe(_p)
            if any(s.tool == "clone_repo" for s in _wf.steps.values()):
                _ALL_CLONE_RECIPE_PATHS.append(_p)

_CI_WATCH_CYCLE_STEPS = {"ci_watch", "handle_no_ci_runs", "check_ci_loop"}


def test_every_bundled_recipe_declares_requires_packs() -> None:
    """All top-level bundled recipes must declare a non-empty requires_packs."""
    for path in sorted(builtin_recipes_dir().glob("*.yaml")):
        recipe = load_recipe(path)
        assert recipe.requires_packs, f"{path.name} does not declare requires_packs"


@pytest.mark.parametrize("recipe_name", ["remediation", "implementation", "implementation-groups"])
def test_bundled_recipe_no_unbounded_cycle_findings(recipe_name: str) -> None:
    """Pipeline recipes must have zero unbounded-cycle findings for the ci_watch cycle."""
    recipe = load_recipe(builtin_recipes_dir() / f"{recipe_name}.yaml")
    findings = run_semantic_rules(recipe)
    ci_watch_cycle_findings = [
        f for f in findings if f.rule == "unbounded-cycle" and f.step_name in _CI_WATCH_CYCLE_STEPS
    ]
    assert ci_watch_cycle_findings == [], (
        f"{recipe_name} has unbounded-cycle findings for ci_watch cycle: "
        + "; ".join(f.message for f in ci_watch_cycle_findings)
    )


def test_make_plan_contract_declares_plan_parts_output() -> None:
    """D4: make-plan contract must declare plan_parts as an output."""
    manifest = load_bundled_manifest()
    make_plan = manifest.get("skills", {}).get("make-plan", {})
    output_names = [o["name"] for o in make_plan.get("outputs", [])]
    assert "plan_parts" in output_names, (
        "make-plan contract must declare plan_parts as an output "
        "so capture_list coverage validation can enforce it"
    )


def test_rectify_contract_declares_plan_parts_output() -> None:
    """D5: rectify contract must declare plan_parts as an output."""
    manifest = load_bundled_manifest()
    rectify = manifest.get("skills", {}).get("rectify", {})
    output_names = [o["name"] for o in rectify.get("outputs", [])]
    assert "plan_parts" in output_names


def test_bundled_recipes_diagrams_dir_exists() -> None:
    """Diagrams directory exists for bundled recipes."""
    from autoskillit.core.paths import pkg_root

    assert (pkg_root() / "recipes" / "diagrams").is_dir()


def test_all_predicate_steps_have_on_failure() -> None:
    """Every tool/python step with on_result.conditions must declare on_failure."""
    paths = {
        "implementation": builtin_recipes_dir() / "implementation.yaml",
        "remediation": builtin_recipes_dir() / "remediation.yaml",
        "smoke-test": SMOKE_RECIPE,
    }
    for recipe_name, recipe_path in paths.items():
        recipe = load_recipe(recipe_path)
        for step_name, step in recipe.steps.items():
            is_tool = step.tool is not None or step.python is not None
            if is_tool and step.on_result and step.on_result.conditions:
                assert step.on_failure is not None, (
                    f"{recipe_name}.{step_name}: predicate step must declare on_failure"
                )


def test_audit_impl_on_failure_routes_to_escalation() -> None:
    """audit_impl.on_failure must route to an escalation step in each recipe."""
    impl = load_recipe(builtin_recipes_dir() / "implementation.yaml")
    rem = load_recipe(builtin_recipes_dir() / "remediation.yaml")
    assert impl.steps["audit_impl"].on_failure == "escalate_stop"
    assert rem.steps["audit_impl"].on_failure == "escalate_stop"


@pytest.mark.parametrize(
    "recipe_name,yaml_name",
    [
        ("implementation", "implementation.yaml"),
        ("remediation", "remediation.yaml"),
        ("merge-prs", "merge-prs.yaml"),
        ("implementation-groups", "implementation-groups.yaml"),
    ],
)
def test_audit_ingredient_defaults_to_false(recipe_name: str, yaml_name: str) -> None:
    """audit must default to 'false' (OFF) in all recipes — opt-in, not opt-out."""
    recipe = load_recipe(builtin_recipes_dir() / yaml_name)
    audit_ing = recipe.ingredients.get("audit")
    assert audit_ing is not None, f"{recipe_name}: 'audit' ingredient not found"
    assert audit_ing.default == "false", (
        f"{recipe_name}: audit.default must be 'false' (OFF by default), got {audit_ing.default!r}"
    )


def test_audit_impl_skill_md_emits_verdict_and_remediation_path() -> None:
    """1b: audit-impl SKILL.md must contain verdict and remediation_path emit lines."""
    from autoskillit.core.paths import pkg_root

    content = (pkg_root() / "skills_extended" / "audit-impl" / "SKILL.md").read_text()
    assert "verdict = " in content, "audit-impl SKILL.md missing 'verdict = ' emit line"
    assert "remediation_path = " in content, (
        "audit-impl SKILL.md missing 'remediation_path = ' emit line"
    )


def test_review_approach_skill_md_emits_review_path() -> None:
    """1c: review-approach SKILL.md must contain review_path emit line."""
    from autoskillit.core.paths import pkg_root

    content = (pkg_root() / "skills_extended" / "review-approach" / "SKILL.md").read_text()
    assert "review_path = " in content, (
        "review-approach SKILL.md missing 'review_path = ' emit line"
    )


def test_make_groups_skill_md_emits_group_files() -> None:
    """1d: make-groups SKILL.md must contain group_files, groups_path, manifest_path lines."""
    from autoskillit.core.paths import pkg_root

    content = (pkg_root() / "skills_extended" / "make-groups" / "SKILL.md").read_text()
    assert "group_files = " in content, "make-groups SKILL.md missing 'group_files = ' emit line"
    assert "groups_path = " in content, "make-groups SKILL.md missing 'groups_path = ' emit line"
    assert "manifest_path = " in content, (
        "make-groups SKILL.md missing 'manifest_path = ' emit line"
    )


def test_bundled_recipes_pass_uncaptured_handoff_consumer() -> None:
    """1i: all bundled recipes must produce zero uncaptured-handoff-consumer findings."""
    for yaml_file in sorted(builtin_recipes_dir().glob("*.yaml")):
        recipe = load_recipe(yaml_file)
        findings = run_semantic_rules(recipe)
        handoff_findings = [f for f in findings if f.rule == "uncaptured-handoff-consumer"]
        assert not handoff_findings, f"{yaml_file.name}: {handoff_findings}"


def test_telemetry_before_open_pr_rule_not_in_registry() -> None:
    """The telemetry-before-open-pr rule must not be in the rule registry.

    This rule was removed because open-pr now self-retrieves token telemetry
    from disk using cwd_filter (Step 0b). If this test fails, the rule was
    re-added to the registry and would silently fire on bundled production recipes.
    """
    import autoskillit.recipe  # noqa: F401 — triggers rule registration
    from autoskillit.recipe.registry import _RULE_REGISTRY

    rule_names = {spec.name for spec in _RULE_REGISTRY}
    assert "telemetry-before-open-pr" not in rule_names, (
        "telemetry-before-open-pr was re-added to the registry; "
        "open-pr self-retrieves token telemetry via cwd_filter — "
        "this rule is no longer needed and must not be registered"
    )


def test_bundled_recipes_pass_unrouted_verdict_value_rule() -> None:
    """All bundled recipes must pass the unrouted-verdict-value semantic rule."""
    for yaml_path in sorted(builtin_recipes_dir().glob("*.yaml")):
        recipe = load_recipe(yaml_path)
        findings = run_semantic_rules(recipe)
        verdict_errors = [f for f in findings if f.rule == "unrouted-verdict-value"]
        assert len(verdict_errors) == 0, (
            f"Recipe '{yaml_path.stem}' has unrouted verdict values: "
            + ", ".join(f.message for f in verdict_errors)
        )


class TestBaseBranchDefaults:
    @pytest.mark.parametrize(
        "recipe_name",
        [
            "implementation",
            "remediation",
            "implementation-groups",
            "merge-prs",
        ],
    )
    def test_recipe_base_branch_auto_detects(self, recipe_name: str) -> None:
        """Non-exempt bundled recipes must use auto-detect for base_branch."""
        recipe = load_recipe(builtin_recipes_dir() / f"{recipe_name}.yaml")
        assert recipe.ingredients["base_branch"].default == "", (
            f"{recipe_name}.yaml: base_branch must use auto-detect (default: '')"
        )

    def test_smoke_test_base_branch_uses_auto_detect(self) -> None:
        """smoke-test.yaml must use auto-detect for base_branch so config-resolved value wins."""
        recipe = load_recipe(SMOKE_RECIPE)
        assert recipe.ingredients["base_branch"].default == "", (
            "smoke-test.yaml base_branch must use auto-detect (default: '') so that "
            "branching.default_base_branch from config is honoured at runtime (#703)"
        )


class TestImplementationRecipeMergeQueueRule:
    """implementation.yaml kitchen_rules must reference merge queue detection."""

    @pytest.fixture(scope="class")
    def recipe(self):
        return load_recipe(builtin_recipes_dir() / "implementation.yaml")

    def test_kitchen_rules_mention_check_repo_merge_state(self, recipe) -> None:
        all_rules = " ".join(recipe.kitchen_rules)
        assert "check_repo_merge_state" in all_rules, (
            "implementation.yaml kitchen_rules must reference check_repo_merge_state"
        )
        assert "MERGE ROUTING" in all_rules, (
            "implementation.yaml kitchen_rules must contain a MERGE ROUTING rule"
        )

    def test_kitchen_rules_prohibit_direct_gh_pr_merge(self, recipe) -> None:
        # Find the specific rule that mentions "gh pr merge" and check for
        # prohibition language within that rule, not across all rules.
        merge_rules = [r for r in recipe.kitchen_rules if "gh pr merge" in r]
        assert merge_rules, (
            "implementation.yaml kitchen_rules must contain a rule mentioning 'gh pr merge'"
        )
        has_prohibition = any(
            any(phrase in rule.lower() for phrase in ("never", "prohibited", "do not"))
            for rule in merge_rules
        )
        assert has_prohibition, (
            "implementation.yaml kitchen_rules must explicitly prohibit calling "
            "gh pr merge directly outside of recipe steps"
        )


_BUNDLED_RECIPE_PATHS = sorted(builtin_recipes_dir().glob("*.yaml"))


@pytest.mark.parametrize("recipe_path", _BUNDLED_RECIPE_PATHS, ids=lambda p: p.stem)
def test_bundled_recipes_emit_no_graph_warnings(recipe_path):
    """WF7: build_recipe_graph emits zero warnings for all bundled recipes."""
    recipe = load_recipe(recipe_path)
    with structlog.testing.capture_logs() as cap_logs:
        build_recipe_graph(recipe)
    warning_events = [entry for entry in cap_logs if entry.get("log_level") == "warning"]
    assert warning_events == [], (
        f"build_recipe_graph emitted {len(warning_events)} warnings for "
        f"{recipe_path.name}: {warning_events}"
    )


@pytest.mark.parametrize("recipe_path", _BUNDLED_RECIPE_PATHS, ids=lambda p: p.stem)
def test_all_advisory_run_skill_steps_have_on_context_limit(recipe_path):
    """
    Every run_skill step with skip_when_false must declare on_context_limit.
    A step that can be skipped by configuration must also be skippable on context limit.
    """
    recipe = load_recipe(recipe_path)
    violations = [
        name
        for name, step in recipe.steps.items()
        if step.tool in SKILL_TOOLS
        and step.skip_when_false is not None
        and step.on_context_limit is None
    ]
    assert violations == [], (
        f"Advisory run_skill steps in {recipe_path.name} missing on_context_limit: "
        f"{violations}. Set on_context_limit to the appropriate skip/recovery step."
    )


@pytest.mark.parametrize(
    "recipe_name",
    ["implementation", "remediation", "implementation-groups", "merge-prs"],
)
def test_review_pr_step_passes_annotated_diff_inputs(recipe_name: str) -> None:
    """Every review-pr invocation must pass annotated_diff_path= and hunk_ranges_path=
    in skill_command, or have a reachable annotate_pr_diff predecessor step."""
    recipe = load_recipe(builtin_recipes_dir() / f"{recipe_name}.yaml")
    # Find review-pr steps
    review_steps = [
        (name, step)
        for name, step in recipe.steps.items()
        if step.tool in SKILL_TOOLS and "review-pr" in step.with_args.get("skill_command", "")
    ]
    assert review_steps, f"No review-pr step found in {recipe_name}.yaml"
    for step_name, step in review_steps:
        cmd = step.with_args.get("skill_command", "")
        has_inline = "annotated_diff_path=" in cmd and "hunk_ranges_path=" in cmd
        has_predecessor = any(
            s.with_args.get("callable", "") == "autoskillit.smoke_utils.annotate_pr_diff"
            for s in recipe.steps.values()
            if s.tool == "run_python"
        )
        assert has_inline or has_predecessor, (
            f"{recipe_name}.yaml: step '{step_name}' invokes review-pr but neither "
            f"passes annotated_diff_path=/hunk_ranges_path= inline nor has an "
            f"annotate_pr_diff predecessor step"
        )


@pytest.mark.parametrize(
    "recipe_name",
    ["implementation", "remediation", "implementation-groups", "merge-prs"],
)
def test_annotate_pr_diff_captures_both_paths(recipe_name: str) -> None:
    """The annotate_pr_diff step must capture annotated_diff_path and hunk_ranges_path."""
    recipe = load_recipe(builtin_recipes_dir() / f"{recipe_name}.yaml")
    annotate_steps = [
        (name, step)
        for name, step in recipe.steps.items()
        if step.tool == "run_python"
        and step.with_args.get("callable", "") == "autoskillit.smoke_utils.annotate_pr_diff"
    ]
    assert annotate_steps, f"No annotate_pr_diff step found in {recipe_name}.yaml"
    for step_name, step in annotate_steps:
        assert step.capture is not None, (
            f"{recipe_name}.yaml: annotate_pr_diff step '{step_name}' has no capture block"
        )
        assert "annotated_diff_path" in step.capture, (
            f"{recipe_name}.yaml: annotate_pr_diff step '{step_name}' must capture "
            f"annotated_diff_path"
        )
        assert "hunk_ranges_path" in step.capture, (
            f"{recipe_name}.yaml: annotate_pr_diff step '{step_name}' must capture "
            f"hunk_ranges_path"
        )


class TestRunModeIngredient:
    """REQ-INGREDIENT-001 through REQ-INGREDIENT-005: run_mode ingredient in multi-issue recipes."""  # noqa: E501

    @pytest.fixture(scope="class")
    def impl_recipe(self):
        return load_recipe(builtin_recipes_dir() / "implementation.yaml")

    @pytest.fixture(scope="class")
    def remed_recipe(self):
        return load_recipe(builtin_recipes_dir() / "remediation.yaml")

    def test_implementation_has_run_mode_ingredient(self, impl_recipe) -> None:
        """REQ-INGREDIENT-001: implementation.yaml declares run_mode ingredient."""
        assert "run_mode" in impl_recipe.ingredients, (
            "implementation.yaml must declare run_mode ingredient"
        )

    def test_implementation_run_mode_default_is_sequential(self, impl_recipe) -> None:
        """REQ-INGREDIENT-002: run_mode defaults to 'sequential'."""
        ing = impl_recipe.ingredients["run_mode"]
        assert ing.default == "sequential", (
            "implementation.yaml run_mode must default to 'sequential'"
        )

    def test_implementation_run_mode_description_mentions_parallel(self, impl_recipe) -> None:
        """REQ-INGREDIENT-001: description must document 'parallel' as a valid option."""
        ing = impl_recipe.ingredients["run_mode"]
        assert "parallel" in ing.description.lower(), (
            "run_mode description must mention 'parallel' as an option"
        )

    def test_remediation_has_run_mode_ingredient(self, remed_recipe) -> None:
        """REQ-INGREDIENT-001: remediation.yaml declares run_mode ingredient."""
        assert "run_mode" in remed_recipe.ingredients, (
            "remediation.yaml must declare run_mode ingredient"
        )

    def test_remediation_run_mode_default_is_sequential(self, remed_recipe) -> None:
        """REQ-INGREDIENT-002: run_mode defaults to 'sequential'."""
        ing = remed_recipe.ingredients["run_mode"]
        assert ing.default == "sequential", (
            "remediation.yaml run_mode must default to 'sequential'"
        )

    def test_remediation_run_mode_description_mentions_parallel(self, remed_recipe) -> None:
        """REQ-INGREDIENT-001: description must document 'parallel' as a valid option."""
        ing = remed_recipe.ingredients["run_mode"]
        assert "parallel" in ing.description.lower(), (
            "run_mode description must mention 'parallel' as an option"
        )


class TestMaxParallelIngredient:
    """REQ-ING-001, REQ-ING-002, REQ-ING-003"""

    @pytest.mark.parametrize("recipe_name", ["implementation", "remediation"])
    def test_recipe_has_max_parallel_ingredient(self, recipe_name: str) -> None:
        recipe = load_recipe(builtin_recipes_dir() / f"{recipe_name}.yaml")
        assert "max_parallel" in recipe.ingredients

    @pytest.mark.parametrize("recipe_name", ["implementation", "remediation"])
    def test_max_parallel_defaults_to_six(self, recipe_name: str) -> None:
        recipe = load_recipe(builtin_recipes_dir() / f"{recipe_name}.yaml")
        ing = recipe.ingredients["max_parallel"]
        assert ing.default == "6"

    @pytest.mark.parametrize("recipe_name", ["implementation", "remediation"])
    def test_max_parallel_is_hidden(self, recipe_name: str) -> None:
        recipe = load_recipe(builtin_recipes_dir() / f"{recipe_name}.yaml")
        ing = recipe.ingredients["max_parallel"]
        assert ing.hidden is True

    @pytest.mark.parametrize("recipe_name", ["implementation", "remediation"])
    def test_max_parallel_description_mentions_parallel_groups(self, recipe_name: str) -> None:
        recipe = load_recipe(builtin_recipes_dir() / f"{recipe_name}.yaml")
        ing = recipe.ingredients["max_parallel"]
        assert "parallel" in ing.description.lower()
        assert "group" in ing.description.lower()


def test_no_bare_temp_paths_in_bundled_recipe_notes() -> None:
    """No bundled recipe YAML should reference temp/ without .autoskillit/ prefix.

    Bare temp/ references are incorrect; all project-local temp output must be
    rooted under .autoskillit/temp/ per CLAUDE.md §3.2.
    """
    import re

    recipes_dir = builtin_recipes_dir()
    bare_temp = re.compile(r"(?<!\.autoskillit/)temp/")

    violations: list[str] = []
    for yaml_file in sorted(recipes_dir.glob("*.yaml")):
        text = yaml_file.read_text()
        for lineno, line in enumerate(text.splitlines(), start=1):
            if bare_temp.search(line):
                violations.append(f"{yaml_file.name}:{lineno}: {line.strip()}")

    assert not violations, (
        "Bundled recipe YAML files contain bare temp/ path references.\n"
        "Replace with .autoskillit/temp/ per CLAUDE.md §3.2:\n" + "\n".join(violations)
    )


@pytest.mark.parametrize(
    "recipe_name",
    ["implementation", "remediation", "implementation-groups", "merge-prs", "smoke-test"],
)
def test_recipe_has_no_defer_cleanup_ingredient(recipe_name: str) -> None:
    """Recipes must not declare 'defer_cleanup' — that design is removed."""
    recipe = load_recipe(_resolve_recipe_path(recipe_name))
    assert "defer_cleanup" not in recipe.ingredients, (
        f"{recipe_name}.yaml must not declare 'defer_cleanup'"
    )


@pytest.mark.parametrize(
    "recipe_name",
    ["implementation", "remediation", "implementation-groups", "merge-prs", "smoke-test"],
)
def test_recipe_has_no_registry_path_ingredient(recipe_name: str) -> None:
    """Recipes must not declare 'registry_path' — replaced by a well-known default."""
    recipe = load_recipe(_resolve_recipe_path(recipe_name))
    assert "registry_path" not in recipe.ingredients, (
        f"{recipe_name}.yaml must not declare 'registry_path'"
    )


@pytest.mark.parametrize(
    "recipe_name",
    ["implementation", "remediation", "implementation-groups", "merge-prs", "smoke-test"],
)
def test_recipe_has_no_interactive_cleanup_steps(recipe_name: str) -> None:
    """Recipes must not have confirm_cleanup or delete_clone — these blocked unattended runs."""
    recipe = load_recipe(_resolve_recipe_path(recipe_name))
    assert "confirm_cleanup" not in recipe.steps, (
        f"{recipe_name}.yaml must not have 'confirm_cleanup' step"
    )
    assert "delete_clone" not in recipe.steps, (
        f"{recipe_name}.yaml must not have 'delete_clone' step"
    )


@pytest.mark.parametrize(
    "recipe_name", ["implementation", "remediation", "implementation-groups", "merge-prs"]
)
def test_recipe_has_unconditional_register_steps(recipe_name: str) -> None:
    """register_clone_success routes to done; register_clone_failure routes to escalate_stop."""
    recipe = load_recipe(builtin_recipes_dir() / f"{recipe_name}.yaml")
    assert "register_clone_success" in recipe.steps
    assert "register_clone_failure" in recipe.steps
    s = recipe.steps["register_clone_success"]
    f = recipe.steps["register_clone_failure"]
    assert s.on_success == "done"
    assert f.on_success == "escalate_stop"
    assert f.on_failure == "escalate_stop"
    assert "check_defer_cleanup" not in recipe.steps
    assert "check_defer_on_failure" not in recipe.steps


@pytest.mark.parametrize(
    "recipe_name",
    ["implementation.yaml", "implementation-groups.yaml", "remediation.yaml"],
)
def test_re_push_steps_have_force_true(recipe_name: str) -> None:
    """All re_push/* steps must have force='true'.

    Post-rebase push requires --force-with-lease.
    """
    recipe = load_recipe(builtin_recipes_dir() / recipe_name)
    for step_name in (
        "re_push",
        "re_push_queue_fix",
        "re_push_direct_fix",
        "re_push_immediate_fix",
    ):
        assert step_name in recipe.steps, f"Expected step {step_name!r} in {recipe_name}"
        step = recipe.steps[step_name]
        assert step.tool == "push_to_remote"
        assert step.with_args.get("force") == "true", (
            f"{step_name} in {recipe_name} must include force='true' — "
            "post-rebase push requires --force-with-lease"
        )


def test_bundled_recipes_have_no_ci_hardcoded_workflow() -> None:
    """No bundled recipe should hardcode workflow in wait_for_ci steps."""
    for yaml_path in sorted(builtin_recipes_dir().glob("*.yaml")):
        recipe = load_recipe(yaml_path)
        findings = run_semantic_rules(recipe)
        wf_findings = [f for f in findings if f.rule == "ci-hardcoded-workflow"]
        assert wf_findings == [], f"{recipe.name} has hardcoded workflow: {wf_findings}"


def test_merge_worktree_has_commit_guard_predecessor() -> None:
    """Every merge_worktree step in bundled recipes must have a commit_guard predecessor.

    A commit_guard step auto-commits any dirty files before the merge_worktree
    dirty-tree gate runs, providing structural immunity to context-exhausted skills
    that leave edits on disk without committing them.
    """
    from autoskillit.recipe.validator import make_validation_context

    for yaml_path in sorted(builtin_recipes_dir().glob("*.yaml")):
        recipe = load_recipe(yaml_path)
        merge_steps = [n for n, s in recipe.steps.items() if s.tool == "merge_worktree"]
        if not merge_steps:
            continue
        ctx = make_validation_context(recipe)
        for step_name in merge_steps:
            preds = ctx.predecessors.get(step_name, set())
            has_guard = any(_is_commit_guard(p, ctx) for p in preds)
            assert has_guard, (
                f"{yaml_path.name}: merge_worktree step '{step_name}' has no commit_guard "
                f"predecessor. Add a commit_guard run_cmd step immediately before merge. "
                f"Predecessors: {sorted(preds)}"
            )


def test_resolve_failures_steps_have_on_context_limit() -> None:
    """Every step invoking resolve-failures must declare on_context_limit.

    Without on_context_limit, context exhaustion mid-fix falls through to
    on_failure, discarding all uncommitted edits and losing partial progress.
    """
    from autoskillit.recipe.contracts import resolve_skill_name

    for yaml_path in sorted(builtin_recipes_dir().glob("*.yaml")):
        recipe = load_recipe(yaml_path)
        for step_name, step in recipe.steps.items():
            if step.tool not in SKILL_TOOLS:
                continue
            skill_cmd = step.with_args.get("skill_command", "")
            skill = resolve_skill_name(skill_cmd)
            if skill != "resolve-failures":
                continue
            assert step.on_context_limit is not None, (
                f"{yaml_path.name}: step '{step_name}' invokes resolve-failures "
                f"but has no on_context_limit. Context exhaustion will strand uncommitted "
                f"edits on disk and fall through to on_failure, losing progress."
            )


@pytest.mark.parametrize("recipe_path", _ALL_CLONE_RECIPE_PATHS, ids=lambda p: p.stem)
def test_all_clone_recipes_use_context_cwd_after_clone(recipe_path: Path) -> None:
    """After clone_repo, no step should use inputs.* as cwd."""
    import re

    input_re = re.compile(r"\$\{\{\s*inputs\.\w+\s*\}\}")
    recipe = load_recipe(recipe_path)

    seen_clone = False
    for name, step in recipe.steps.items():
        if step.tool == "clone_repo":
            seen_clone = True
            continue
        if seen_clone and step.with_args:
            cwd = step.with_args.get("cwd", "")
            assert not input_re.search(cwd), (
                f"{recipe_path.stem}: step '{name}' uses inputs.* as cwd after clone: {cwd}"
            )


@pytest.mark.parametrize("recipe_name", ["implementation", "implementation-groups", "remediation"])
def test_bundled_recipes_have_no_release_issue_on_unconfirmed_merge(recipe_name: str) -> None:
    """No bundled recipe may route a merge-wait timeout exit to release_issue.

    When wait_for_merge_queue (or equivalent) times out, the PR may still be
    in the queue. Calling release_issue on that path removes the in-progress
    label while the merge is still pending, leaving the issue visually unclaimed.
    """
    recipe = load_recipe(builtin_recipes_dir() / f"{recipe_name}.yaml")
    findings = run_semantic_rules(recipe)
    violations = [f for f in findings if f.rule == "release-issue-on-unconfirmed-merge"]
    assert violations == [], (
        f"{recipe_name} has release-issue-on-unconfirmed-merge violations: {violations}"
    )


@pytest.mark.parametrize("recipe_name", ["remediation", "implementation", "implementation-groups"])
def test_pre_ci_watch_mergeable_check_exists(recipe_name: str) -> None:
    """A check_pr_state step must exist between check_repo_ci_event and ci_watch."""
    recipe = load_recipe(builtin_recipes_dir() / f"{recipe_name}.yaml")
    assert "check_pr_state" in recipe.steps
    pr_state = recipe.steps["check_pr_state"]
    assert pr_state.tool == "check_pr_mergeable"
    assert pr_state.on_result is not None
    routes = {c.route for c in pr_state.on_result.conditions}
    assert "ci_watch" in routes, "MERGEABLE must route to ci_watch"
    conflicting_routes = {
        c.route for c in pr_state.on_result.conditions if c.when and "CONFLICTING" in c.when
    }
    assert conflicting_routes, "CONFLICTING condition must be handled"
    assert "ci_watch" not in conflicting_routes, "CONFLICTING must not route to ci_watch"
    ci_event_step = recipe.steps["check_repo_ci_event"]
    assert ci_event_step.on_success == "check_pr_state"


@pytest.mark.parametrize("recipe_name", ["remediation", "implementation", "implementation-groups"])
def test_active_ci_trigger_step_exists(recipe_name: str) -> None:
    """escalate_stop_no_ci predecessor must attempt active CI trigger before stopping."""
    recipe = load_recipe(builtin_recipes_dir() / f"{recipe_name}.yaml")
    assert "trigger_ci_actively" in recipe.steps
    trigger = recipe.steps["trigger_ci_actively"]
    assert trigger.tool == "run_cmd"
    assert "check_ci_loop" in recipe.steps, "check_ci_loop step missing from recipe"
    guard = recipe.steps["check_ci_loop"]
    guard_routes = {c.route for c in guard.on_result.conditions}
    assert "check_active_trigger_loop" in guard_routes
    assert "check_active_trigger_loop" in recipe.steps, (
        "check_active_trigger_loop guard step missing"
    )
    trigger_guard = recipe.steps["check_active_trigger_loop"]
    trigger_guard_routes = {c.route for c in trigger_guard.on_result.conditions}
    assert "trigger_ci_actively" in trigger_guard_routes


@pytest.mark.parametrize("recipe_name", ["remediation", "implementation", "implementation-groups"])
def test_reenroll_stalled_pr_has_loop_guard(recipe_name: str) -> None:
    """reenroll_stalled_pr cycle must have an iteration guard."""
    recipe = load_recipe(builtin_recipes_dir() / f"{recipe_name}.yaml")
    reenroll = recipe.steps["reenroll_stalled_pr"]
    assert reenroll.on_success != "wait_for_queue", (
        "reenroll_stalled_pr must route through a loop guard, not directly to wait_for_queue"
    )
    assert reenroll.on_success == "check_stall_loop", (
        f"reenroll_stalled_pr must route to check_stall_loop, got: {reenroll.on_success!r}"
    )
