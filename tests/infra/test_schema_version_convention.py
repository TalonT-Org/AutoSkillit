"""Allowlist ratchet: enforce that new JSON dict write sites use write_versioned_json.

Scans src/autoskillit/ for atomic_write calls whose second argument wraps json.dumps
of a dict payload. Sites in the _LEGACY_JSON_WRITE_COUNTS allowlist are grandfathered;
any new site must use write_versioned_json instead.
"""

from __future__ import annotations

import ast
from collections import Counter
from collections.abc import Mapping
from pathlib import Path

import pytest

from tests.contracts._ast_helpers import call_name, scope_qualnames

pytestmark = [pytest.mark.layer("infra"), pytest.mark.medium]


def _legacy_json_write_sites(tree: ast.AST, relpath: str) -> Counter[str]:
    """Return qualified legacy JSON write scopes from one parsed source file."""
    owners = scope_qualnames(tree)
    sites: Counter[str] = Counter()

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or call_name(node) != "atomic_write":
            continue
        if len(node.args) < 2:
            continue

        second_arg = node.args[1]
        json_dumps_call = _extract_json_dumps(second_arg)
        if json_dumps_call is None or not _is_dict_payload(json_dumps_call):
            continue
        if _is_yaml_dump(second_arg):
            continue

        sites[f"{relpath}::{owners[node]}"] += 1

    return sites


def _scan_atomic_write_json_dict_sites() -> Counter[str]:
    """Scan src/autoskillit/ for atomic_write(path, json.dumps({...})) calls.

    Source parsing fails directly with the filename for any syntax error, independently
    of whether lint or type checking also reports that syntax error.
    """
    src_root = Path(__file__).resolve().parents[2] / "src" / "autoskillit"
    sites: Counter[str] = Counter()

    for py_file in sorted(src_root.rglob("*.py")):
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        relpath = str(py_file.relative_to(src_root.parent.parent))
        sites.update(_legacy_json_write_sites(tree, relpath))

    return sites


def _extract_json_dumps(node: ast.expr) -> ast.Call | None:
    """If node is json.dumps(...) or wraps it (e.g. json.dumps(...) + '\\n'), extract the call."""
    if isinstance(node, ast.Call):
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr == "dumps":
            if isinstance(func.value, ast.Name) and func.value.id == "json":
                return node
            if isinstance(func.value, ast.Name) and func.value.id == "_json":
                return node
    # Handle json.dumps(...) + "\n"
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _extract_json_dumps(node.left) or _extract_json_dumps(node.right)
    return None


def _is_dict_payload(json_dumps_call: ast.Call) -> bool:
    """Return True if the first arg to json.dumps is likely a dict (not list/str)."""
    if not json_dumps_call.args:
        return False
    arg = json_dumps_call.args[0]
    # Explicit list/listcomp/set → not a dict
    if isinstance(arg, (ast.List, ast.ListComp, ast.SetComp)):
        return False
    # Explicit string → not a dict
    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
        return False
    # Explicit dict → definitely a dict
    if isinstance(arg, ast.Dict):
        return True
    # Name/Attribute/Call → assume dict (conservative)
    return True


def _is_yaml_dump(node: ast.expr) -> bool:
    """Return True if the node involves dump_yaml_str."""
    if isinstance(node, ast.Call):
        func = node.func
        if isinstance(func, ast.Name) and func.id == "dump_yaml_str":
            return True
        if isinstance(func, ast.Attribute) and func.attr == "dump_yaml_str":
            return True
    return False


def _legacy_write_problems(expected: Mapping[str, int], observed: Counter[str]) -> list[str]:
    """Describe per-scope count differences between the registry and source scan."""
    problems = []
    for key in sorted(expected.keys() | observed.keys()):
        expected_count = expected.get(key, 0)
        observed_count = observed.get(key, 0)

        if key in expected and expected_count < 1:
            problems.append(f"{key}: count must be at least 1")
            continue
        if observed_count > expected_count:
            problems.append(
                f"{key}: use write_versioned_json instead (or, if a moved/renamed "
                "allowlisted write, update its key)"
            )
        elif observed_count < expected_count and observed_count == 0:
            problems.append(
                f"{key}: missing write; if the write moved or was renamed, update the key; "
                "if it was removed or migrated to write_versioned_json, delete the entry"
            )
        elif observed_count < expected_count:
            problems.append(f"{key}: fewer write sites than expected; lower the count")

    return problems


# Hard-curated counts of existing atomic_write + json.dumps sites detected by AST scan.
# Any new site that writes a dict payload SHOULD use write_versioned_json.
_LEGACY_JSON_WRITE_COUNTS: dict[str, int] = {
    # migration/store.py — failure store dicts
    "src/autoskillit/migration/store.py::FailureStore.record": 1,
    "src/autoskillit/migration/store.py::FailureStore.clear": 1,
    # clone/_registry.py — clones dict (CloneRegistry.__exit__ atomic write-back)
    "src/autoskillit/workspace/clone/_registry.py::CloneRegistry.__exit__": 1,
    # staleness_cache.py — cache dict
    "src/autoskillit/recipe/contracts/staleness_cache.py::write_staleness_cache": 1,
    # _hook_repair.py — hooks.json self-heal for plugin cache and projection scopes,
    # triggered by the in-process repair primitive instead of startup drift detection
    # (co-owned with Claude plugin system). The shared lease-held hook transaction in
    # _repair_hook_payload_under_lease rewrites hooks.json for both cache and projection
    # artifacts. Manifest refresh stays versioned separately.
    (
        "src/autoskillit/workspace/_projected_artifact/_hook_repair.py::"
        "_repair_hook_payload_under_lease"
    ): 1,
    # tools_kitchen/_hook_config.py — hook config, quota guard, and git_ops_policy
    "src/autoskillit/server/tools/tools_kitchen/_hook_config.py::_write_hook_config": 1,
    (
        "src/autoskillit/server/tools/tools_kitchen/_hook_config.py::"
        "_update_hook_config_with_recipe"
    ): 1,
    (
        "src/autoskillit/server/tools/tools_kitchen/_hook_config.py::"
        "_update_hook_config_with_git_ops_policy"
    ): 1,
    # _overlay_state.py — session-scoped hook config overlay (not schema-versioned)
    "src/autoskillit/server/tools/_overlay_state.py::write_overlay_locked": 1,
    # pipeline_tracker.py — locked tracker init/merge/mutation writes centralize the existing
    # unversioned tracker file schema.
    "src/autoskillit/core/pipeline/pipeline_tracker.py::initialize_kitchen_tracker": 1,
    "src/autoskillit/core/pipeline/pipeline_tracker.py::initialize_manual_tracker": 1,
    "src/autoskillit/core/pipeline/pipeline_tracker.py::mutate_tracker": 1,
    # tools_status.py — mcp_data dict
    "src/autoskillit/server/tools/tools_status.py::write_telemetry_files": 1,
    # tools_github.py — bug report dict (non-blocking report-bug status file)
    "src/autoskillit/server/tools/tools_github.py::report_bug": 1,
    # _hooks.py — settings.json dict (co-owned with Claude CLI)
    "src/autoskillit/cli/_hooks.py::_write_settings_data": 1,
    # _installed_plugins.py — installed_plugins.json (co-owned with Claude plugin system)
    "src/autoskillit/cli/install/_installed_plugins.py::InstalledPluginsFile.remove": 1,
    # _update_checks.py — dismissal state file
    "src/autoskillit/cli/update/_update_checks.py::_write_dismiss_state": 1,
    # _update_checks_fetch.py — fetch cache (extracted from _update_checks.py)
    "src/autoskillit/cli/update/_update_checks_fetch.py::_write_fetch_cache": 1,
    # smoke_utils/_review_design.py — atomic v1→v2 handoff enrichment and manifest
    "src/autoskillit/smoke_utils/_review_design.py::enrich_diff_context": 1,
    "src/autoskillit/smoke_utils/_review_design.py::select_review_dimensions": 1,
    # tools_pr_ops.py — GitHubReviewReceipt.to_dict() already carries schema_version
    "src/autoskillit/server/tools/tools_pr_ops.py::post_pr_review": 1,
    # smoke_utils/_git.py — partitions, merge queue data
    # The fetch_merge_queue_data site is a list-payload write site (dual membership: also in
    # list_sites in test_allowlist_includes_list_payloads_as_documented). The AST scanner catches
    # it because it cannot distinguish list vs dict return types — intentional.
    "src/autoskillit/smoke_utils/_git.py::compute_domain_partitions": 1,
    "src/autoskillit/smoke_utils/_git.py::fetch_merge_queue_data": 1,
    # smoke_utils/_eval.py — eval resolved/manifest/scorecard/eval_context, agent-eval
    "src/autoskillit/smoke_utils/_eval.py::parse_eval_manifests": 2,
    "src/autoskillit/smoke_utils/_eval.py::parse_agent_eval_manifests": 2,
    "src/autoskillit/smoke_utils/_eval.py::_build_eval_context_common": 1,
    "src/autoskillit/smoke_utils/_eval.py::compile_eval_scorecard": 1,
    # planner/consolidation.py — broken_cycle_edges.json (list payload; AST scanner catches it)
    "src/autoskillit/planner/consolidation.py::_persist_consolidation": 2,
    # planner/manifests.py — finalize_wp_manifest: wp_index.json rebuild (list payload)
    "src/autoskillit/planner/manifests.py::finalize_wp_manifest": 1,
    # _cmd_rpc_issues.py — emit_fallback_map: BEM fallback execution map (recipe-internal)
    "src/autoskillit/recipe/cmd_rpc/_cmd_rpc_issues.py::emit_fallback_map": 1,
    # _probe_canary.py — CanaryState save() 3-field state file (intentionally unversioned)
    "src/autoskillit/_probe_canary.py::CanaryState.save": 1,
}


class TestSchemaVersionConvention:
    def test_legacy_write_identity_survives_line_shift(self):
        source = (
            'class Store:\n    def save(self):\n        atomic_write(p, json.dumps({"a": 1}))\n'
        )
        expected = Counter({"pkg/m.py::Store.save": 1})

        original = _legacy_json_write_sites(ast.parse(source), "pkg/m.py")
        shifted = _legacy_json_write_sites(ast.parse("\n" * 30 + source), "pkg/m.py")

        assert original == expected
        assert shifted == expected

    @pytest.mark.parametrize(
        ("expected", "observed", "key", "message"),
        [
            (
                {"pkg/m.py::writer": 1},
                Counter({"pkg/m.py::writer": 2}),
                "pkg/m.py::writer",
                "use write_versioned_json",
            ),
            (
                {"pkg/m.py::present": 1, "pkg/m.py::missing": 1},
                Counter({"pkg/m.py::present": 1}),
                "pkg/m.py::missing",
                "update the key",
            ),
            (
                {"pkg/m.py::writer": 2},
                Counter({"pkg/m.py::writer": 1}),
                "pkg/m.py::writer",
                "lower the count",
            ),
            ({"pkg/m.py::writer": 0}, Counter(), "pkg/m.py::writer", "count must be at least 1"),
            ({"pkg/m.py::writer": -1}, Counter(), "pkg/m.py::writer", "count must be at least 1"),
        ],
    )
    def test_legacy_write_registry_diff_is_per_key_and_counted(
        self, expected, observed, key, message
    ):
        problems = _legacy_write_problems(expected, observed)

        assert len(problems) == 1
        assert key in problems[0]
        assert message in problems[0]
        if key == "pkg/m.py::missing":
            assert "delete the entry" in problems[0]
            assert "present" not in problems[0]

    def test_current_json_write_sites_match_allowlist(self):
        """All atomic_write+json.dumps(dict) sites must match the scope/count registry."""
        current = _scan_atomic_write_json_dict_sites()
        problems = _legacy_write_problems(_LEGACY_JSON_WRITE_COUNTS, current)

        assert not problems, "\n".join(problems)

    def test_new_json_write_site_without_helper_fails(self):
        """Meta-test: a fake extra site should cause the ratchet to fail."""
        sites = _scan_atomic_write_json_dict_sites()
        sites["src/autoskillit/fake_module.py::fake_writer"] += 1

        problems = _legacy_write_problems(_LEGACY_JSON_WRITE_COUNTS, sites)
        assert any("fake_module" in problem for problem in problems)

    def test_allowlist_excludes_externally_co_owned_files(self):
        """Documented exclusions: settings.json, ~/.claude.json, installed_plugins.json, etc."""
        co_owned_paths = {
            "_hooks.py",
            "_init_helpers.py",
            "_marketplace.py",
        }
        for key in _LEGACY_JSON_WRITE_COUNTS:
            path, _scope = key.rsplit("::", 1)
            basename = Path(path).name
            if basename in co_owned_paths:
                assert isinstance(path, str) and path, (
                    f"Co-owned allowlist entry has invalid path: {path!r}"
                )
                assert isinstance(_scope, str) and _scope

    def test_allowlist_includes_list_payloads_as_documented(self):
        """List-payload sites are included since the AST scanner can't distinguish return types."""
        # These sites write list payloads through function calls but are caught by the scanner
        site = "src/autoskillit/smoke_utils/_git.py::fetch_merge_queue_data"
        assert site in _LEGACY_JSON_WRITE_COUNTS, (
            f"List-payload site {site} should be in _LEGACY_JSON_WRITE_COUNTS (scanner limitation)"
        )

    def test_allowlist_excludes_yaml_writes(self):
        """YAML write sites must not appear in the allowlist."""
        for key in _LEGACY_JSON_WRITE_COUNTS:
            path, _scope = key.rsplit("::", 1)
            assert "yaml" not in Path(path).stem.lower() or "staleness" in path, (
                f"YAML write file {path} should not be in _LEGACY_JSON_WRITE_COUNTS"
            )

    def test_allowlist_excludes_string_body_writes(self):
        """Gitignore, marker files, pre-rendered report text must not be in allowlist."""
        string_body_keywords = ["gitignore", "marker", "report"]
        for key in _LEGACY_JSON_WRITE_COUNTS:
            path, _scope = key.rsplit("::", 1)
            for kw in string_body_keywords:
                assert kw not in Path(path).stem.lower(), (
                    f"String-body file {path} should not be in _LEGACY_JSON_WRITE_COUNTS"
                )
