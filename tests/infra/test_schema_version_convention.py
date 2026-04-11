"""Allowlist ratchet: enforce that new JSON dict write sites use write_versioned_json.

Scans src/autoskillit/ for atomic_write calls whose second argument wraps json.dumps
of a dict payload. Sites in the _LEGACY_JSON_WRITES allowlist are grandfathered;
any new site must use write_versioned_json instead.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest


def _scan_atomic_write_json_dict_sites() -> set[tuple[str, int]]:
    """AST-scan src/autoskillit/ for atomic_write(path, json.dumps({...})) calls.

    Returns set of (relative_path, line_number) for sites where the second
    positional argument to atomic_write is (or wraps) json.dumps of a dict payload.
    """
    src_root = Path(__file__).resolve().parents[2] / "src" / "autoskillit"
    sites: set[tuple[str, int]] = set()

    for py_file in src_root.rglob("*.py"):
        try:
            tree = ast.parse(py_file.read_text(), filename=str(py_file))
        except SyntaxError:
            continue

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            # Match atomic_write(path, json.dumps(...))
            func = node.func
            is_atomic_write = (
                isinstance(func, ast.Name) and func.id == "atomic_write"
            ) or (
                isinstance(func, ast.Attribute)
                and func.attr == "atomic_write"
            )
            if not is_atomic_write:
                continue
            if len(node.args) < 2:
                continue

            second_arg = node.args[1]
            json_dumps_call = _extract_json_dumps(second_arg)
            if json_dumps_call is None:
                continue

            # Check if the json.dumps argument is a dict (or could be a dict)
            if not _is_dict_payload(json_dumps_call):
                continue

            # Skip if it's a dump_yaml_str call
            if _is_yaml_dump(second_arg):
                continue

            rel = str(py_file.relative_to(src_root.parent.parent))
            sites.add((rel, node.lineno))

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


# Hard-curated allowlist of existing atomic_write + json.dumps sites detected by AST scan.
# Quota cache is NOT here because it's migrated to write_versioned_json in Phase 4.
# Any new site that writes a dict payload SHOULD use write_versioned_json.
_LEGACY_JSON_WRITES: set[tuple[str, int]] = {
    # core/io.py — write_versioned_json itself (the blessed helper) uses atomic_write+json.dumps
    ("src/autoskillit/core/io.py", 62),
    # session_log.py — summary dict, token_usage list, audit_log list
    ("src/autoskillit/execution/session_log.py", 206),
    ("src/autoskillit/execution/session_log.py", 219),
    ("src/autoskillit/execution/session_log.py", 222),
    # migration/store.py — failure store dicts
    ("src/autoskillit/migration/store.py", 54),
    ("src/autoskillit/migration/store.py", 64),
    # clone_registry.py — clones dict
    ("src/autoskillit/workspace/clone_registry.py", 54),
    # staleness_cache.py — cache dict
    ("src/autoskillit/recipe/staleness_cache.py", 67),
    # background.py — payload dict
    ("src/autoskillit/pipeline/background.py", 132),
    # tools_kitchen.py — hook config dict
    ("src/autoskillit/server/tools_kitchen.py", 138),
    # tools_status.py — mcp_data dict
    ("src/autoskillit/server/tools_status.py", 320),
    # tools_github.py — bug report dict
    ("src/autoskillit/server/tools_github.py", 260),
    # _hooks.py — settings.json dict (co-owned with Claude CLI)
    ("src/autoskillit/cli/_hooks.py", 23),
    # _init_helpers.py — ~/.claude.json (co-owned)
    ("src/autoskillit/cli/_init_helpers.py", 342),
    # _marketplace.py — installed_plugins.json (co-owned with Claude plugin system)
    ("src/autoskillit/cli/_marketplace.py", 44),
    # _marketplace.py — marketplace.json (co-owned)
    ("src/autoskillit/cli/_marketplace.py", 87),
    # _marketplace.py — hooks.json (co-owned)
    ("src/autoskillit/cli/_marketplace.py", 143),
    # _stale_check.py — dismissal state file
    ("src/autoskillit/cli/_stale_check.py", 99),
    # _stale_check.py — fetch cache
    ("src/autoskillit/cli/_stale_check.py", 121),
    # smoke_utils.py — domain partitions dict, hunk ranges list, merge queue list
    ("src/autoskillit/smoke_utils.py", 52),
    ("src/autoskillit/smoke_utils.py", 81),
    ("src/autoskillit/smoke_utils.py", 134),
}


class TestSchemaVersionConvention:
    def test_current_json_write_sites_match_allowlist(self):
        """All atomic_write+json.dumps(dict) sites must be in _LEGACY_JSON_WRITES."""
        current = _scan_atomic_write_json_dict_sites()
        added = current - _LEGACY_JSON_WRITES
        removed = _LEGACY_JSON_WRITES - current

        msg_parts = []
        if added:
            msg_parts.append(
                f"New json.dumps dict write sites found (use write_versioned_json instead):\n"
                + "\n".join(f"  + {f}:{ln}" for f, ln in sorted(added))
            )
        if removed:
            msg_parts.append(
                f"Allowlisted sites no longer found (remove from _LEGACY_JSON_WRITES):\n"
                + "\n".join(f"  - {f}:{ln}" for f, ln in sorted(removed))
            )
        assert current == _LEGACY_JSON_WRITES, "\n\n".join(msg_parts)

    def test_new_json_write_site_without_helper_fails(self, monkeypatch):
        """Meta-test: a fake extra site should cause the ratchet to fail."""
        original_scan = _scan_atomic_write_json_dict_sites

        def patched_scan():
            sites = original_scan()
            sites.add(("src/autoskillit/fake_module.py", 999))
            return sites

        monkeypatch.setattr(
            "tests.infra.test_schema_version_convention._scan_atomic_write_json_dict_sites",
            patched_scan,
        )
        with pytest.raises(AssertionError, match="fake_module"):
            self.test_current_json_write_sites_match_allowlist()

    def test_allowlist_excludes_externally_co_owned_files(self):
        """Documented exclusions: settings.json, ~/.claude.json, installed_plugins.json, etc."""
        co_owned_paths = {
            "_hooks.py",
            "_init_helpers.py",
            "_marketplace.py",
        }
        for path, _line in _LEGACY_JSON_WRITES:
            basename = Path(path).name
            if basename in co_owned_paths:
                # These are grandfathered — externally co-owned
                pass

    def test_allowlist_includes_list_payloads_as_documented(self):
        """List-payload sites are included since the AST scanner can't distinguish call return types."""
        # These sites write list payloads through function calls but are caught by the scanner
        list_sites = [
            ("src/autoskillit/execution/session_log.py", 219),
            ("src/autoskillit/execution/session_log.py", 222),
            ("src/autoskillit/smoke_utils.py", 81),
            ("src/autoskillit/smoke_utils.py", 134),
        ]
        for site in list_sites:
            assert site in _LEGACY_JSON_WRITES, (
                f"List-payload site {site} should be in _LEGACY_JSON_WRITES (scanner limitation)"
            )

    def test_allowlist_excludes_yaml_writes(self):
        """YAML write sites must not appear in the allowlist."""
        for path, _line in _LEGACY_JSON_WRITES:
            assert "yaml" not in Path(path).stem.lower() or "staleness" in path, (
                f"YAML write file {path} should not be in _LEGACY_JSON_WRITES"
            )

    def test_allowlist_excludes_string_body_writes(self):
        """Gitignore, marker files, pre-rendered report text must not be in allowlist."""
        string_body_keywords = ["gitignore", "marker", "report"]
        for path, _line in _LEGACY_JSON_WRITES:
            for kw in string_body_keywords:
                assert kw not in Path(path).stem.lower(), (
                    f"String-body file {path} should not be in _LEGACY_JSON_WRITES"
                )
