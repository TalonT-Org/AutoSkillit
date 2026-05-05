# core/

Core layer (IL-0) unit tests — paths, IO, types, feature flags.

## Files

| File | Purpose |
|------|---------|
| `__init__.py` | empty |
| `test_add_dir_validation.py` | Tests for ValidatedAddDir and validate_add_dir |
| `test_branch_guard.py` | Tests for core.branch_guard — protected-branch validation |
| `test_claude_env.py` | Unit tests for build_claude_env() — IDE env scrubbing at the subprocess launch boundary |
| `test_core.py` | Tests for the core/ sub-package foundation layer |
| `test_core_terminal_table.py` | Tests for core/_terminal_table.py — the L0 shared table primitive |
| `test_ensure_project_temp_with_config.py` | Tests for ensure_project_temp with configurable override |
| `test_feature_flags.py` | Tests for core/feature_flags.py — _collect_disabled_feature_tags helper |
| `test_github_url.py` | Unit tests for core.github_url.parse_github_repo |
| `test_import_isolation.py` | Fleet package must not be imported at server startup via lazy-import structure |
| `test_install_detect.py` | Tests for core/_install_detect.py — install-type detection |
| `test_io.py` | Extended YAML I/O tests for core/io.py consolidation |
| `test_kitchen_state.py` | Tests for KitchenMarker hash field support |
| `test_logging.py` | Tests for autoskillit.core.logging — centralized structlog configuration |
| `test_paths.py` | Tests for autoskillit.core.paths — is_git_worktree and pkg_root |
| `test_resolve_temp_dir.py` | Tests for autoskillit.core.io.resolve_temp_dir |
| `test_session_checkpoint.py` | Tests for SessionCheckpoint schema validation and compute_remaining |
| `test_session_liveness.py` | Tests for is_session_alive generalized liveness triple-check |
| `test_session_registry.py` | Tests for core/session_registry.py |
| `test_session_type.py` | Tests for SessionType resolver and constants |
| `test_skill_command_parsing.py` | Unit tests for extract_path_arg in core._type_helpers |
| `test_tool_sequence_analysis.py` | Tool sequence analysis tests |
| `test_type_constants.py` | Tests for PACK_REGISTRY and related constants in core._type_constants |
| `test_type_protocol_shards.py` | Type protocol shards guard |
| `test_types.py` | Tests for shared type contracts — enum exhaustiveness |
| `test_types_structure.py` | Tests for core/types.py split into focused sub-modules (P8-F2) |
| `test_version_snapshot.py` | Tests for core/_version_snapshot.py |
