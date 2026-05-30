"""Tests for run_cmd echo-capture alignment rules."""

from __future__ import annotations

import pytest

from autoskillit.recipe.validator import run_semantic_rules
from tests.recipe.conftest import _make_workflow

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


def _make_recipe(steps: dict) -> object:
    """Build a minimal Recipe from a dict of step dicts, with a terminal END step."""
    all_steps = {**steps, "END": {"action": "stop", "message": "Done"}}
    return _make_workflow(all_steps)


def test_emit_alignment_errors_on_missing_echo():
    """run_cmd with capture key K but no echo "K=..." in cmd → ERROR."""
    recipe = _make_recipe(
        {
            "step_a": {
                "tool": "run_cmd",
                "with": {"cmd": "RESULT=$(compute_it)"},
                "capture": {"my_path": "${{ result.my_path }}"},
                "on_success": "END",
            }
        }
    )
    findings = run_semantic_rules(recipe)
    codes = [f.rule for f in findings]
    assert "run-cmd-emit-alignment" in codes
    finding = next(f for f in findings if f.rule == "run-cmd-emit-alignment")
    assert "step_a" in finding.step_name
    assert "my_path" in finding.message


def test_emit_alignment_passes_with_echo():
    """run_cmd with matching echo "K=..." → no alignment error."""
    recipe = _make_recipe(
        {
            "step_a": {
                "tool": "run_cmd",
                "with": {"cmd": 'RESULT=$(compute_it) && echo "my_path=${RESULT}"'},
                "capture": {"my_path": "${{ result.my_path }}"},
                "on_success": "END",
            }
        }
    )
    findings = run_semantic_rules(recipe)
    codes = [f.rule for f in findings]
    assert "run-cmd-emit-alignment" not in codes


def test_emit_alignment_ignores_stdout_capture():
    """capture: {K: "${{ result.stdout }}"} does not require echo."""
    recipe = _make_recipe(
        {
            "step_a": {
                "tool": "run_cmd",
                "with": {"cmd": "git rev-parse HEAD"},
                "capture": {"sha": "${{ result.stdout | trim }}"},
                "on_success": "END",
            }
        }
    )
    findings = run_semantic_rules(recipe)
    codes = [f.rule for f in findings]
    assert "run-cmd-emit-alignment" not in codes


def test_emit_alignment_ignores_exit_code_capture():
    """capture: {K: "${{ result.exit_code }}"} does not require echo."""
    recipe = _make_recipe(
        {
            "step_a": {
                "tool": "run_cmd",
                "with": {"cmd": "test -f something"},
                "capture": {"ok": "${{ result.exit_code }}"},
                "on_success": "END",
            }
        }
    )
    findings = run_semantic_rules(recipe)
    assert all(f.rule != "run-cmd-emit-alignment" for f in findings)


def test_emit_alignment_ignores_run_skill():
    """run_skill steps are not subject to the emit-alignment rule."""
    recipe = _make_recipe(
        {
            "step_a": {
                "tool": "run_skill",
                "with": {"skill_command": "/foo:bar"},
                "capture": {"plan_path": "${{ result.plan_path }}"},
                "on_success": "END",
            }
        }
    )
    findings = run_semantic_rules(recipe)
    assert all(f.rule != "run-cmd-emit-alignment" for f in findings)


def test_find_rediscovery_warns_on_find_sort_tail():
    """find|sort|tail -1 in run_cmd cmd → WARNING."""
    recipe = _make_recipe(
        {
            "step_a": {
                "tool": "run_cmd",
                "with": {
                    "cmd": (
                        "DIR=$(find /some/path -maxdepth 1 -type d"
                        " -name '????-??-??-*' | sort | tail -1)"
                    )
                },
                "on_success": "END",
            }
        }
    )
    findings = run_semantic_rules(recipe)
    codes = [f.rule for f in findings]
    assert "run-cmd-find-rediscovery" in codes


def test_find_rediscovery_no_warning_without_heuristic():
    """find without sort|tail does not trigger the warning."""
    recipe = _make_recipe(
        {
            "step_a": {
                "tool": "run_cmd",
                "with": {"cmd": "find /path -name '*.md' | xargs wc -l"},
                "on_success": "END",
            }
        }
    )
    findings = run_semantic_rules(recipe)
    assert all(f.rule != "run-cmd-find-rediscovery" for f in findings)


def test_hardcoded_origin_in_run_cmd_fires_on_fetch_origin():
    """run_cmd with 'git fetch origin main' triggers the warning."""
    recipe = _make_recipe(
        {
            "step_a": {
                "tool": "run_cmd",
                "with": {"cmd": "git fetch origin main && git rebase origin/main"},
                "on_success": "END",
            }
        }
    )
    findings = run_semantic_rules(recipe)
    codes = [f.rule for f in findings]
    assert "hardcoded-origin-in-run-cmd" in codes
    finding = next(f for f in findings if f.rule == "hardcoded-origin-in-run-cmd")
    assert "step_a" in finding.step_name
    assert "origin" in finding.message


def test_hardcoded_origin_in_run_cmd_clean_with_remote_var():
    """run_cmd using $REMOTE instead of literal 'origin' does not trigger."""
    recipe = _make_recipe(
        {
            "step_a": {
                "tool": "run_cmd",
                "with": {
                    "cmd": (
                        "REMOTE=$(git remote get-url upstream >/dev/null 2>&1 "
                        "&& echo upstream || echo origin)\n"
                        "git fetch $REMOTE main && git rebase $REMOTE/main"
                    )
                },
                "on_success": "END",
            }
        }
    )
    findings = run_semantic_rules(recipe)
    assert all(f.rule != "hardcoded-origin-in-run-cmd" for f in findings)


def test_hardcoded_origin_in_run_cmd_suppressed_by_set_url_step():
    """Step containing 'git remote set-url origin' is not flagged; other steps still are."""
    recipe = _make_recipe(
        {
            "step_setup": {
                "tool": "run_cmd",
                "with": {"cmd": "git remote set-url origin https://github.com/org/repo.git"},
                "on_success": "step_fetch",
            },
            "step_fetch": {
                "tool": "run_cmd",
                "with": {"cmd": "git fetch origin main"},
                "on_success": "END",
            },
        }
    )
    findings = run_semantic_rules(recipe)
    violations = [f for f in findings if f.rule == "hardcoded-origin-in-run-cmd"]
    step_names = [v.step_name for v in violations]
    assert "step_setup" not in step_names, "step with set-url origin must not be flagged"
    assert "step_fetch" in step_names, (
        "step without set-url using hardcoded origin must be flagged"
    )


def test_hardcoded_origin_in_run_cmd_fires_on_push_origin():
    """run_cmd with 'git push origin main' triggers the warning (push is in the verb set)."""
    recipe = _make_recipe(
        {
            "step_a": {
                "tool": "run_cmd",
                "with": {"cmd": "git push origin main"},
                "on_success": "END",
            }
        }
    )
    findings = run_semantic_rules(recipe)
    codes = [f.rule for f in findings]
    assert "hardcoded-origin-in-run-cmd" in codes


def test_hardcoded_origin_in_run_cmd_fires_on_merge_base_origin():
    """run_cmd with 'git merge-base origin/main HEAD' triggers the warning."""
    recipe = _make_recipe(
        {
            "step_a": {
                "tool": "run_cmd",
                "with": {"cmd": "git merge-base origin/main HEAD"},
                "on_success": "END",
            }
        }
    )
    findings = run_semantic_rules(recipe)
    codes = [f.rule for f in findings]
    assert "hardcoded-origin-in-run-cmd" in codes


# ─────────────────────────────────────────────────────────────────────────────
# run-cmd-script-exists
# ─────────────────────────────────────────────────────────────────────────────


def test_script_exists_errors_on_nonexistent_script():
    """bash /nonexistent/path/script.sh → ERROR from run-cmd-script-exists."""
    recipe = _make_recipe(
        {
            "step_a": {
                "tool": "run_cmd",
                "with": {"cmd": "bash /nonexistent/path/script.sh"},
                "on_success": "END",
            }
        }
    )
    findings = run_semantic_rules(recipe)
    codes = [f.rule for f in findings]
    assert "run-cmd-script-exists" in codes
    finding = next(f for f in findings if f.rule == "run-cmd-script-exists")
    assert "step_a" in finding.step_name
    assert "/nonexistent/path/script.sh" in finding.message


def test_script_exists_passes_on_real_script():
    """bash <real_absolute_path.sh> → no finding from run-cmd-script-exists."""
    from autoskillit.recipe.io import builtin_scripts_dir

    real_script = builtin_scripts_dir() / "create_worktree.sh"
    recipe = _make_recipe(
        {
            "step_a": {
                "tool": "run_cmd",
                "with": {"cmd": f"bash {real_script}"},
                "on_success": "END",
            }
        }
    )
    findings = run_semantic_rules(recipe)
    assert all(f.rule != "run-cmd-script-exists" for f in findings)


def test_script_exists_only_fires_for_bash_sh_pattern():
    """run-cmd-script-exists does NOT fire for non-bash commands."""
    recipe = _make_recipe(
        {
            "step_a": {
                "tool": "run_cmd",
                "with": {"cmd": "git status"},
                "on_success": "END",
            }
        }
    )
    findings = run_semantic_rules(recipe)
    assert all(f.rule != "run-cmd-script-exists" for f in findings)


def test_script_exists_ignores_bash_c_inline():
    """run-cmd-script-exists does NOT fire for bash -c "inline script" (no .sh path)."""
    recipe = _make_recipe(
        {
            "step_a": {
                "tool": "run_cmd",
                "with": {"cmd": 'bash -c "echo hello && ls /tmp"'},
                "on_success": "END",
            }
        }
    )
    findings = run_semantic_rules(recipe)
    assert all(f.rule != "run-cmd-script-exists" for f in findings)


def test_script_exists_ignores_relative_script_path():
    """run-cmd-script-exists does NOT fire for relative scripts/ paths (caught by unbundled)."""
    recipe = _make_recipe(
        {
            "step_a": {
                "tool": "run_cmd",
                "with": {"cmd": "bash scripts/recipe/foo.sh"},
                "on_success": "END",
            }
        }
    )
    findings = run_semantic_rules(recipe)
    assert all(f.rule != "run-cmd-script-exists" for f in findings)


# ─────────────────────────────────────────────────────────────────────────────
# run-cmd-unbundled-script-ref
# ─────────────────────────────────────────────────────────────────────────────


def test_unbundled_script_ref_errors_on_relative_path():
    """bash scripts/recipe/foo.sh → ERROR from run-cmd-unbundled-script-ref."""
    recipe = _make_recipe(
        {
            "step_a": {
                "tool": "run_cmd",
                "with": {"cmd": "bash scripts/recipe/foo.sh"},
                "on_success": "END",
            }
        }
    )
    findings = run_semantic_rules(recipe)
    codes = [f.rule for f in findings]
    assert "run-cmd-unbundled-script-ref" in codes
    finding = next(f for f in findings if f.rule == "run-cmd-unbundled-script-ref")
    assert "step_a" in finding.step_name


def test_unbundled_script_ref_ignores_bash_absolute_path():
    """bash /abs/path/foo.sh → no finding from run-cmd-unbundled-script-ref."""
    recipe = _make_recipe(
        {
            "step_a": {
                "tool": "run_cmd",
                "with": {"cmd": "bash /some/absolute/path/foo.sh"},
                "on_success": "END",
            }
        }
    )
    findings = run_semantic_rules(recipe)
    assert all(f.rule != "run-cmd-unbundled-script-ref" for f in findings)


# ─────────────────────────────────────────────────────────────────────────────
# bash / exemption in run-cmd-emit-alignment
# ─────────────────────────────────────────────────────────────────────────────


def test_emit_alignment_bash_absolute_path_exempt():
    """bash /script.sh with capture → emit-alignment does NOT fire (exempt)."""
    recipe = _make_recipe(
        {
            "step_a": {
                "tool": "run_cmd",
                "with": {"cmd": "bash /some/script.sh"},
                "capture": {"output": "${{ result.stdout }}"},
                "on_success": "END",
            }
        }
    )
    findings = run_semantic_rules(recipe)
    assert all(f.rule != "run-cmd-emit-alignment" for f in findings)


def test_emit_alignment_non_bash_capture_still_flagged():
    """Non-raw capture key without matching echo → emit-alignment fires for non-absolute."""
    recipe = _make_recipe(
        {
            "step_a": {
                "tool": "run_cmd",
                "with": {"cmd": "echo something"},
                "capture": {"my_key": "${{ result.my_output }}"},
                "on_success": "END",
            }
        }
    )
    findings = run_semantic_rules(recipe)
    codes = [f.rule for f in findings]
    assert "run-cmd-emit-alignment" in codes


# ─────────────────────────────────────────────────────────────────────────────
# run-cmd-bare-rebase-without-conflict-routing
# ─────────────────────────────────────────────────────────────────────────────


def test_bare_rebase_fires_on_terminal_on_failure():
    """run_cmd git rebase with on_failure → terminal step → ERROR."""
    recipe = _make_recipe(
        {
            "rebase_step": {
                "tool": "run_cmd",
                "with": {"cmd": "git fetch origin && git rebase origin/main"},
                "on_success": "END",
                "on_failure": "END",
            }
        }
    )
    findings = run_semantic_rules(recipe)
    codes = [f.rule for f in findings]
    assert "run-cmd-bare-rebase-without-conflict-routing" in codes
    finding = next(f for f in findings if f.rule == "run-cmd-bare-rebase-without-conflict-routing")
    assert "rebase_step" in finding.step_name


def test_bare_rebase_clean_when_run_python():
    """run_python step (not run_cmd) does NOT trigger the rule."""
    recipe = _make_recipe(
        {
            "rebase_step": {
                "tool": "run_python",
                "with": {
                    "callable": "autoskillit.recipe._cmd_rpc.review_path_rebase",
                    "work_dir": "/tmp",
                    "base_branch": "main",
                },
                "on_result": [
                    {"when": "${{ result.status }} == clean", "route": "END"},
                    {"route": "END"},
                ],
                "on_failure": "END",
            }
        }
    )
    findings = run_semantic_rules(recipe)
    assert all(f.rule != "run-cmd-bare-rebase-without-conflict-routing" for f in findings)


def test_bare_rebase_clean_when_on_failure_routes_to_conflict_resolution():
    """run_cmd git rebase with on_failure → resolve-merge-conflicts → no finding."""
    recipe = _make_recipe(
        {
            "rebase_step": {
                "tool": "run_cmd",
                "with": {"cmd": "git fetch origin && git rebase origin/main"},
                "on_success": "END",
                "on_failure": "resolve_conflicts",
            },
            "resolve_conflicts": {
                "tool": "run_skill",
                "with": {"skill_command": "/autoskillit:resolve-merge-conflicts /work main"},
                "on_success": "END",
                "on_failure": "END",
            },
        }
    )
    findings = run_semantic_rules(recipe)
    assert all(f.rule != "run-cmd-bare-rebase-without-conflict-routing" for f in findings)


def test_bare_rebase_clean_when_on_result_routes_to_conflict_resolution():
    """run_cmd git rebase with on_result routing to resolve-merge-conflicts → no finding."""
    recipe = _make_recipe(
        {
            "rebase_step": {
                "tool": "run_cmd",
                "with": {"cmd": "git fetch origin && git rebase origin/main"},
                "on_result": [
                    {"when": "${{ result.status }} == clean", "route": "END"},
                    {"route": "resolve_conflicts"},
                ],
                "on_failure": "resolve_conflicts",
            },
            "resolve_conflicts": {
                "tool": "run_skill",
                "with": {"skill_command": "/autoskillit:resolve-merge-conflicts /work main"},
                "on_success": "END",
                "on_failure": "END",
            },
        }
    )
    findings = run_semantic_rules(recipe)
    assert all(f.rule != "run-cmd-bare-rebase-without-conflict-routing" for f in findings)


def test_bare_rebase_does_not_match_rebase_abort():
    """run_cmd with git rebase --abort does NOT trigger the rule."""
    recipe = _make_recipe(
        {
            "cleanup_step": {
                "tool": "run_cmd",
                "with": {"cmd": "git rebase --abort"},
                "on_success": "END",
                "on_failure": "END",
            }
        }
    )
    findings = run_semantic_rules(recipe)
    assert all(f.rule != "run-cmd-bare-rebase-without-conflict-routing" for f in findings)


def test_bundled_recipes_have_no_bare_rebase_findings():
    """All bundled recipes must have zero run-cmd-bare-rebase-without-conflict-routing findings."""
    from autoskillit.recipe.io import builtin_recipes_dir, load_recipe

    recipes_dir = builtin_recipes_dir()
    for yaml_path in sorted(recipes_dir.glob("*.yaml")):
        recipe = load_recipe(yaml_path)
        findings = run_semantic_rules(recipe)
        bare_rebase = [
            f for f in findings if f.rule == "run-cmd-bare-rebase-without-conflict-routing"
        ]
        assert not bare_rebase, (
            f"{yaml_path.name}: found bare rebase findings: "
            f"{[(f.step_name, f.message) for f in bare_rebase]}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# run-cmd-path-capture-requires-nonempty-guard
# ─────────────────────────────────────────────────────────────────────────────


def test_run_cmd_path_capture_without_guard_flagged():
    """run_cmd with path-typed capture but no test -s or [ -s guard → WARNING."""
    recipe = _make_recipe(
        {
            "step_a": {
                "tool": "run_cmd",
                "with": {"cmd": 'echo "output_path=/tmp/out.md"'},
                "capture": {"output_path": {"from": "${{ result.output_path }}", "type": "path"}},
                "on_success": "END",
            }
        }
    )
    findings = run_semantic_rules(recipe)
    codes = [f.rule for f in findings]
    assert "run-cmd-path-capture-requires-nonempty-guard" in codes
    finding = next(f for f in findings if f.rule == "run-cmd-path-capture-requires-nonempty-guard")
    assert finding.severity.value == "warning"


def test_run_cmd_path_capture_with_guard_passes():
    """run_cmd with path-typed capture and test -s guard → no finding."""
    recipe = _make_recipe(
        {
            "step_a": {
                "tool": "run_cmd",
                "with": {"cmd": 'test -s "/tmp/out.md" && echo "output_path=/tmp/out.md"'},
                "capture": {"output_path": {"from": "${{ result.output_path }}", "type": "path"}},
                "on_success": "END",
            }
        }
    )
    findings = run_semantic_rules(recipe)
    assert all(f.rule != "run-cmd-path-capture-requires-nonempty-guard" for f in findings)


def test_run_cmd_string_capture_not_flagged():
    """run_cmd with string-typed capture (shorthand) → no finding from path guard rule."""
    recipe = _make_recipe(
        {
            "step_a": {
                "tool": "run_cmd",
                "with": {"cmd": 'echo "output_path=/tmp/out.md"'},
                "capture": {"output_path": "${{ result.output_path }}"},
                "on_success": "END",
            }
        }
    )
    findings = run_semantic_rules(recipe)
    assert all(f.rule != "run-cmd-path-capture-requires-nonempty-guard" for f in findings)


def test_run_cmd_path_capture_bracket_guard_passes():
    """run_cmd with path-typed capture and [ -s bracket guard → no finding."""
    recipe = _make_recipe(
        {
            "step_a": {
                "tool": "run_cmd",
                "with": {"cmd": '[ -s "/tmp/out.md" ] && echo "output_path=/tmp/out.md"'},
                "capture": {"output_path": {"from": "${{ result.output_path }}", "type": "path"}},
                "on_success": "END",
            }
        }
    )
    findings = run_semantic_rules(recipe)
    assert all(f.rule != "run-cmd-path-capture-requires-nonempty-guard" for f in findings)
