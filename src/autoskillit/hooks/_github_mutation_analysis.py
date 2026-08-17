"""GitHub-mutation analysis extracted from _command_classification.

This module is the consumer of tokenization primitives defined in
_command_classification. Tokenization primitives are imported lazily
inside each wrapper to avoid a circular import — the bare-name
_command_classification reference is resolved after the source module
is fully populated.
"""

from __future__ import annotations

import json
import os
import re
import stat
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any
from urllib.parse import urlsplit


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
    tokens: Sequence[str],
    *,
    cwd: str,
    redirect_syntax: Sequence[bool] | None = None,
) -> tuple[list[str], list[str], int]:
    from _command_classification import _partition_output_redirects

    return _partition_output_redirects(tokens, cwd=cwd, redirect_syntax=redirect_syntax)


def _extract_interpreter_segment_specs_call(
    segment: Sequence[str],
) -> tuple[list[Any], bool]:
    from _command_classification import _extract_interpreter_segment_specs

    return _extract_interpreter_segment_specs(segment)


def _segment_evaluates_shell_payload_call(tokens: list[str], payload: str) -> bool:
    from _command_classification import _segment_evaluates_shell_payload

    return _segment_evaluates_shell_payload(tokens, payload)


def _extract_shell_command_payloads_call(command: str) -> list[str]:
    from _command_classification import extract_shell_command_payloads

    return extract_shell_command_payloads(command)


class GitHubMutationStatus(StrEnum):
    """Deterministic cardinality result for a shell command."""

    NONE = "none"
    SINGLE_RESOLVED = "single_resolved"
    MULTIPLE = "multiple"
    UNRESOLVED = "unresolved"


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


@dataclass(frozen=True, slots=True)
class GitHubMutationAnalysis:
    status: GitHubMutationStatus
    mutations: tuple[GitHubMutationRecord, ...]
    request_count: int | None
    review_comment_count: int | None
    reason_code: str
    reason: str


_GITHUB_WRITE_METHODS: frozenset[str] = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_GITHUB_INPUT_LIMIT = 1024 * 1024
_DYNAMIC_SHELL_TOKEN_RE = re.compile(r"\$|`|\*|\?|\[")
_REPEATABLE_SHELL_RE = re.compile(
    r"(?:^|[;&|]\s*)(?:for|while|until)\b"
    r"|\b[A-Za-z_][A-Za-z0-9_]*\s*\(\s*\)\s*\{"
)
_PROCESS_SUBSTITUTION_RE = re.compile(r"[<>]\(")
_POSSIBLE_GITHUB_EXEC_RE = re.compile(
    r"""(?:^|[\s;&|()'"])(?:[^\s;&|()'"]*/)?(?:gh|curl)(?:[\s'"]|$)""",
    re.IGNORECASE,
)
_GH_DISPATCH_WORDS: frozenset[str] = frozenset({"eval", "xargs", "source", "."})
_POSSIBLE_GITHUB_EXEC_NAMES: frozenset[str] = frozenset({"gh", "curl"})
_PULL_REVIEW_ROUTE_RE = re.compile(
    r"^/repos/[^/]+/[^/]+/pulls/\d+/reviews"
    r"(?:/\d+(?:/events)?)?/?$",
    re.IGNORECASE,
)
_PULL_REVIEW_REPLY_ROUTE_RE = re.compile(
    r"^/repos/[^/]+/[^/]+/pulls/\d+/comments/\d+/replies/?$",
    re.IGNORECASE,
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


def _segment_has_possible_github_exec_token(segment: Sequence[str]) -> bool:
    """True when gh/curl (path-normalized) is in command position within *segment*.

    Checks the segment's own command verb — via ``command_verb_and_args``,
    which already skips env/wrapper prefixes and loop control words like
    ``do``/``then`` — not every token. A bare-word argument that merely
    mentions ``gh``/``curl`` (e.g. ``echo "gh"``, which shlex collapses to
    the same tokens as an unquoted ``echo gh``) is never in command position
    and must not trip this check.

    An inline function definition (``name() {``) or a bare compound-command
    opener (``{``) fuses its body's first command into the same shlex
    segment as the opener — the segmenter only splits on shell operators
    like ``;``, never on ``{`` — so the body's own verb would otherwise be
    invisible to a verb-only check. When the segment's verb is such an
    opener, the check is re-applied to the token immediately following it.
    """
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
    """True when any segment contains gh/curl as a standalone token.

    Used to bound repeatable shell constructs (for/while/until, an inline
    function body fused into its opener's segment): a possible-exec token
    reachable through repetition is cardinality-unresolved regardless of
    which segment it lands in after shlex splits the payload on ``;``.
    """
    return any(_segment_has_possible_github_exec_token(segment) for segment in segments)


def _segments_have_dispatch_word_exec_risk(segments: Sequence[Sequence[str]]) -> bool:
    """True when a segment opened by eval/xargs/source/. also mentions gh/curl.

    Scoped to the dispatch word's own segment, not the whole payload: a
    ``gh`` command appearing as an unrelated *sibling* segment — e.g.
    ``source .venv/bin/activate && gh pr view`` — is already independently
    walked and classified by the normal segment loop and must not be
    treated as hidden behind an unrelated dispatch word earlier on the
    same line. ``eval``/``xargs`` genuinely consume or hand off the
    following text to a fresh command, so their own segment's joined text
    is searched (not just its tokens) to catch eval's single quoted
    argument token.
    """
    for segment in segments:
        verb, _args = _command_verb_and_args(list(segment))
        if verb not in _GH_DISPATCH_WORDS:
            continue
        if _POSSIBLE_GITHUB_EXEC_RE.search(" ".join(segment)):
            return True
    return False


def _none_github_analysis() -> GitHubMutationAnalysis:
    return GitHubMutationAnalysis(
        status=GitHubMutationStatus.NONE,
        mutations=(),
        request_count=0,
        review_comment_count=None,
        reason_code="",
        reason="",
    )


def _unresolved_github_analysis(
    *,
    reason_code: str,
    reason: str,
    mutations: Sequence[GitHubMutationRecord] = (),
) -> GitHubMutationAnalysis:
    return GitHubMutationAnalysis(
        status=GitHubMutationStatus.UNRESOLVED,
        mutations=tuple(mutations),
        request_count=None,
        review_comment_count=None,
        reason_code=reason_code,
        reason=reason,
    )


def _is_dynamic_shell_value(value: str) -> bool:
    return bool(_DYNAMIC_SHELL_TOKEN_RE.search(value))


def _normalize_github_route(route: str) -> str:
    if route.startswith(("http://", "https://")):
        parsed = urlsplit(route)
        return parsed.path or "/"
    if not route.startswith("/"):
        return f"/{route}"
    return route


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
    value: str,
    *,
    cwd: str,
) -> tuple[dict[str, Any] | None, str, str]:
    if value == "-":
        return (None, "unsafe_input_provenance", "GitHub --input stdin is unresolved")
    if not value or _is_dynamic_shell_value(value):
        return (None, "dynamic_target", "GitHub --input path is dynamic")
    if os.path.isabs(value):
        path = os.path.normpath(value)
    else:
        if not cwd or not os.path.isabs(cwd):
            return (
                None,
                "cwd_unresolved",
                "relative GitHub --input requires an absolute cwd",
            )
        path = os.path.normpath(os.path.join(cwd, value))

    try:
        before = os.lstat(path)
        if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
            return (
                None,
                "input_inspection_failed",
                "GitHub --input must be a regular non-symlink file",
            )
        if before.st_size > _GITHUB_INPUT_LIMIT:
            return (
                None,
                "input_inspection_failed",
                "GitHub --input exceeds the inspection limit",
            )
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
                return (
                    None,
                    "input_inspection_failed",
                    "GitHub --input file identity changed",
                )
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
            return (
                None,
                "input_inspection_failed",
                "GitHub --input exceeds the inspection limit",
            )
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
    """Return whether *segment* is proven unable to rewrite a later input file."""
    verb, _ = _command_verb_and_args(list(segment))
    executable = _normalize_executable_call(verb)
    return executable in _INPUT_SAFE_PRIOR_COMMANDS


def _comment_count_from_payload(payload: dict[str, Any]) -> tuple[int | None, str, str]:
    if "comments" not in payload:
        return (None, "", "")
    comments = payload["comments"]
    if not isinstance(comments, list):
        return (
            None,
            "invalid_input_payload",
            "GitHub review comments must be a JSON array",
        )
    return (len(comments), "", "")


def _flag_value(
    args: Sequence[str],
    index: int,
    *,
    long_name: str,
    short_name: str | None = None,
) -> tuple[str | None, int, bool]:
    token = args[index]
    if token == long_name or (short_name is not None and token == short_name):
        if index + 1 >= len(args):
            return (None, index + 1, False)
        return (args[index + 1], index + 2, True)
    if token.startswith(f"{long_name}="):
        return (token.split("=", 1)[1], index + 1, True)
    if short_name and token.startswith(short_name) and token != short_name:
        return (token[len(short_name) :], index + 1, True)
    return (None, index, False)


def _analyze_gh_api(
    args: Sequence[str],
    *,
    cwd: str,
    input_context_safe: bool,
    resolved_redirect_targets: Sequence[str],
    file_redirect_count: int,
) -> tuple[GitHubMutationRecord | None, str, str]:
    method: str | None = None
    route: str | None = None
    input_value: str | None = None
    field_values: list[str] = []
    has_body_fields = False
    paginate = False
    graphql = False
    i = 0

    while i < len(args):
        token = args[i]
        if token == "graphql" and route is None:
            graphql = True
            route = "/graphql"
            i += 1
            continue

        value, next_i, matched = _flag_value(args, i, long_name="--method", short_name="-X")
        if matched or token in {"--method", "-X"}:
            if not matched or value is None:
                return (None, "missing_required_value", "GitHub API method is missing")
            method = value.upper()
            i = next_i
            continue

        value, next_i, matched = _flag_value(args, i, long_name="--input")
        if matched or token == "--input":
            if not matched or value is None:
                return (None, "missing_required_value", "GitHub --input path is missing")
            input_value = value
            has_body_fields = True
            i = next_i
            continue

        field_match = False
        for long_name, short_name in (
            ("--field", "-F"),
            ("--raw-field", "-f"),
        ):
            value, next_i, matched = _flag_value(
                args, i, long_name=long_name, short_name=short_name
            )
            if matched or token in {long_name, short_name}:
                if not matched or value is None:
                    return (None, "missing_required_value", f"{long_name} value is missing")
                field_values.append(value)
                has_body_fields = True
                i = next_i
                field_match = True
                break
        if field_match:
            continue

        if token == "--paginate":
            paginate = True
            i += 1
            continue
        if token in {"-H", "--header", "--hostname", "--cache"}:
            if i + 1 >= len(args):
                return (None, "missing_required_value", f"{token} value is missing")
            i += 2
            continue
        if token.startswith(("--header=", "--hostname=", "--cache=")):
            i += 1
            continue
        if token.startswith("-"):
            i += 1
            continue
        if route is None:
            route = token
            i += 1
            continue
        return (
            None,
            "request_cardinality_unresolved",
            "multiple GitHub API routes are unresolved",
        )

    if route is None:
        if method is not None or has_body_fields:
            return (None, "missing_required_value", "GitHub API route is missing")
        return (None, "", "")
    if _is_dynamic_shell_value(route):
        return (None, "dynamic_target", "GitHub API route is dynamic")
    if method is not None and _is_dynamic_shell_value(method):
        return (None, "dynamic_target", "GitHub API method is dynamic")

    payload: dict[str, Any] = {}
    query_from_literal_input = input_value is not None
    if input_value is not None:
        if not input_context_safe:
            return (
                None,
                "unsafe_input_provenance",
                "a prior command may rewrite the inspected GitHub --input file",
            )
        loaded, reason_code, reason = _load_literal_github_input(input_value, cwd=cwd)
        if loaded is None:
            return (None, reason_code, reason)
        input_path = (
            os.path.normpath(input_value)
            if os.path.isabs(input_value)
            else os.path.normpath(os.path.join(cwd, input_value))
        )
        if file_redirect_count != len(resolved_redirect_targets):
            return (
                None,
                "unsafe_input_provenance",
                "an output redirect may alias the inspected GitHub --input file",
            )
        redirect_aliases_input = False
        for target in resolved_redirect_targets:
            if os.path.realpath(target) == os.path.realpath(input_path):
                redirect_aliases_input = True
                break
            if not os.path.exists(target):
                continue
            try:
                redirect_aliases_input = os.path.samefile(target, input_path)
            except OSError:
                return (
                    None,
                    "unsafe_input_provenance",
                    "an output redirect alias could not be inspected safely",
                )
            if redirect_aliases_input:
                break
        if redirect_aliases_input:
            return (
                None,
                "unsafe_input_provenance",
                "an output redirect aliases the inspected GitHub --input file",
            )
        payload = loaded

    effective_method = method or ("POST" if has_body_fields else "GET")
    normalized_route = _normalize_github_route(route)
    if effective_method not in _GITHUB_WRITE_METHODS:
        return (None, "", "")
    if paginate:
        return (
            None,
            "request_cardinality_unresolved",
            "mutation request count is indeterminate with --paginate",
        )

    comment_count, reason_code, reason = _comment_count_from_payload(payload)
    if reason:
        return (None, reason_code, reason)

    if graphql:
        query = payload.get("query")
        if query is None and not query_from_literal_input:
            for field in field_values:
                key, separator, value = field.partition("=")
                if separator and key == "query":
                    query = value
                    break
        if not isinstance(query, str) or (
            not query_from_literal_input and _is_dynamic_shell_value(query)
        ):
            return (None, "dynamic_target", "GraphQL mutation document is unresolved")
        if not re.search(r"\bmutation\b", query):
            return (None, "", "")
        kind = (
            GitHubMutationKind.GRAPHQL_REVIEW
            if any(
                re.search(rf"\b{re.escape(name)}\b", query) for name in _GRAPHQL_REVIEW_MUTATIONS
            )
            else GitHubMutationKind.OTHER
        )
    else:
        kind = _github_mutation_kind(normalized_route)

    return (
        GitHubMutationRecord(
            method=effective_method,
            route=normalized_route,
            kind=kind,
            request_count=1,
            review_comment_count=comment_count,
        ),
        "",
        "",
    )


_GH_HELP_FLAGS: frozenset[str] = frozenset({"--help", "-h"})
# Value-taking flags across the gh subcommands this module classifies below,
# curated so the --help exemption cannot be spoofed by a flag's own value
# (e.g. `gh pr review 5 --approve --body --help`) without needing a full
# per-subcommand flag grammar — mirrors the hardcoded-per-command flag
# tables already used by _analyze_gh_api/_analyze_curl_segment.
_GH_KNOWN_VALUE_FLAGS: frozenset[str] = frozenset(
    {
        "--body",
        "-b",
        "--body-file",
        "-F",
        "--add-label",
        "--remove-label",
        "--add-assignee",
        "--remove-assignee",
        "--add-project",
        "--remove-project",
        "--milestone",
        "-m",
        "--repo",
        "-R",
        "--title",
        "-t",
        "--reason",
        "--target",
        "--visibility",
    }
)

_GH_ISSUE_EDIT_LONG_VALUE_FLAGS: frozenset[str] = frozenset(
    {
        "--add-assignee",
        "--add-label",
        "--add-project",
        "--body",
        "--body-file",
        "--milestone",
        "--remove-assignee",
        "--remove-label",
        "--remove-project",
        "--repo",
        "--title",
    }
)
_GH_ISSUE_EDIT_SHORT_VALUE_FLAGS: frozenset[str] = frozenset({"-b", "-F", "-m", "-R", "-t"})
_GH_ISSUE_URL_RE = re.compile(r"^/[^/\s]+/[^/\s]+/issues/\d+/?$")


def _is_static_issue_edit_target(value: str) -> bool:
    if not value or _is_dynamic_shell_value(value):
        return False
    if value.isdecimal():
        return True
    parsed = urlsplit(value)
    return bool(
        parsed.scheme in {"http", "https"}
        and parsed.netloc
        and _GH_ISSUE_URL_RE.fullmatch(parsed.path)
    )


def _issue_edit_request_count(args: Sequence[str]) -> tuple[int | None, str, str]:
    targets = 0
    options_ended = False
    i = 0
    while i < len(args):
        token = args[i]
        if not options_ended and token == "--":
            options_ended = True
            i += 1
            continue
        if not options_ended:
            if (
                token in _GH_ISSUE_EDIT_LONG_VALUE_FLAGS
                or token in _GH_ISSUE_EDIT_SHORT_VALUE_FLAGS
            ):
                if i + 1 >= len(args):
                    return (
                        None,
                        "missing_required_value",
                        f"gh issue edit flag {token} is missing a value",
                    )
                i += 2
                continue
            if any(token.startswith(f"{flag}=") for flag in _GH_ISSUE_EDIT_LONG_VALUE_FLAGS):
                i += 1
                continue
            if any(
                token.startswith(flag) and token != flag
                for flag in _GH_ISSUE_EDIT_SHORT_VALUE_FLAGS
            ):
                i += 1
                continue
            if token.startswith("-"):
                return (
                    None,
                    "unsupported_grammar",
                    f"gh issue edit flag {token} is unresolved",
                )
        if not _is_static_issue_edit_target(token):
            return (None, "dynamic_target", "gh issue edit target is unresolved")
        targets += 1
        i += 1

    if targets == 0:
        return (None, "missing_required_value", "gh issue edit target is missing")
    return (targets, "", "")


_GH_MUTATION_SUBCOMMANDS: dict[str, frozenset[str]] = {
    row.partition(":")[0]: frozenset(row.partition(":")[2].split())
    for row in (
        "cache:delete;codespace:create delete edit rebuild stop;gist:create delete edit "
        "rename;gpg-key:add delete;issue:close comment create delete develop edit lock pin reopen "
        "transfer unlock unpin;label:clone create delete edit;pr:close comment edit lock merge "
        "ready reopen unlock;project:close copy create delete edit field-create field-delete "
        "item-add item-archive item-create item-delete item-edit link mark-template "
        "unlink;release:create delete delete-asset edit upload;repo:archive create delete edit "
        "fork rename sync unarchive;run:cancel delete rerun;secret:delete set;ssh-key:add "
        "delete;variable:delete set;workflow:disable enable run"
    ).split(";")
}
_GH_READ_ONLY_SUBCOMMANDS: dict[str, frozenset[str]] = {
    row.partition(":")[0]: frozenset(row.partition(":")[2].split())
    for row in (
        "cache:list;codespace:code cp jupyter list logs ports ssh view;gist:clone list "
        "view;gpg-key:list;issue:list status view;label:list;pr:checkout checks diff list status "
        "view;project:field-list item-list list view;release:download list verify verify-asset "
        "view;repo:clone list set-default view;run:download list view "
        "watch;secret:list;ssh-key:list;variable:list;workflow:list view"
    ).split(";")
}


def _gh_args_have_bare_help_flag(args: Sequence[str]) -> bool:
    """Return True when -h/--help appears as its own flag, not another flag's value."""
    i = 0
    n = len(args)
    while i < n:
        token = args[i]
        if token in _GH_HELP_FLAGS:
            return True
        if token in _GH_KNOWN_VALUE_FLAGS:
            i += 2
            continue
        i += 1
    return False


def _analyze_gh_segment(
    args: Sequence[str],
    *,
    cwd: str,
    input_context_safe: bool,
    resolved_redirect_targets: Sequence[str],
    file_redirect_count: int,
) -> tuple[GitHubMutationRecord | None, str, str]:
    if not args:
        return (None, "", "")
    if _gh_args_have_bare_help_flag(args[1:]):
        return (None, "", "")
    if args[:2] == ["pr", "create"]:
        return (None, "", "")
    if args[:2] == ["pr", "review"]:
        return (
            GitHubMutationRecord(
                method="POST",
                route="/gh/pr/review",
                kind=GitHubMutationKind.PULL_REVIEW,
                request_count=1,
                review_comment_count=None,
            ),
            "",
            "",
        )
    if args[:2] == ["issue", "edit"]:
        request_count, reason_code, reason = _issue_edit_request_count(args[2:])
        if request_count is None:
            return (None, reason_code, reason)
        return (
            GitHubMutationRecord(
                method="POST",
                route="/gh/issue/edit",
                kind=GitHubMutationKind.OTHER,
                request_count=request_count,
                review_comment_count=None,
            ),
            "",
            "",
        )
    noun = args[0]
    mutation_verbs = _GH_MUTATION_SUBCOMMANDS.get(noun)
    if mutation_verbs is not None and len(args) >= 2:
        verb = args[1]
        if verb in _GH_READ_ONLY_SUBCOMMANDS.get(noun, frozenset()):
            return (None, "", "")
        if verb not in mutation_verbs:
            return (
                None,
                "unsupported_grammar",
                f"gh {noun} {verb} mutation classification is unresolved",
            )
        return (
            GitHubMutationRecord(
                method="POST",
                route=f"/gh/{noun}/{verb}",
                kind=GitHubMutationKind.OTHER,
                request_count=1,
                review_comment_count=None,
            ),
            "",
            "",
        )
    if args[0] != "api":
        return (None, "", "")
    return _analyze_gh_api(
        args[1:],
        cwd=cwd,
        input_context_safe=input_context_safe,
        resolved_redirect_targets=resolved_redirect_targets,
        file_redirect_count=file_redirect_count,
    )


def _analyze_curl_segment(
    args: Sequence[str],
) -> tuple[list[GitHubMutationRecord], str, str]:
    method: str | None = None
    has_data = False
    force_get = False
    urls: list[str] = []
    saw_next = False
    i = 0
    data_flags = (
        ("--data", "-d"),
        ("--data-raw", None),
        ("--data-binary", None),
        ("--data-urlencode", None),
        ("--form", "-F"),
        ("--upload-file", "-T"),
    )
    value_flags = (("--header", "-H"), ("--user", "-u"), ("--output", "-o"))
    while i < len(args):
        token = args[i]
        value, next_i, matched = _flag_value(args, i, long_name="--request", short_name="-X")
        if matched or token in {"--request", "-X"}:
            if not matched or value is None:
                return ([], "missing_required_value", "curl method is missing")
            method = value.upper()
            i = next_i
            continue
        value, next_i, matched = _flag_value(args, i, long_name="--url")
        if matched or token == "--url":
            if not matched or value is None:
                return ([], "missing_required_value", "curl URL is missing")
            urls.append(value)
            i = next_i
            continue
        if token in {"-G", "--get"}:
            force_get = True
            i += 1
            continue
        if token == "--next":
            saw_next = True
            i += 1
            continue
        matched_value_flag = False
        for long_name, short_name in data_flags:
            value, next_i, matched = _flag_value(
                args,
                i,
                long_name=long_name,
                short_name=short_name,
            )
            if matched or token == long_name or (short_name is not None and token == short_name):
                if not matched or value is None:
                    return ([], "missing_required_value", f"{token} value is missing")
                has_data = True
                i = next_i
                matched_value_flag = True
                break
        if matched_value_flag:
            continue
        for long_name, short_name in value_flags:
            value, next_i, matched = _flag_value(
                args,
                i,
                long_name=long_name,
                short_name=short_name,
            )
            if matched or token == long_name or token == short_name:
                if not matched or value is None:
                    return ([], "missing_required_value", f"{token} value is missing")
                i = next_i
                matched_value_flag = True
                break
        if matched_value_flag:
            continue
        if token.startswith("-"):
            i += 1
            continue
        urls.append(token)
        i += 1

    if method is not None and _is_dynamic_shell_value(method):
        return ([], "dynamic_target", "curl method is dynamic")
    if any(_is_dynamic_shell_value(url) for url in urls):
        return ([], "dynamic_target", "curl URL is dynamic")
    github_urls = []
    for url in urls:
        hostname = urlsplit(url).hostname
        if hostname is not None and hostname.lower() in {"api.github.com", "github.com"}:
            github_urls.append(url)
    if not github_urls:
        return ([], "", "")
    effective_method = method or ("GET" if force_get else ("POST" if has_data else "GET"))
    if effective_method not in _GITHUB_WRITE_METHODS:
        return ([], "", "")
    if saw_next or len(github_urls) != 1 or len(urls) != 1:
        return (
            [],
            "request_cardinality_unresolved",
            "curl mutation request count is indeterminate",
        )
    route = urlsplit(github_urls[0]).path or "/"
    return (
        [
            GitHubMutationRecord(
                method=effective_method,
                route=route,
                kind=_github_mutation_kind(route),
                request_count=1,
                review_comment_count=None,
            )
        ],
        "",
        "",
    )


def _segment_cwd(segment: Sequence[str], cwd: str) -> str:
    current = cwd
    for index, token in enumerate(segment):
        if token in {"-C", "--chdir"} and index and segment[index - 1] == "env":
            if index + 1 < len(segment):
                value = segment[index + 1]
                if os.path.isabs(value):
                    current = value
                elif current:
                    current = os.path.normpath(os.path.join(current, value))
        elif token.startswith("--chdir=") and "env" in segment[:index]:
            value = token.split("=", 1)[1]
            if os.path.isabs(value):
                current = value
            elif current:
                current = os.path.normpath(os.path.join(current, value))
    return current


def _analyze_github_segment(
    segment: Sequence[str],
    *,
    cwd: str,
    input_context_safe: bool = True,
    resolved_redirect_targets: Sequence[str] = (),
    file_redirect_count: int = 0,
) -> tuple[list[GitHubMutationRecord], str, str]:
    verb, args = _command_verb_and_args(list(segment))
    executable = _normalize_executable_call(verb)
    if executable == "gh":
        record, reason_code, reason = _analyze_gh_segment(
            args,
            cwd=_segment_cwd(segment, cwd),
            input_context_safe=input_context_safe,
            resolved_redirect_targets=resolved_redirect_targets,
            file_redirect_count=file_redirect_count,
        )
        return (([record] if record is not None else []), reason_code, reason)
    if executable == "curl":
        return _analyze_curl_segment(args)
    return ([], "", "")


def analyze_github_mutations(
    command: str,
    *,
    cwd: str = "",
) -> GitHubMutationAnalysis:
    """Classify all reachable mutations, treating uncertainty as absorbing."""
    if not isinstance(command, str) or not command.strip():
        return _none_github_analysis()

    records: list[GitHubMutationRecord] = []
    reasons: list[tuple[str, str]] = []
    queue: list[tuple[str, str, int, bool, tuple[str, ...], int]] = [
        (command, cwd, 0, True, (), 0)
    ]
    argv_payloads: list[tuple[list[str], str, bool, tuple[str, ...], int]] = []

    while queue:
        (
            payload,
            payload_cwd,
            depth,
            inherited_input_safe,
            outer_redirect_targets,
            outer_file_redirect_count,
        ) = queue.pop(0)
        if depth >= 32:
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
                    raw_segment,
                    cwd=current_cwd,
                    redirect_syntax=command_segment.redirect_syntax,
                )
            )
            active_redirect_targets = outer_redirect_targets + tuple(redirect_targets)
            active_file_redirect_count = outer_file_redirect_count + file_redirect_count
            segment_cwd = _segment_cwd(executable_tokens, current_cwd)
            nested_contexts.append(
                (
                    raw_segment,
                    segment_cwd,
                    input_context_safe,
                    active_redirect_targets,
                    active_file_redirect_count,
                )
            )
            verb, args = _command_verb_and_args(executable_tokens)
            if _normalize_executable_call(verb) == "cd":
                input_context_safe = input_context_safe and file_redirect_count == 0
                if len(args) != 1 or _is_dynamic_shell_value(args[0]):
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
                resolved_redirect_targets=active_redirect_targets,
                file_redirect_count=active_file_redirect_count,
            )
            records.extend(found)
            if reason:
                reasons.append((reason_code, reason))

            interpreter_specs, has_unresolved = _extract_interpreter_segment_specs_call(
                executable_tokens
            )
            if has_unresolved and _POSSIBLE_GITHUB_EXEC_RE.search(payload):
                reasons.append(
                    (
                        "interpreter_structure_unresolved",
                        "interpreter subprocess command or cwd is unresolved",
                    )
                )
            for spec in interpreter_specs:
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
                            active_redirect_targets,
                            active_file_redirect_count,
                        )
                    )
                else:
                    argv_payloads.append(
                        (
                            spec.payload,
                            interpreter_cwd,
                            input_context_safe,
                            active_redirect_targets,
                            active_file_redirect_count,
                        )
                    )

            input_context_safe = (
                input_context_safe
                and file_redirect_count == 0
                and _segment_is_safe_before_literal_input(executable_tokens)
            )

        remaining_nested_contexts = list(nested_contexts)
        for nested in _extract_shell_command_payloads_call(payload):
            matching_index = next(
                (
                    index
                    for index, context in enumerate(remaining_nested_contexts)
                    if _segment_evaluates_shell_payload_call(context[0], nested)
                ),
                None,
            )
            matching_context: tuple[list[str], str, bool, tuple[str, ...], int]
            if matching_index is None:
                matching_context = (
                    [],
                    payload_cwd,
                    inherited_input_safe,
                    outer_redirect_targets,
                    outer_file_redirect_count,
                )
            else:
                matching_context = remaining_nested_contexts.pop(matching_index)
            _, nested_cwd, nested_input_safe, nested_targets, nested_count = matching_context
            queue.append(
                (
                    nested,
                    nested_cwd,
                    depth + 1,
                    nested_input_safe,
                    nested_targets,
                    nested_count,
                )
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

    for (
        argv,
        argv_cwd,
        input_context_safe,
        inherited_redirect_targets,
        redirect_count,
    ) in argv_payloads:
        found, reason_code, reason = _analyze_github_segment(
            argv,
            cwd=argv_cwd,
            input_context_safe=input_context_safe,
            resolved_redirect_targets=inherited_redirect_targets,
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
            status=GitHubMutationStatus.MULTIPLE,
            mutations=tuple(records),
            request_count=request_count,
            review_comment_count=None,
            reason_code="",
            reason="",
        )
    record = records[0]
    return GitHubMutationAnalysis(
        status=GitHubMutationStatus.SINGLE_RESOLVED,
        mutations=(record,),
        request_count=1,
        review_comment_count=record.review_comment_count,
        reason_code="",
        reason="",
    )
