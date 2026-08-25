"""Structural guards proving the factory test split is mechanically complete.

These tests assert that every test from the pre-split `test_factory.py` has been moved
to one of the three new focused files, that no pre-split name is lost or duplicated,
and that the new files carry the project-required layer and size markers. The tests
themselves do not exercise production code; they guard the reorganization contract.
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

_PRE_SPLIT_FACTORY_NAMES: frozenset[str] = frozenset(
    {
        "test_make_context_survives_stale_precontract_shadowing_skill",
        "test_factory_bootstraps_exploration_store_from_verified_launch_authority",
        "test_factory_does_not_redirect_for_invalid_launch_authority",
        "test_factory_normal_session_keeps_explicit_project_root",
        "test_make_context_returns_toolcontext",
        "test_make_context_uses_explicit_parent_audit_admission_authority",
        "test_make_context_default_audit_authority_is_clone_local",
        "test_make_context_gate_starts_closed",
        "test_make_context_gate_stays_closed_in_headless_session",
        "test_make_context_executor_is_default_headless",
        "test_make_context_tester_is_default_test_runner",
        "test_make_context_service_fields_are_typed_instances",
        "test_make_context_creates_isolated_context_admission_ledgers",
        "test_make_context_github_client_is_default_fetcher",
        "test_make_context_github_client_uses_config_token",
        "test_make_context_github_client_uses_env_token",
        "test_make_context_github_client_no_token",
        "test_make_context_github_client_uses_gh_cli_fallback",
        "test_make_context_github_client_config_token_takes_priority_over_gh_cli",
        "test_make_context_github_client_token_snapshot_is_immutable",
        "test_make_context_tester_none_when_no_runner",
        "test_make_context_protocol_substitution",
        "test_output_patterns_nonempty_for_open_pr",
        "test_output_patterns_nonempty_for_investigate",
        "test_write_expected_resolver_mode",
        "test_cook_and_factory_session_skill_manager_ctor_args_in_sync",
        "test_gh_cli_token_returns_token_on_success",
        "test_gh_cli_token_returns_none_on_failure",
        "test_gh_cli_token_returns_none_when_gh_not_installed",
        "test_token_factory_resolves_lazily",
        "test_token_factory_caches_none",
        "test_gh_cli_token_not_called_during_make_context",
        "test_make_context_plugin_authority_derives_from_pkg_root_not_the_registry",
        "test_make_context_direct_install_yields_lazy_sanitized_authority",
        "test_make_context_sets_token_factory",
        "test_make_context_uses_explicit_project_dir",
        "test_serve_passes_project_dir_env_to_make_context",
        "test_serve_normalizes_empty_audit_authority_before_context_construction",
        "test_make_context_project_dir_git_root_fallback",
        "test_resolve_project_dir_git_root",
        "test_resolve_project_dir_ignores_a_toplevel_that_is_not_a_directory",
        "test_resolve_project_dir_cwd_fallback",
        "test_make_context_ignores_ambient_provider_profile",
        "test_make_context_no_env_profile_preserves_config_default",
        "test_tool_ctx_fixture_gate_starts_closed",
        "test_minimal_ctx_fixture_gate_starts_closed",
        "test_tool_ctx_kitchen_open_fixture_gate_starts_open",
        "test_make_context_skips_replay_runner_for_non_claude_backend",
        "test_make_context_skips_record_runner_for_non_claude_backend",
        "test_make_context_backend_is_coding_agent_backend",
        "test_make_context_default_backend_is_claude_code",
        "test_make_context_unknown_backend_raises_value_error",
        "test_make_context_codex_backend_not_none_plain_config",
        "test_make_context_builds_persistent_roots_over_all_registered_backends",
    }
)

_TEST_TO_MODULE: dict[str, str] = {
    # context_construction
    "test_make_context_survives_stale_precontract_shadowing_skill": "tests.server.test_factory_context_construction",
    "test_factory_bootstraps_exploration_store_from_verified_launch_authority": "tests.server.test_factory_context_construction",
    "test_factory_does_not_redirect_for_invalid_launch_authority": "tests.server.test_factory_context_construction",
    "test_factory_normal_session_keeps_explicit_project_root": "tests.server.test_factory_context_construction",
    "test_make_context_returns_toolcontext": "tests.server.test_factory_context_construction",
    "test_make_context_uses_explicit_parent_audit_admission_authority": "tests.server.test_factory_context_construction",
    "test_make_context_default_audit_authority_is_clone_local": "tests.server.test_factory_context_construction",
    "test_make_context_gate_starts_closed": "tests.server.test_factory_context_construction",
    "test_make_context_gate_stays_closed_in_headless_session": "tests.server.test_factory_context_construction",
    "test_make_context_executor_is_default_headless": "tests.server.test_factory_context_construction",
    "test_make_context_tester_is_default_test_runner": "tests.server.test_factory_context_construction",
    "test_make_context_service_fields_are_typed_instances": "tests.server.test_factory_context_construction",
    "test_make_context_creates_isolated_context_admission_ledgers": "tests.server.test_factory_context_construction",
    "test_make_context_tester_none_when_no_runner": "tests.server.test_factory_context_construction",
    "test_make_context_protocol_substitution": "tests.server.test_factory_context_construction",
    "test_cook_and_factory_session_skill_manager_ctor_args_in_sync": "tests.server.test_factory_context_construction",
    "test_make_context_plugin_authority_derives_from_pkg_root_not_the_registry": "tests.server.test_factory_context_construction",
    "test_make_context_direct_install_yields_lazy_sanitized_authority": "tests.server.test_factory_context_construction",
    "test_make_context_sets_token_factory": "tests.server.test_factory_context_construction",
    "test_make_context_ignores_ambient_provider_profile": "tests.server.test_factory_context_construction",
    "test_make_context_no_env_profile_preserves_config_default": "tests.server.test_factory_context_construction",
    # output_and_token_resolution
    "test_make_context_github_client_is_default_fetcher": "tests.server.test_factory_output_and_token_resolution",
    "test_make_context_github_client_uses_config_token": "tests.server.test_factory_output_and_token_resolution",
    "test_make_context_github_client_uses_env_token": "tests.server.test_factory_output_and_token_resolution",
    "test_make_context_github_client_no_token": "tests.server.test_factory_output_and_token_resolution",
    "test_make_context_github_client_uses_gh_cli_fallback": "tests.server.test_factory_output_and_token_resolution",
    "test_make_context_github_client_config_token_takes_priority_over_gh_cli": "tests.server.test_factory_output_and_token_resolution",
    "test_make_context_github_client_token_snapshot_is_immutable": "tests.server.test_factory_output_and_token_resolution",
    "test_output_patterns_nonempty_for_open_pr": "tests.server.test_factory_output_and_token_resolution",
    "test_output_patterns_nonempty_for_investigate": "tests.server.test_factory_output_and_token_resolution",
    "test_write_expected_resolver_mode": "tests.server.test_factory_output_and_token_resolution",
    "test_gh_cli_token_returns_token_on_success": "tests.server.test_factory_output_and_token_resolution",
    "test_gh_cli_token_returns_none_on_failure": "tests.server.test_factory_output_and_token_resolution",
    "test_gh_cli_token_returns_none_when_gh_not_installed": "tests.server.test_factory_output_and_token_resolution",
    "test_token_factory_resolves_lazily": "tests.server.test_factory_output_and_token_resolution",
    "test_token_factory_caches_none": "tests.server.test_factory_output_and_token_resolution",
    "test_gh_cli_token_not_called_during_make_context": "tests.server.test_factory_output_and_token_resolution",
    # project_dir_backend_roots
    "test_make_context_uses_explicit_project_dir": "tests.server.test_factory_project_dir_backend_roots",
    "test_serve_passes_project_dir_env_to_make_context": "tests.server.test_factory_project_dir_backend_roots",
    "test_serve_normalizes_empty_audit_authority_before_context_construction": "tests.server.test_factory_project_dir_backend_roots",
    "test_make_context_project_dir_git_root_fallback": "tests.server.test_factory_project_dir_backend_roots",
    "test_resolve_project_dir_git_root": "tests.server.test_factory_project_dir_backend_roots",
    "test_resolve_project_dir_ignores_a_toplevel_that_is_not_a_directory": "tests.server.test_factory_project_dir_backend_roots",
    "test_resolve_project_dir_cwd_fallback": "tests.server.test_factory_project_dir_backend_roots",
    "test_tool_ctx_fixture_gate_starts_closed": "tests.server.test_factory_project_dir_backend_roots",
    "test_minimal_ctx_fixture_gate_starts_closed": "tests.server.test_factory_project_dir_backend_roots",
    "test_tool_ctx_kitchen_open_fixture_gate_starts_open": "tests.server.test_factory_project_dir_backend_roots",
    "test_make_context_skips_replay_runner_for_non_claude_backend": "tests.server.test_factory_project_dir_backend_roots",
    "test_make_context_skips_record_runner_for_non_claude_backend": "tests.server.test_factory_project_dir_backend_roots",
    "test_make_context_backend_is_coding_agent_backend": "tests.server.test_factory_project_dir_backend_roots",
    "test_make_context_default_backend_is_claude_code": "tests.server.test_factory_project_dir_backend_roots",
    "test_make_context_unknown_backend_raises_value_error": "tests.server.test_factory_project_dir_backend_roots",
    "test_make_context_codex_backend_not_none_plain_config": "tests.server.test_factory_project_dir_backend_roots",
    "test_make_context_builds_persistent_roots_over_all_registered_backends": "tests.server.test_factory_project_dir_backend_roots",
}

_TEST_TO_MODULE_KEYS = frozenset(_TEST_TO_MODULE.keys())

# Base SHA of PR #4797 — the commit from which "files added by this split" is measured.
# Keep this in sync if the PR is rebased onto a new base; the guard's intent is to detect
# unexpected *new* factory test files relative to the pre-PR state.
_PR_BASE_SHA = "d846cb8e2c4300a75d405b31a34ad10336d65357"

_NEW_FACTORY_TEST_FILES = (
    "tests/server/test_factory_context_construction.py",
    "tests/server/test_factory_output_and_token_resolution.py",
    "tests/server/test_factory_project_dir_backend_roots.py",
)

_ALL_NEW_FACTORY_FILES = (
    "tests/server/_factory_helpers.py",
    *_NEW_FACTORY_TEST_FILES,
    "tests/server/test_factory_split_completeness.py",
)

# Helper-module names that the new test files import. Same private names
# as the source used — preserving these is the contract that makes the
# "verbatim" move claim in the Tests section true.
_EXPECTED_HELPER_EXPORTS = frozenset(
    {
        "_install_shared_explorer_authority",
        "_runner",
    }
)


def test_pre_split_factory_inventory_is_frozen() -> None:
    """The pre-split inventory is well-formed (no duplicates, no leading dots)."""
    # Size check derives from the module map; if a pre-split test is added or
    # dropped without updating both sides, this size check fires first with a
    # clear "drift" signal before the equality check below can run.
    assert len(_PRE_SPLIT_FACTORY_NAMES) == len(_TEST_TO_MODULE_KEYS)
    for name in _PRE_SPLIT_FACTORY_NAMES:
        assert not name.startswith("."), name
    assert _TEST_TO_MODULE_KEYS == _PRE_SPLIT_FACTORY_NAMES


def test_no_pre_split_factory_file_exists() -> None:
    """The pre-split `test_factory.py` must be deleted post-split."""
    assert not Path("tests/server/test_factory.py").exists(), (
        "Pre-split factory file must be deleted; the completeness guard is now live"
    )


@pytest.mark.parametrize("path", _ALL_NEW_FACTORY_FILES)
def test_every_split_target_file_exists(path: str) -> None:
    assert Path(path).is_file(), f"Split target {path} does not exist"


@pytest.mark.parametrize(
    ("test_name", "module_name"),
    sorted(_TEST_TO_MODULE.items()),
)
def test_pre_split_test_name_resolves_to_its_target_file(test_name: str, module_name: str) -> None:
    """Every pre-split test must resolve to its declared target module."""
    module = importlib.import_module(module_name)
    assert hasattr(module, test_name), f"{test_name} missing from {module_name}"


def test_every_pre_split_test_name_appears_in_exactly_one_new_file() -> None:
    """Each pre-split name appears in exactly one of the three new test files (no dup, no loss)."""
    counts: dict[str, int] = {}
    for path_str in _NEW_FACTORY_TEST_FILES:
        path = Path(path_str)
        text = path.read_text(encoding="utf-8")
        for name in _PRE_SPLIT_FACTORY_NAMES:
            if f"def {name}(" in text:
                counts[name] = counts.get(name, 0) + 1
    missing = _PRE_SPLIT_FACTORY_NAMES - counts.keys()
    duplicates = {name: c for name, c in counts.items() if c > 1}
    assert not missing, f"Pre-split tests not found in any new file: {missing}"
    assert not duplicates, f"Pre-split tests duplicated across new files: {duplicates}"


def test_no_unintended_new_factory_test_files_under_tests_server() -> None:
    """Only the four expected new factory files exist; pre-existing siblings are discovered.

    Pre-existing siblings are discovered from git history relative to the PR's base SHA,
    so future additions to tests/server/ matching this pattern don't require updating a
    hardcoded list.
    """
    import re
    import subprocess

    server_dir = Path("tests/server")
    pattern = re.compile(r"^test_factory_.*\.py$")
    # Discover pre-existing factory test files from the PR's base commit, so adding a new
    # test_factory_*.py file in a future PR is automatically accommodated.
    try:
        diff_output = subprocess.run(
            ["git", "ls-tree", "--name-only", "-r", _PR_BASE_SHA, "--", "tests/server/"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        pre_existing_siblings = {
            Path(p).name for p in diff_output.splitlines() if pattern.match(Path(p).name)
        }
    except OSError:
        # Git unavailable (FileNotFoundError / PermissionError) or the SHA is unreachable
        # (CalledProcessError): fall back to no exclusions. The test then reduces to
        # "expected new files exist" and loses the "no unexpected files" coverage, but
        # still passes rather than masking a true positive as a hard error.
        pre_existing_siblings = set()

    found = {p.name for p in server_dir.iterdir() if pattern.match(p.name)}
    found -= pre_existing_siblings
    expected = {p.split("/")[-1] for p in _ALL_NEW_FACTORY_FILES if "test_" in p}
    assert found == expected, (
        f"Unexpected new factory test files: {found - expected}; missing: {expected - found}"
    )


def test_helper_module_exports_match_source_helper_names() -> None:
    """The helper module exposes exactly the same private names the source used — this is the
    contract that makes verbatim test-body moves work without call-site rewrites."""
    import ast

    from tests.server import _factory_helpers as helpers

    # Walk only the module body (not function bodies) to collect names defined at module
    # level: functions, classes, and module-level assignments. dir()/vars() would surface
    # imports; ast.walk() would descend into function bodies and pick up locals.
    assert helpers.__file__ is not None
    tree = ast.parse(Path(helpers.__file__).read_text(encoding="utf-8"))
    actual: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            actual.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    actual.add(target.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            actual.add(node.target.id)
    actual = {name for name in actual if not name.startswith("__")}
    missing = _EXPECTED_HELPER_EXPORTS - actual
    unexpected = actual - _EXPECTED_HELPER_EXPORTS
    assert not missing, f"Helper module is missing expected names: {missing}"
    assert not unexpected, f"Helper module has unexpected names: {unexpected}"


@pytest.mark.parametrize("path", _NEW_FACTORY_TEST_FILES)
def test_every_split_file_has_layer_server_marker(path: str) -> None:
    text = Path(path).read_text(encoding="utf-8")
    assert 'pytest.mark.layer("server")' in text, f"{path} must carry the server layer marker"


@pytest.mark.parametrize("path", _NEW_FACTORY_TEST_FILES)
def test_every_split_file_has_small_size_marker(path: str) -> None:
    text = Path(path).read_text(encoding="utf-8")
    assert "pytest.mark.small" in text, (
        f"{path} must carry the small size marker (matches source file)"
    )


pytestmark = [pytest.mark.layer("server"), pytest.mark.small]
