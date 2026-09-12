"""GitHub API request parsing for mutation analysis."""

from __future__ import annotations

import json
import os
import re
import stat
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

if TYPE_CHECKING:
    from autoskillit.hooks._runtime._command_classification import (
        ArgvToken,
        _argv_token_after_prefix,
        _argv_token_value_after_key,
        _consume_argv_flag,
        _FlagArity,
        _normalize_executable,
        command_verb_and_args,
    )
else:
    if __package__ == "autoskillit.hooks._classification":
        from .._runtime._command_classification import (  # noqa: E402
            ArgvToken,
            _argv_token_after_prefix,
            _argv_token_value_after_key,
            _consume_argv_flag,
            _FlagArity,
            _normalize_executable,
            command_verb_and_args,
        )
    else:
        from _command_classification import (  # noqa: E402
            ArgvToken,
            _argv_token_after_prefix,
            _argv_token_value_after_key,
            _consume_argv_flag,
            _FlagArity,
            _normalize_executable,
            command_verb_and_args,
        )


class GitHubMutationKind(StrEnum):
    """Closed mutation families relevant to GitHub review publication."""

    PULL_REVIEW = "pull_review"
    PULL_REVIEW_COMMENT = "pull_review_comment"
    PULL_REVIEW_REPLY = "pull_review_reply"
    GRAPHQL_REVIEW = "graphql_review"
    OTHER = "other"


@dataclass(frozen=True, slots=True)
class GitHubMutationRecord:
    method: str
    route: str
    kind: GitHubMutationKind
    request_count: int
    review_comment_count: int | None


_GITHUB_WRITE_METHODS: frozenset[str] = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_GITHUB_INPUT_LIMIT = 1024 * 1024
_DYNAMIC_SHELL_TOKEN_RE = re.compile(r"\$|`|\*|\?|\[")
_PULL_REVIEW_ROUTE_RE = re.compile(
    r"^/repos/[^/]+/[^/]+/pulls/\d+/reviews(?:/\d+(?:/events)?)?/?$", re.IGNORECASE
)
_PULL_REVIEW_REPLY_ROUTE_RE = re.compile(
    r"^/repos/[^/]+/[^/]+/pulls/\d+/comments/\d+/replies/?$", re.IGNORECASE
)
_PULL_REVIEW_COMMENT_ROUTE_RE = re.compile(
    r"^/repos/[^/]+/[^/]+/(?:pulls/\d+/comments(?:/\d+)?|pulls/comments/\d+)/?$",
    re.IGNORECASE,
)
_GRAPHQL_REVIEW_MUTATIONS: frozenset[str] = frozenset(
    {
        "addPullRequestReview",
        "submitPullRequestReview",
        "dismissPullRequestReview",
        "deletePullRequestReview",
        "addPullRequestReviewComment",
        "addPullRequestReviewThread",
    }
)


def _is_dynamic_shell_value(token: ArgvToken) -> bool:
    return not token.fully_single_quoted and bool(_DYNAMIC_SHELL_TOKEN_RE.search(token.text))


def _normalize_github_route(route: str) -> str:
    if route.startswith(("http://", "https://")):
        return urlsplit(route).path or "/"
    return f"/{route}" if not route.startswith("/") else route


def _github_mutation_kind(route: str) -> GitHubMutationKind:
    normalized = _normalize_github_route(route)
    if _PULL_REVIEW_REPLY_ROUTE_RE.fullmatch(normalized):
        return GitHubMutationKind.PULL_REVIEW_REPLY
    if _PULL_REVIEW_COMMENT_ROUTE_RE.fullmatch(normalized):
        return GitHubMutationKind.PULL_REVIEW_COMMENT
    if _PULL_REVIEW_ROUTE_RE.fullmatch(normalized):
        return GitHubMutationKind.PULL_REVIEW
    return GitHubMutationKind.OTHER


def _json_object_without_duplicate_keys(raw: bytes) -> dict[str, Any]:
    def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    value = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs)
    if not isinstance(value, dict):
        raise ValueError("GitHub --input payload must be a JSON object")
    return value


def _load_literal_github_input(
    value: ArgvToken, *, cwd: str
) -> tuple[dict[str, Any] | None, str, str]:
    if value.text == "-":
        return (None, "unsafe_input_provenance", "GitHub --input stdin is unresolved")
    if not value.text or _is_dynamic_shell_value(value):
        return (None, "dynamic_target", "GitHub --input path is dynamic")
    if os.path.isabs(value.text):
        path = os.path.normpath(value.text)
    else:
        if not cwd or not os.path.isabs(cwd):
            return (None, "cwd_unresolved", "relative GitHub --input requires an absolute cwd")
        path = os.path.normpath(os.path.join(cwd, value.text))
    try:
        before = os.lstat(path)
        if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
            return (
                None,
                "input_inspection_failed",
                "GitHub --input must be a regular non-symlink file",
            )
        if before.st_size > _GITHUB_INPUT_LIMIT:
            return (None, "input_inspection_failed", "GitHub --input exceeds the inspection limit")
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        fd = os.open(path, flags)
        try:
            after = os.fstat(fd)
            if not stat.S_ISREG(after.st_mode) or (before.st_dev, before.st_ino) != (
                after.st_dev,
                after.st_ino,
            ):
                return (None, "input_inspection_failed", "GitHub --input file identity changed")
            chunks: list[bytes] = []
            remaining = _GITHUB_INPUT_LIMIT + 1
            while remaining:
                chunk = os.read(fd, min(65536, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            raw = b"".join(chunks)
        finally:
            os.close(fd)
        if len(raw) > _GITHUB_INPUT_LIMIT:
            return (None, "input_inspection_failed", "GitHub --input exceeds the inspection limit")
        return (_json_object_without_duplicate_keys(raw), "", "")
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        return (
            None,
            "input_inspection_failed",
            f"GitHub --input is not safely inspectable: {exc}",
        )


_INPUT_SAFE_PRIOR_COMMANDS: frozenset[str] = frozenset(
    {"[", "cat", "echo", "false", "head", "ls", "printf", "pwd", "stat", "test", "true", "wc"}
)


def _segment_is_safe_before_literal_input(segment: Sequence[str]) -> bool:
    verb, _ = command_verb_and_args(list(segment))
    return _normalize_executable(verb) in _INPUT_SAFE_PRIOR_COMMANDS


def _comment_count_from_payload(payload: dict[str, Any]) -> tuple[int | None, str, str]:
    if "comments" not in payload:
        return (None, "", "")
    comments = payload["comments"]
    if not isinstance(comments, list):
        return (None, "invalid_input_payload", "GitHub review comments must be a JSON array")
    return (len(comments), "", "")


def _flag_value(
    args: Sequence[ArgvToken], index: int, *, long_name: str, short_name: str | None = None
) -> tuple[ArgvToken | None, int, bool]:
    token = args[index]
    if token.text == long_name or (short_name is not None and token.text == short_name):
        return (
            (args[index + 1], index + 2, True)
            if index + 1 < len(args)
            else (None, index + 1, False)
        )
    if token.text.startswith(f"{long_name}="):
        value_text = token.text.split("=", 1)[1]
        return (_argv_token_after_prefix(token, f"{long_name}=", value_text), index + 1, True)
    if short_name and token.text.startswith(short_name) and token.text != short_name:
        value_text = token.text[len(short_name) :]
        return (_argv_token_after_prefix(token, short_name, value_text), index + 1, True)
    return (None, index, False)


_GH_API_FLAG_SPEC: Mapping[str, _FlagArity] = {
    "-X": _FlagArity.VALUE,
    "--method": _FlagArity.VALUE,
    "--input": _FlagArity.VALUE,
    "-F": _FlagArity.VALUE,
    "--field": _FlagArity.VALUE,
    "-f": _FlagArity.VALUE,
    "--raw-field": _FlagArity.VALUE,
    "-H": _FlagArity.VALUE,
    "--header": _FlagArity.VALUE,
    "--hostname": _FlagArity.VALUE,
    "--cache": _FlagArity.VALUE,
    "-q": _FlagArity.VALUE,
    "--jq": _FlagArity.VALUE,
    "-t": _FlagArity.VALUE,
    "--template": _FlagArity.VALUE,
    "-p": _FlagArity.VALUE,
    "--preview": _FlagArity.VALUE,
    "--paginate": _FlagArity.BOOLEAN,
    "--silent": _FlagArity.BOOLEAN,
    "-i": _FlagArity.BOOLEAN,
    "--include": _FlagArity.BOOLEAN,
    "--verbose": _FlagArity.BOOLEAN,
    "-h": _FlagArity.BOOLEAN,
    "--help": _FlagArity.BOOLEAN,
}


def _analyze_gh_api(
    args: Sequence[ArgvToken],
    *,
    cwd: str,
    input_context_safe: bool,
    resolved_redirect_targets: Sequence[str],
    file_redirect_count: int,
) -> tuple[GitHubMutationRecord | None, str, str, bool]:
    method: ArgvToken | None = None
    route: ArgvToken | None = None
    input_value: ArgvToken | None = None
    field_values: list[ArgvToken] = []
    has_body_fields = False
    paginate = False
    graphql = False
    i = 0
    while i < len(args):
        token = args[i]
        if token.text == "graphql" and route is None:
            graphql = True
            route = ArgvToken("/graphql", True, "'/graphql'")
            i += 1
            continue
        value, next_i, matched = _flag_value(args, i, long_name="--method", short_name="-X")
        if matched or token.text in {"--method", "-X"}:
            if not matched or value is None:
                return (None, "missing_required_value", "GitHub API method is missing", False)
            method, i = value, next_i
            continue
        value, next_i, matched = _flag_value(args, i, long_name="--input")
        if matched or token.text == "--input":
            if not matched or value is None:
                return (None, "missing_required_value", "GitHub --input path is missing", False)
            input_value, has_body_fields, i = value, True, next_i
            continue
        field_match = False
        for long_name, short_name in (("--field", "-F"), ("--raw-field", "-f")):
            value, next_i, matched = _flag_value(
                args, i, long_name=long_name, short_name=short_name
            )
            if matched or token.text in {long_name, short_name}:
                if not matched or value is None:
                    return (None, "missing_required_value", f"{long_name} value is missing", False)
                field_values.append(value)
                has_body_fields, i, field_match = True, next_i, True
                break
        if field_match:
            continue
        if token.text == "--paginate":
            paginate = True
            i += 1
            continue
        if token.text.startswith("-"):
            value, next_i, recognized = _consume_argv_flag(args, i, _GH_API_FLAG_SPEC)
            if not recognized:
                return (
                    None,
                    "unrecognized_gh_api_flag",
                    f"unrecognized gh api flag: {token.text!r}",
                    False,
                )
            if value is None and _GH_API_FLAG_SPEC.get(token.text) == _FlagArity.VALUE:
                return (None, "missing_required_value", f"{token.text} value is missing", False)
            i = next_i
            continue
        if route is None:
            route = token
            i += 1
            continue
        return (
            None,
            "request_cardinality_unresolved",
            "multiple GitHub API routes are unresolved",
            False,
        )
    if route is None:
        return (
            (None, "missing_required_value", "GitHub API route is missing", False)
            if method is not None or has_body_fields
            else (None, "", "", True)
        )
    if _is_dynamic_shell_value(route):
        return (None, "dynamic_target", "GitHub API route is dynamic", False)
    if method is not None and _is_dynamic_shell_value(method):
        return (None, "dynamic_target", "GitHub API method is dynamic", False)
    payload: dict[str, Any] = {}
    query_from_literal_input = input_value is not None
    if input_value is not None:
        if not input_context_safe:
            return (
                None,
                "unsafe_input_provenance",
                "a prior command may rewrite the inspected GitHub --input file",
                False,
            )
        loaded, reason_code, reason = _load_literal_github_input(input_value, cwd=cwd)
        if loaded is None:
            return (None, reason_code, reason, False)
        input_path = (
            os.path.normpath(input_value.text)
            if os.path.isabs(input_value.text)
            else os.path.normpath(os.path.join(cwd, input_value.text))
        )
        if file_redirect_count != len(resolved_redirect_targets):
            return (
                None,
                "unsafe_input_provenance",
                "an output redirect may alias the inspected GitHub --input file",
                False,
            )
        for target in resolved_redirect_targets:
            if os.path.realpath(target) == os.path.realpath(input_path):
                return (
                    None,
                    "unsafe_input_provenance",
                    "an output redirect aliases the inspected GitHub --input file",
                    False,
                )
            if not os.path.exists(target):
                continue
            try:
                if os.path.samefile(target, input_path):
                    return (
                        None,
                        "unsafe_input_provenance",
                        "an output redirect aliases the inspected GitHub --input file",
                        False,
                    )
            except OSError:
                return (
                    None,
                    "unsafe_input_provenance",
                    "an output redirect alias could not be inspected safely",
                    False,
                )
        payload = loaded
    effective_method = (
        method.text.upper()
        if method is not None and method.text
        else ("POST" if has_body_fields else "GET")
    )
    normalized_route = _normalize_github_route(route.text)
    if effective_method not in _GITHUB_WRITE_METHODS:
        return (None, "", "", True)
    if paginate:
        return (
            None,
            "request_cardinality_unresolved",
            "mutation request count is indeterminate with --paginate",
            False,
        )
    comment_count, reason_code, reason = _comment_count_from_payload(payload)
    if reason:
        return (None, reason_code, reason, False)
    if graphql:
        query: ArgvToken | None = None
        raw_query = payload.get("query")
        if isinstance(raw_query, str):
            query = ArgvToken(raw_query, False, raw_query)
        if query is None and not query_from_literal_input:
            for field in field_values:
                field_key, field_separator, _field_value_text = field.text.partition("=")
                if field_separator and field_key == "query":
                    query = _argv_token_value_after_key(field, field_key)
                    break
        if query is None or (not query_from_literal_input and _is_dynamic_shell_value(query)):
            return (None, "dynamic_target", "GraphQL mutation document is unresolved", False)
        if not re.search(r"\bmutation\b", query.text):
            return (None, "", "", True)
        kind = (
            GitHubMutationKind.GRAPHQL_REVIEW
            if any(
                re.search(rf"\b{re.escape(name)}\b", query.text)
                for name in _GRAPHQL_REVIEW_MUTATIONS
            )
            else GitHubMutationKind.OTHER
        )
    else:
        kind = _github_mutation_kind(normalized_route)
    return (
        GitHubMutationRecord(effective_method, normalized_route, kind, 1, comment_count),
        "",
        "",
        False,
    )
