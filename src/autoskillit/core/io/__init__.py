"""IL-0 filesystem, path, JSON, YAML, terminal-table, version-snapshot, and delivery-bounds primitives (issue #4671 Phase A).

Re-exports the public surface of the io/ sub-package through the
``autoskillit.core.io`` namespace. Backward-compat shims at
``core/io.py``, ``core/paths.py``, ``core/path_containment.py``,
``core/_json.py``, ``core/_terminal_table.py``,
``core/_version_snapshot.py``, and ``core/_delivery_bounds.py`` preserve
old import paths (issue #4671 Phase A decomposition).

Implementation split: ``io`` (core file/versioned JSON primitives) and
``yaml_io`` (YAML loader + helpers) co-locate under this sub-package. The
``io.py`` module is split to stay under the 750-line diff-scoped cap
(REQ-CNST-010); the YAML loader and helpers live in ``yaml_io.py``.
"""

from __future__ import annotations

from autoskillit.core.io.delivery_bounds import (
    recipe_delivery_request_digest,
    resolve_general_output_token_limit,
    resolve_recipe_delivery_decision,
    resolve_recipe_envelope_byte_limit,
    resolve_recipe_section_response_bound,
)
from autoskillit.core.io.io import (
    _AUTOSKILLIT_GITIGNORE_ENTRIES,
    _COMMITTED_BY_DESIGN,
    ReadResult,
    TreeEntry,
    TreeVanishedError,
    _AtomicWriteDurabilityError,
    _reset_schema_drift_logged_for_tests,
    atomic_write,
    decode_versioned_json_bytes,
    directory_tree_digest,
    ensure_project_temp,
    is_python_bytecode_path,
    read_versioned_json,
    resolve_skill_temp_dir,
    resolve_temp_dir,
    safe_upsert_section,
    spill_output,
    strict_walk,
    temp_dir_display_str,
    write_canonical_versioned_json,
    write_versioned_json,
)
from autoskillit.core.io.json import (
    fast_dumps,
    fast_loads,
)
from autoskillit.core.io.path_containment import (
    ContainmentError,
    check_metadata_stable,
    read_stable_contained_bytes,
    read_stable_contained_range,
    resolve_contained_path,
)
from autoskillit.core.io.paths import (
    GENERATED_FILES,
    claude_code_log_path,
    claude_code_project_dir,
    default_log_dir,
    destination_location,
    find_latest_session_id,
    github_review_ledger_path,
    is_generated_path,
    is_git_main_checkout,
    is_git_worktree,
    is_in_git_repo,
    pkg_root,
    resolve_main_worktree,
    resolve_project_dir,
)
from autoskillit.core.io.terminal_table import (
    TerminalColumn,
    _render_gfm_table,
    _render_terminal_table,
)
from autoskillit.core.io.version_snapshot import (
    collect_version_snapshot,
)
from autoskillit.core.io.yaml_io import (
    YAMLError,
    compose_yaml,
    dump_yaml_str,
    is_yaml_mapping_node,
    load_yaml,
    mapping_entry_byte_ranges_from_yaml,
)

__all__ = [
    "ContainmentError",
    "GENERATED_FILES",
    "ReadResult",
    "TerminalColumn",
    "TreeEntry",
    "TreeVanishedError",
    "YAMLError",
    "_AUTOSKILLIT_GITIGNORE_ENTRIES",
    "_COMMITTED_BY_DESIGN",
    "_render_gfm_table",
    "_render_terminal_table",
    "atomic_write",
    "check_metadata_stable",
    "claude_code_log_path",
    "claude_code_project_dir",
    "collect_version_snapshot",
    "compose_yaml",
    "decode_versioned_json_bytes",
    "default_log_dir",
    "destination_location",
    "directory_tree_digest",
    "dump_yaml_str",
    "ensure_project_temp",
    "fast_dumps",
    "fast_loads",
    "find_latest_session_id",
    "github_review_ledger_path",
    "is_generated_path",
    "is_git_main_checkout",
    "is_git_worktree",
    "is_in_git_repo",
    "is_python_bytecode_path",
    "is_yaml_mapping_node",
    "load_yaml",
    "mapping_entry_byte_ranges_from_yaml",
    "pkg_root",
    "read_stable_contained_bytes",
    "read_stable_contained_range",
    "read_versioned_json",
    "recipe_delivery_request_digest",
    "resolve_contained_path",
    "resolve_general_output_token_limit",
    "resolve_main_worktree",
    "resolve_project_dir",
    "resolve_recipe_delivery_decision",
    "resolve_recipe_envelope_byte_limit",
    "resolve_recipe_section_response_bound",
    "resolve_skill_temp_dir",
    "resolve_temp_dir",
    "safe_upsert_section",
    "spill_output",
    "strict_walk",
    "temp_dir_display_str",
    "write_canonical_versioned_json",
    "write_versioned_json",
]
