"""Recursive GitHub-mutation cardinality analysis facade.

Request parsing and command-line grammars live in sibling modules; this file
owns the cross-command traversal and the public analysis result.
"""

from __future__ import annotations

import os
import re
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from autoskillit.hooks import _github_mutation_cli_analysis as _cli
    from autoskillit.hooks import _github_mutation_request_analysis as _request
    from autoskillit.hooks._command_classification import (
        ArgvToken,
        _select_executable_argv_tokens,
        _verb_start_index,
    )
else:
    if __package__ == "autoskillit.hooks":
        from . import _github_mutation_cli_analysis as _cli
        from . import _github_mutation_request_analysis as _request
    else:
        import _github_mutation_cli_analysis as _cli
        import _github_mutation_request_analysis as _request
    from _command_classification import (  # noqa: E402
        ArgvToken,
        _select_executable_argv_tokens,
        _verb_start_index,
    )

GitHubMutationKind = _request.GitHubMutationKind
GitHubMutationRecord = _request.GitHubMutationRecord
_DYNAMIC_SHELL_TOKEN_RE = _request._DYNAMIC_SHELL_TOKEN_RE
_GH_API_FLAG_SPEC = _request._GH_API_FLAG_SPEC
_is_dynamic_shell_value = _request._is_dynamic_shell_value
_segment_is_safe_before_literal_input = _request._segment_is_safe_before_literal_input
_GH_HELP_FLAGS = _cli._GH_HELP_FLAGS
_GH_READ_ONLY_SUBCOMMANDS = _cli._GH_READ_ONLY_SUBCOMMANDS
_CURL_FLAG_SPEC = _cli._CURL_FLAG_SPEC
_analyze_gh_segment = _cli._analyze_gh_segment
_analyze_curl_segment = _cli._analyze_curl_segment


def _command_verb_and_args(segment: Sequence[str]) -> tuple[str, list[str]]:
    from _command_classification import command_verb_and_args

    return command_verb_and_args(list(segment))


def _tokenize_with_redirects(command: str) -> list[Any]:
    from _command_classification import _tokenize_command_segments_with_redirects

    return _tokenize_command_segments_with_redirects(command)


def _normalize_executable_call(token: str) -> str:
    from _command_classification import _normalize_executable

    return _normalize_executable(token)


def _partition_output_redirects_call(
    tokens: Sequence[str], *, cwd: str, redirect_syntax: Sequence[bool] | None = None
) -> tuple[list[str], list[str], int]:
    from _command_classification import _partition_output_redirects

    return _partition_output_redirects(tokens, cwd=cwd, redirect_syntax=redirect_syntax)


def _extract_interpreter_segment_specs_call(segment: Sequence[str]) -> tuple[list[Any], bool]:
    from _command_classification import _extract_interpreter_segment_specs

    return _extract_interpreter_segment_specs(segment)


def _segment_evaluates_shell_payload_call(tokens: list[str], payload: str) -> bool:
    from _command_classification import _segment_evaluates_shell_payload

    return _segment_evaluates_shell_payload(tokens, payload)


def _extract_shell_command_payloads_call(command: str) -> list[str]:
    from _command_classification import extract_shell_command_payloads

    return extract_shell_command_payloads(command)


class GitHubMutationStatus(StrEnum):
    NONE = "none"
    SINGLE_RESOLVED = "single_resolved"
    MULTIPLE = "multiple"
    UNRESOLVED = "unresolved"


@dataclass(frozen=True, slots=True)
class GitHubMutationAnalysis:
    status: GitHubMutationStatus
    mutations: tuple[GitHubMutationRecord, ...]
    request_count: int | None
    review_comment_count: int | None
    reason_code: str
    reason: str


_REPEATABLE_SHELL_RE = re.compile(
    r"(?:^|[;&|]\s*)(?:for|while|until)\b|\b[A-Za-z_][A-Za-z0-9_]*\s*\(\s*\)\s*\{"
)
_PROCESS_SUBSTITUTION_RE = re.compile(r"[<>]\(")
_POSSIBLE_GITHUB_EXEC_RE = re.compile(
    r"""(?:^|[\s;&|()'"])(?:[^\s;&|()'"]*/)?(?:gh|curl)(?:[\s'"]|$)""",
    re.IGNORECASE,
)
_GH_DISPATCH_WORDS: frozenset[str] = frozenset({"eval", "xargs", "source", "."})
_POSSIBLE_GITHUB_EXEC_NAMES: frozenset[str] = frozenset({"gh", "curl"})


def _segment_has_possible_github_exec_token(segment: Sequence[str]) -> bool:
    verb, args = _command_verb_and_args(list(segment))
    if _normalize_executable_call(verb) in _POSSIBLE_GITHUB_EXEC_NAMES:
        return True
    body: list[str] | None = None
    if verb == "{":
        body = args
    elif verb.endswith("()") and args[:1] == ["{"]:
        body = args[1:]
    if body is None:
        return False
    body_verb, _body_args = _command_verb_and_args(body)
    return _normalize_executable_call(body_verb) in _POSSIBLE_GITHUB_EXEC_NAMES


def _segments_have_possible_github_exec_token(segments: Sequence[Sequence[str]]) -> bool:
    return any(_segment_has_possible_github_exec_token(segment) for segment in segments)


def _segments_have_dispatch_word_exec_risk(segments: Sequence[Sequence[str]]) -> bool:
    for segment in segments:
        verb, _args = _command_verb_and_args(list(segment))
        if verb in _GH_DISPATCH_WORDS and _POSSIBLE_GITHUB_EXEC_RE.search(" ".join(segment)):
            return True
    return False


def _none_github_analysis() -> GitHubMutationAnalysis:
    return GitHubMutationAnalysis(GitHubMutationStatus.NONE, (), 0, None, "", "")


def _unresolved_github_analysis(
    *, reason_code: str, reason: str, mutations: Sequence[GitHubMutationRecord] = ()
) -> GitHubMutationAnalysis:
    return GitHubMutationAnalysis(
        GitHubMutationStatus.UNRESOLVED, tuple(mutations), None, None, reason_code, reason
    )


def _segment_cwd(segment: Sequence[str], cwd: str) -> str:
    current = cwd
    for index, token in enumerate(segment):
        if token in {"-C", "--chdir"} and index and segment[index - 1] == "env":
            if index + 1 < len(segment):
                value = segment[index + 1]
                current = (
                    value
                    if os.path.isabs(value)
                    else os.path.normpath(os.path.join(current, value))
                    if current
                    else current
                )
        elif token.startswith("--chdir=") and "env" in segment[:index]:
            value = token.split("=", 1)[1]
            current = (
                value
                if os.path.isabs(value)
                else os.path.normpath(os.path.join(current, value))
                if current
                else current
            )
    return current


def _analyze_github_segment(
    segment: Sequence[str],
    *,
    cwd: str,
    input_context_safe: bool = True,
    resolved_redirect_targets: Sequence[str] = (),
    file_redirect_count: int = 0,
    argv_tokens: Sequence[ArgvToken] | None = None,
) -> tuple[list[GitHubMutationRecord], str, str]:
    verb, args = _command_verb_and_args(list(segment))
    executable = _normalize_executable_call(verb)
    if argv_tokens is None:
        argv_tokens = [ArgvToken(token, True, token) for token in segment]
    start = _verb_start_index(list(segment))
    argv_args = list(argv_tokens[start + 1 :]) if start is not None else []
    if executable == "gh":
        record, reason_code, reason = _analyze_gh_segment(
            args,
            argv_args=argv_args,
            cwd=_segment_cwd(segment, cwd),
            input_context_safe=input_context_safe,
            resolved_redirect_targets=resolved_redirect_targets,
            file_redirect_count=file_redirect_count,
        )
        return ([record] if record is not None else [], reason_code, reason)
    return _analyze_curl_segment(argv_args) if executable == "curl" else ([], "", "")


def analyze_github_mutations(command: str, *, cwd: str = "") -> GitHubMutationAnalysis:
    if not isinstance(command, str) or not command.strip():
        return _none_github_analysis()
    records: list[GitHubMutationRecord] = []
    reasons: list[tuple[str, str]] = []
    queue: list[tuple[str, str, int, bool, tuple[str, ...], int]] = [
        (command, cwd, 0, True, (), 0)
    ]
    argv_payloads: list[tuple[list[str], str, bool, tuple[str, ...], int]] = []
    while queue:
        payload, payload_cwd, depth, inherited_input_safe, outer_targets, outer_count = queue.pop(
            0
        )
        if depth > 32:
            reasons.append(
                ("shell_structure_unresolved", "nested mutation command depth is unresolved")
            )
            continue
        tokenized_segments = _tokenize_with_redirects(payload)
        segments = [segment.tokens for segment in tokenized_segments]
        if not tokenized_segments and payload.strip():
            if _POSSIBLE_GITHUB_EXEC_RE.search(payload):
                reasons.append(
                    (
                        "shell_parse_unresolved",
                        "mutation-bearing shell payload could not be parsed",
                    )
                )
            continue
        current_cwd = payload_cwd
        input_context_safe = inherited_input_safe
        nested_contexts: list[tuple[list[str], str, bool, tuple[str, ...], int]] = []
        for command_segment in tokenized_segments:
            raw_segment = command_segment.tokens
            executable_tokens, redirect_targets, file_redirect_count = (
                _partition_output_redirects_call(
                    raw_segment, cwd=current_cwd, redirect_syntax=command_segment.redirect_syntax
                )
            )
            executable_argv_tokens = _select_executable_argv_tokens(
                raw_segment,
                command_segment.argv_tokens,
                cwd=current_cwd,
                redirect_syntax=command_segment.redirect_syntax,
            )
            active_targets = outer_targets + tuple(redirect_targets)
            active_count = outer_count + file_redirect_count
            segment_cwd = _segment_cwd(executable_tokens, current_cwd)
            nested_contexts.append(
                (raw_segment, segment_cwd, input_context_safe, active_targets, active_count)
            )
            verb, args = _command_verb_and_args(executable_tokens)
            if _normalize_executable_call(verb) == "cd":
                input_context_safe = input_context_safe and file_redirect_count == 0
                cd_start = _verb_start_index(executable_tokens)
                assert cd_start is not None
                cd_argv_args = executable_argv_tokens[cd_start + 1 :]
                if len(args) != 1 or _is_dynamic_shell_value(cd_argv_args[0]):
                    reasons.append(("cwd_unresolved", "shell cwd transition is unresolved"))
                elif os.path.isabs(args[0]):
                    current_cwd = os.path.normpath(args[0])
                elif current_cwd:
                    current_cwd = os.path.normpath(os.path.join(current_cwd, args[0]))
                else:
                    reasons.append(
                        ("cwd_unresolved", "relative shell cwd transition has no authority")
                    )
                continue
            found, reason_code, reason = _analyze_github_segment(
                executable_tokens,
                cwd=current_cwd,
                input_context_safe=input_context_safe,
                resolved_redirect_targets=active_targets,
                file_redirect_count=active_count,
                argv_tokens=executable_argv_tokens,
            )
            records.extend(found)
            if reason:
                reasons.append((reason_code, reason))
            specs, has_unresolved = _extract_interpreter_segment_specs_call(executable_tokens)
            if has_unresolved and _POSSIBLE_GITHUB_EXEC_RE.search(payload):
                reasons.append(
                    (
                        "interpreter_structure_unresolved",
                        "interpreter subprocess command or cwd is unresolved",
                    )
                )
            for spec in specs:
                interpreter_cwd = segment_cwd
                if spec.cwd is not None:
                    if os.path.isabs(spec.cwd):
                        interpreter_cwd = os.path.normpath(spec.cwd)
                    elif current_cwd:
                        interpreter_cwd = os.path.normpath(os.path.join(current_cwd, spec.cwd))
                    else:
                        reasons.append(
                            ("cwd_unresolved", "relative interpreter cwd has no authority")
                        )
                        continue
                if isinstance(spec.payload, str):
                    queue.append(
                        (
                            spec.payload,
                            interpreter_cwd,
                            depth + 1,
                            input_context_safe,
                            active_targets,
                            active_count,
                        )
                    )
                else:
                    argv_payloads.append(
                        (
                            spec.payload,
                            interpreter_cwd,
                            input_context_safe,
                            active_targets,
                            active_count,
                        )
                    )
            input_context_safe = (
                input_context_safe
                and file_redirect_count == 0
                and _segment_is_safe_before_literal_input(executable_tokens)
            )
        remaining_contexts = list(nested_contexts)
        for nested in _extract_shell_command_payloads_call(payload):
            matching_index = next(
                (
                    index
                    for index, context in enumerate(remaining_contexts)
                    if _segment_evaluates_shell_payload_call(context[0], nested)
                ),
                None,
            )
            context = (
                ([], payload_cwd, inherited_input_safe, outer_targets, outer_count)
                if matching_index is None
                else remaining_contexts.pop(matching_index)
            )
            _raw, nested_cwd, nested_input_safe, nested_targets, nested_count = context
            queue.append(
                (nested, nested_cwd, depth + 1, nested_input_safe, nested_targets, nested_count)
            )
        if (
            _REPEATABLE_SHELL_RE.search(payload) or _PROCESS_SUBSTITUTION_RE.search(payload)
        ) and _segments_have_possible_github_exec_token(segments):
            reasons.append(
                (
                    "shell_structure_unresolved",
                    "shell loop or wrapper has unresolved mutation cardinality",
                )
            )
        if _segments_have_dispatch_word_exec_risk(segments):
            reasons.append(
                (
                    "shell_structure_unresolved",
                    "mutation cardinality is unresolved in a shell wrapper",
                )
            )
    for argv, argv_cwd, input_context_safe, inherited_targets, redirect_count in argv_payloads:
        found, reason_code, reason = _analyze_github_segment(
            argv,
            cwd=argv_cwd,
            input_context_safe=input_context_safe,
            resolved_redirect_targets=inherited_targets,
            file_redirect_count=redirect_count,
        )
        records.extend(found)
        if reason:
            reasons.append((reason_code, reason))
    if reasons:
        unique_reasons = list(dict.fromkeys(reasons))
        return _unresolved_github_analysis(
            reason_code=unique_reasons[0][0],
            reason="; ".join(reason for _, reason in unique_reasons),
            mutations=records,
        )
    request_count = sum(record.request_count for record in records)
    if request_count == 0:
        return _none_github_analysis()
    if request_count != 1 or len(records) != 1:
        return GitHubMutationAnalysis(
            GitHubMutationStatus.MULTIPLE, tuple(records), request_count, None, "", ""
        )
    record = records[0]
    return GitHubMutationAnalysis(
        GitHubMutationStatus.SINGLE_RESOLVED, (record,), 1, record.review_comment_count, "", ""
    )
