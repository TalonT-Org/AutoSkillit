"""hooks/_runtime/ — stdlib-only hook utilities. Re-exports the public surface."""

from __future__ import annotations

from autoskillit.hooks._runtime._hook_constants import (
    DENY_REASON_BY_GUARD, DENY_TRIGGER_BY_GUARD, EXEMPT_SESSION_TYPES_BY_GUARD,
    EXEMPT_SKILLS_BY_GUARD, MANAGED_PARENT_ALLOWED_TOOLS, MANAGED_PARENT_ALLOWED_TOOL_SET,
    RISKY_GH_SUBCOMMANDS, RISKY_GIT_OPERATIONS,
)

from autoskillit.hooks._runtime._hook_payload import (
    ParsedHookCommand, PayloadAnomaly, _RUN_CMD_SUFFIX, _absolute_string_or_blank,
    extract_apply_patch_text, normalize_payload_cwd, parse_hook_command, resolve_kitchen_state_dir,
    resolve_state_root,
)

from autoskillit.hooks._runtime._hook_settings import (
    DEFAULT_BUFFER_SECONDS, DEFAULT_CACHE_MAX_AGE, DEFAULT_CACHE_PATH, DIAGNOSTIC_KEYS,
    ENV_BUFFER_SECONDS, ENV_CACHE_MAX_AGE, ENV_CACHE_PATH, ENV_DISABLED, HOOK_CONFIG_FILENAME,
    HOOK_CONFIG_OVERLAY_FILENAME, HOOK_DIR_COMPONENTS, MARKER_TTL_SECONDS,
    OUTPUT_BUDGET_POLICY_HOOK_PAYLOAD_KEYS, QUOTA_GUARD_HOOK_PAYLOAD_KEYS, QuotaHookSettings,
    TOKEN_USAGE_FILE_KEYS, _AUTOSKILLIT_LOG_DIR_ENV, _MAPPING_OVERLAY_DOMAINS, _MAX_HOOK_LOG_LINES,
    _V1_TOKEN_FIELD_ALIASES, _append_and_trim_jsonl_line, _atomic_write_marker, _default_state_root,
    _read_hook_config, _resolve_int, _resolve_quota_disable_state_dir, clear_quota_disable_marker,
    is_quota_guard_disabled_for_session, merge_hook_configs, payload_managed_codex_route,
    quota_disable_marker_path, read_merged_hook_config, read_quota_cache, read_quota_disable_marker,
    read_session_binding, resolve_quota_log_dir, resolve_quota_settings, session_join_required,
    session_managed_codex_route, session_managed_scope, validate_session_id,
    write_dispatch_diagnostic, write_join_diagnostic, write_quota_disable_marker,
    write_quota_log_event,
)

from autoskillit.hooks._runtime._hook_utils import (
    STEP_SUFFIX_RE, find_project_root,
)

from autoskillit.hooks._runtime._policy_event import (
    POLICY_EVENT_SCHEMA_VERSION, PolicyEvent, render_provenance_prefix,
)

from autoskillit.hooks._runtime._command_classification import (
    ArgvToken, DECLARABLE_SOURCE_PATH_PATTERNS, PROTECTED_SOURCE_PATH_PATTERNS, SearchPattern,
    _COMMAND_WRAPPERS, _CommandSegment, _ENV_NO_VALUE_FLAGS, _ENV_VALUE_FLAGS,
    _ENV_VALUE_FLAGS_ATTACHED, _FD_DUPLICATION_RE, _FD_REDIRECT_RE, _FlagArity,
    _GIT_ADD_CONTENT_FLAGS, _GIT_DIFF_CONTENT_FLAGS, _GIT_DIFF_METADATA_FLAGS, _GIT_GLOBAL_FLAGS,
    _GIT_GLOBAL_FLAGS_WITH_VALUE, _GIT_GLOBAL_FLAG_SPEC, _GIT_STATUS_CONTENT_FLAGS, _HEREDOC_BODY_RE,
    _HEREDOC_MARKER_RE, _INTERPRETER_LINE_RE, _INTERPRETER_RE, _InterpreterCommandSpec,
    _LITERAL_OPEN_PATH_RE, _LITERAL_PATH_CONSTRUCTOR_RE, _NESTED_SHELL_RE, _PIP_GLOBAL_FLAG_SPEC,
    _PROTECTED_PATH_METADATA_GIT_SUBCOMMANDS, _PROTECTED_READ_SHELL_OPS, _PYTHON_OS_EXEC_FUNCS,
    _PYTHON_SUBPROCESS_FUNCS, _REDIRECT_OP_ONLY_RE, _REDIRECT_TOKEN_RE, _SHELL_CONTROL_WORDS,
    _SHELL_INTERPRETERS, _SHELL_OPERATORS, _SHELL_OPERATOR_CHARS, _SHELL_OPS, _SHELL_STATE_VAR_RE,
    _SHELL_SUBSTITUTION_RE, _SHELL_VAR_RE, _SUBPROCESS_APIS_RE, _TRAILING_SHELL_CLOSERS, _WC_FLAG_RE,
    _WRAPPERS_WITH_DURATION, _WRAPPERS_WITH_SHORT_FLAG, _WRAPPER_VALUE_FLAGS_ATTACHED,
    _WRAPPER_VALUE_FLAGS_DETACHED, _WRITE_APIS_RE, _WRITE_CALL_SITE_RE, _argv_token_after_prefix,
    _argv_token_value_after_key, _command_start_index, _consume_argv_flag, _consume_env,
    _consume_str_flag, _consume_wrapper_options, _extract_interpreter_command_specs,
    _extract_interpreter_segment_specs, _extract_substitution_payloads, _find_substitution_end,
    _is_allowed_wc_flag, _is_posix_assignment, _is_shell_interpreter, _literal_to_argv,
    _literal_to_string, _mark_unquoted_output_redirects, _normalize_executable,
    _normalize_newlines_for_tokenize, _parse_python_program_literals,
    _partition_output_redirect_indices, _partition_output_redirects, _python_program_command_specs,
    _segment_evaluates_shell_payload, _select_executable_argv_tokens,
    _tokenize_command_segments_with_redirects, _tokenize_protected_read_segments, _verb_start_index,
    command_has_blocked_protected_path_read, command_verb, command_verb_and_args,
    extract_git_subcommand_and_flags, extract_interpreter_command_payloads,
    extract_interpreter_write_paths, extract_redirect_targets, extract_shell_command_payloads,
    has_interpreter_wrapped_command, has_interpreter_write, has_nested_shell,
    is_allowed_protected_path_metadata_command, is_gh_command, is_git_command, resolve_write_target,
    strip_heredoc_bodies, tokenize_command_segments, tokenize_shell_payload_segments,
)

from autoskillit.hooks._runtime._github_mutation_analysis import (
    GitHubMutationAnalysis, GitHubMutationKind, GitHubMutationRecord, GitHubMutationStatus,
    _CURL_FLAG_SPEC, _DYNAMIC_SHELL_TOKEN_RE, _GH_API_FLAG_SPEC, _GH_DISPATCH_WORDS, _GH_HELP_FLAGS,
    _GH_ISSUE_EDIT_LONG_VALUE_FLAGS, _GH_ISSUE_EDIT_SHORT_VALUE_FLAGS, _GH_ISSUE_URL_RE,
    _GH_KNOWN_VALUE_FLAGS, _GH_MUTATION_SUBCOMMANDS, _GH_READ_ONLY_SUBCOMMANDS, _GITHUB_INPUT_LIMIT,
    _GITHUB_WRITE_METHODS, _GRAPHQL_REVIEW_MUTATIONS, _INPUT_SAFE_PRIOR_COMMANDS,
    _POSSIBLE_GITHUB_EXEC_NAMES, _POSSIBLE_GITHUB_EXEC_RE, _PROCESS_SUBSTITUTION_RE,
    _PULL_REVIEW_COMMENT_ROUTE_RE, _PULL_REVIEW_REPLY_ROUTE_RE, _PULL_REVIEW_ROUTE_RE,
    _REPEATABLE_SHELL_RE, _analyze_curl_segment, _analyze_gh_api, _analyze_gh_segment,
    _analyze_github_segment, _command_verb_and_args, _comment_count_from_payload,
    _extract_interpreter_segment_specs_call, _extract_shell_command_payloads_call, _flag_value,
    _gh_args_have_bare_help_flag, _github_mutation_kind, _is_dynamic_shell_value,
    _is_static_issue_edit_target, _issue_edit_request_count, _json_object_without_duplicate_keys,
    _load_literal_github_input, _none_github_analysis, _normalize_executable_call,
    _normalize_github_route, _partition_output_redirects_call, _segment_cwd,
    _segment_evaluates_shell_payload_call, _segment_has_possible_github_exec_token,
    _segment_is_safe_before_literal_input, _segments_have_dispatch_word_exec_risk,
    _segments_have_possible_github_exec_token, _tokenize_with_redirects, _unresolved_github_analysis,
    analyze_github_mutations,
)

from autoskillit.hooks._runtime._exploration_request_record import (
    SUPPORTED_EXPLORATION_REQUEST_TOOLS, _CLAIM_PREFIX, _DIRECTORY_FLAGS, _MAX_CLEANUP_ENTRIES,
    _MAX_RECORD_BYTES, _READ_FLAGS, _RECORD_PREFIX, _RECORD_SUFFIX, _REQUEST_DIRECTORY,
    _REQUEST_TTL_SECONDS, _TOKEN_PATTERN, _WRITE_FLAGS, _cleanup_expired, _clock,
    _open_child_directory, _open_request_directory, _parse_record, _read_bounded, _record_name,
    _valid_session_id, _valid_tool_name, _write_all, consume_exploration_request_record,
    write_exploration_request_record,
)

__all__ = [
    "DENY_REASON_BY_GUARD",
    "DENY_TRIGGER_BY_GUARD",
    "EXEMPT_SESSION_TYPES_BY_GUARD",
    "EXEMPT_SKILLS_BY_GUARD",
    "MANAGED_PARENT_ALLOWED_TOOLS",
    "RISKY_GH_SUBCOMMANDS",
    "RISKY_GIT_OPERATIONS",
    "PROTECTED_SOURCE_PATH_PATTERNS",
    "_INTERPRETER_LINE_RE",
    "_WRITE_APIS_RE",
    "command_has_blocked_protected_path_read",
    "analyze_github_mutations",
    "consume_exploration_request_record",
]
