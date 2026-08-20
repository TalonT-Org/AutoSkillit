"""GitHub-mutation analysis extracted from _command_classification.

This module is the consumer of command-segment helpers defined in
_command_classification (verb extraction, tokenizer, executable
normalization, redirect partitioning, interpreter-spec extraction, and
two shell-payload helpers). Most of these are imported lazily inside a thin
wrapper to defer imports past the module-load boundary — the bare-name
_command_classification reference resolves once the sibling module is
fully populated. The bare-name form is also required to satisfy the
hook-script stdlib-only contract enforced by test_hooks_are_stdlib_only
(REQ-AST-001).

The quote-provenance/flag-arity primitives this module's own gh-api/curl
analysis needs (ArgvToken, _FlagArity, _consume_argv_flag, and their
helpers) are imported once at module scope instead, using the same
TYPE_CHECKING/bare-name split hooks/guards/github_mutation_guard.py already
uses for its own cross-hooks-file import of this module: they are used
pervasively as type annotations and constructors throughout this file
(every _analyze_gh_api/_analyze_curl_segment call site, the GraphQL
provenance wrapping), so threading them through a per-call lazy wrapper —
workable for the handful of simple delegating calls above — would not
scale, and a plain sibling-module bare-name import carries no runtime cost
either style of import doesn't already pay.
"""

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
    from autoskillit.hooks._command_classification import (
        ArgvToken,
        _argv_token_after_prefix,
        _argv_token_value_after_key,
        _consume_argv_flag,
        _FlagArity,
        _select_executable_argv_tokens,
        _verb_start_index,
    )
else:
    from _command_classification import (  # noqa: E402
        ArgvToken,
        _argv_token_after_prefix,
        _argv_token_value_after_key,
        _consume_argv_flag,
        _FlagArity,
        _select_executable_argv_tokens,
        _verb_start_index,
    )


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


def _is_dynamic_shell_value(token: ArgvToken) -> bool:
    """A token is dynamic unless it was fully single-quoted end-to-end --

    a fact provable from the raw command text independent of which
    characters the (already-dequoted) token contains -- or its content is
    otherwise proven inert by its own provenance (see ArgvToken).
    """
    return not token.fully_single_quoted and bool(_DYNAMIC_SHELL_TOKEN_RE.search(token.text))


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
    value: ArgvToken,
    *,
    cwd: str,
) -> tuple[dict[str, Any] | None, str, str]:
    if value.text == "-":
        return (None, "unsafe_input_provenance", "GitHub --input stdin is unresolved")
    if not value.text or _is_dynamic_shell_value(value):
        return (None, "dynamic_target", "GitHub --input path is dynamic")
    if os.path.isabs(value.text):
        path = os.path.normpath(value.text)
    else:
        if not cwd or not os.path.isabs(cwd):
            return (
                None,
                "cwd_unresolved",
                "relative GitHub --input requires an absolute cwd",
            )
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
    args: Sequence[ArgvToken],
    index: int,
    *,
    long_name: str,
    short_name: str | None = None,
) -> tuple[ArgvToken | None, int, bool]:
    token = args[index]
    if token.text == long_name or (short_name is not None and token.text == short_name):
        if index + 1 >= len(args):
            return (None, index + 1, False)
        # Space-form: the value is the next token in full -- return its own
        # ArgvToken unmodified, full provenance already correct.
        return (args[index + 1], index + 2, True)
    if token.text.startswith(f"{long_name}="):
        # `=`-form: the value is a substring of this token, after the known
        # `long_name=` prefix -- see _argv_token_after_prefix for how
        # provenance is re-derived rather than inherited wholesale (a
        # `--flag=` prefix outside any quotes does not disqualify a
        # separately-quoted value, e.g. `--jq='.id'`).
        value_text = token.text.split("=", 1)[1]
        return (
            _argv_token_after_prefix(token, f"{long_name}=", value_text),
            index + 1,
            True,
        )
    if short_name and token.text.startswith(short_name) and token.text != short_name:
        # Bundled-short-form: same prefix-aware provenance re-derivation.
        value_text = token.text[len(short_name) :]
        return (
            _argv_token_after_prefix(token, short_name, value_text),
            index + 1,
            True,
        )
    return (None, index, False)


# Complete `gh api` flag allowlist, verified against a live `gh api --help`
# read (see tests/hooks/test_gh_api_flag_spec_contract.py, which fails the
# suite if a future `gh` CLI adds a value-taking flag not listed here).
# Recognizing every flag -- not just the ones _analyze_gh_api special-cases
# for route/method extraction -- lets an unrecognized flag be denied with
# its own distinguishable reason code instead of silently misparsed as a
# second route (see _consume_argv_flag).
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
) -> tuple[GitHubMutationRecord | None, str, str]:
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
            # Hardcoded literal, never shell-parsed -- provably inert by
            # construction, not "true because we said so": no argv span
            # exists for it to be dynamic through.
            route = ArgvToken("/graphql", True, "'/graphql'")
            i += 1
            continue

        value, next_i, matched = _flag_value(args, i, long_name="--method", short_name="-X")
        if matched or token.text in {"--method", "-X"}:
            if not matched or value is None:
                return (None, "missing_required_value", "GitHub API method is missing")
            method = value
            i = next_i
            continue

        value, next_i, matched = _flag_value(args, i, long_name="--input")
        if matched or token.text == "--input":
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
            if matched or token.text in {long_name, short_name}:
                if not matched or value is None:
                    return (None, "missing_required_value", f"{long_name} value is missing")
                field_values.append(value)
                has_body_fields = True
                i = next_i
                field_match = True
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
                )
            # _consume_argv_flag returns (None, i + 1, True) for both BOOLEAN
            # arity and VALUE arity with a missing next token -- distinguish
            # them here so a stray `-H` (no value) still surfaces as
            # `missing_required_value` rather than silently passing.
            if value is None and _GH_API_FLAG_SPEC.get(token.text) == _FlagArity.VALUE:
                return (
                    None,
                    "missing_required_value",
                    f"{token.text} value is missing",
                )
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
            os.path.normpath(input_value.text)
            if os.path.isabs(input_value.text)
            else os.path.normpath(os.path.join(cwd, input_value.text))
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

    effective_method = (
        method.text.upper()
        if method is not None and method.text
        else ("POST" if has_body_fields else "GET")
    )
    normalized_route = _normalize_github_route(route.text)
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
        query: ArgvToken | None = None
        raw_query = payload.get("query")
        if isinstance(raw_query, str):
            # File-content value (from --input): never shell-parsed, so
            # quote provenance doesn't apply here -- query_from_literal_input
            # alone is what proves this branch safe (see the skip condition
            # below). The wrapper exists only to keep `query` uniformly
            # typed across both assignment branches.
            query = ArgvToken(raw_query, False, raw_query)
        if query is None and not query_from_literal_input:
            for field in field_values:
                field_key, field_separator, _field_value_text = field.text.partition("=")
                if field_separator and field_key == "query":
                    # gh's `-f`/`-F key=value` syntax: `key` is a bareword,
                    # never itself quoted, so a quoted value (the common
                    # `-f query='...'` shape) must be re-derived from its
                    # own raw span rather than inheriting `field`'s coarser
                    # whole-token flag -- see _argv_token_value_after_key.
                    query = _argv_token_value_after_key(field, field_key)
                    break
        if query is None or (not query_from_literal_input and _is_dynamic_shell_value(query)):
            return (None, "dynamic_target", "GraphQL mutation document is unresolved")
        if not re.search(r"\bmutation\b", query.text):
            return (None, "", "")
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


def _is_static_issue_edit_target(value: ArgvToken) -> bool:
    if not value.text or _is_dynamic_shell_value(value):
        return False
    if value.text.isdecimal():
        return True
    parsed = urlsplit(value.text)
    return bool(
        parsed.scheme in {"http", "https"}
        and parsed.netloc
        and _GH_ISSUE_URL_RE.fullmatch(parsed.path)
    )


def _issue_edit_request_count(args: Sequence[ArgvToken]) -> tuple[int | None, str, str]:
    targets = 0
    options_ended = False
    i = 0
    while i < len(args):
        token = args[i]
        if not options_ended and token.text == "--":
            options_ended = True
            i += 1
            continue
        if not options_ended:
            if (
                token.text in _GH_ISSUE_EDIT_LONG_VALUE_FLAGS
                or token.text in _GH_ISSUE_EDIT_SHORT_VALUE_FLAGS
            ):
                if i + 1 >= len(args):
                    return (
                        None,
                        "missing_required_value",
                        f"gh issue edit flag {token.text} is missing a value",
                    )
                i += 2
                continue
            if any(token.text.startswith(f"{flag}=") for flag in _GH_ISSUE_EDIT_LONG_VALUE_FLAGS):
                i += 1
                continue
            if any(
                token.text.startswith(flag) and token.text != flag
                for flag in _GH_ISSUE_EDIT_SHORT_VALUE_FLAGS
            ):
                i += 1
                continue
            if token.text.startswith("-"):
                return (
                    None,
                    "unsupported_grammar",
                    f"gh issue edit flag {token.text} is unresolved",
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
    """Return True when -h/--help appears as its own flag, not another flag's value.

    A --help/-h token immediately following an unrecognized `-`-prefixed
    flag (one that is neither a curated known-value flag nor --help/-h
    itself) cannot be trusted as a bare flag -- an unrecognized flag's own
    arity is unknown, so this token might actually be *its* value instead
    (e.g. `gh release create v1 --notes '--help'`, where --notes is not in
    _GH_KNOWN_VALUE_FLAGS). That occurrence is skipped rather than trusted.

    This is deliberately narrower than defaulting every unrecognized flag
    to value-taking (advance 2): doing so would also mis-skip a *known*
    value-taking flag immediately following an unrecognized boolean one --
    e.g. `gh pr review 5 --approve --body --help` (--approve is boolean,
    unrecognized here; --body is a real _GH_KNOWN_VALUE_FLAGS entry whose
    own value is the literal review-body text "--help") -- a blanket
    advance-2 default would jump from --approve straight past --body
    without ever separately recognizing it, corrupting the scan and
    wrongly exempting a genuine review mutation. Scoping the fix to only
    the --help occurrence itself keeps --body's own, already-correct
    2-token skip intact.
    """
    i = 0
    n = len(args)
    while i < n:
        token = args[i]
        if token in _GH_HELP_FLAGS:
            previous = args[i - 1] if i > 0 else None
            if (
                previous is not None
                and previous.startswith("-")
                and previous not in _GH_HELP_FLAGS
                and previous not in _GH_KNOWN_VALUE_FLAGS
            ):
                i += 1
                continue
            return True
        if token in _GH_KNOWN_VALUE_FLAGS:
            i += 2
            continue
        i += 1
    return False


def _analyze_gh_segment(
    args: Sequence[str],
    *,
    argv_args: Sequence[ArgvToken],
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
        request_count, reason_code, reason = _issue_edit_request_count(argv_args[2:])
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
        argv_args[1:],
        cwd=cwd,
        input_context_safe=input_context_safe,
        resolved_redirect_targets=resolved_redirect_targets,
        file_redirect_count=file_redirect_count,
    )


# curl flag spec covering every flag _analyze_curl_segment already
# special-cased, plus the flags named by this rectify's investigation
# (-A/--user-agent, -b/--cookie, -c/--cookie-jar, -x/--proxy, -w/--write-out,
# -m/--max-time, --cacert, --cert/-E, --key, --connect-timeout, --retry,
# --resolve) and a further set of curl's most common boolean flags, verified
# against a live `curl --help all` read. curl has hundreds of flags in
# total; this is not exhaustive -- an unrecognized flag now fails closed
# (see _analyze_curl_segment's catch-all) rather than being silently
# misparsed, so this list trades some availability for the flags it omits
# in exchange for closing the overblock-by-misparse bug shape.
_CURL_FLAG_SPEC: Mapping[str, _FlagArity] = {
    "-X": _FlagArity.VALUE,
    "--request": _FlagArity.VALUE,
    "--url": _FlagArity.VALUE,
    "-d": _FlagArity.VALUE,
    "--data": _FlagArity.VALUE,
    "--data-raw": _FlagArity.VALUE,
    "--data-binary": _FlagArity.VALUE,
    "--data-urlencode": _FlagArity.VALUE,
    "-F": _FlagArity.VALUE,
    "--form": _FlagArity.VALUE,
    "-T": _FlagArity.VALUE,
    "--upload-file": _FlagArity.VALUE,
    "-H": _FlagArity.VALUE,
    "--header": _FlagArity.VALUE,
    "-u": _FlagArity.VALUE,
    "--user": _FlagArity.VALUE,
    "-o": _FlagArity.VALUE,
    "--output": _FlagArity.VALUE,
    "-A": _FlagArity.VALUE,
    "--user-agent": _FlagArity.VALUE,
    "-b": _FlagArity.VALUE,
    "--cookie": _FlagArity.VALUE,
    "-c": _FlagArity.VALUE,
    "--cookie-jar": _FlagArity.VALUE,
    "-x": _FlagArity.VALUE,
    "--proxy": _FlagArity.VALUE,
    "-w": _FlagArity.VALUE,
    "--write-out": _FlagArity.VALUE,
    "-m": _FlagArity.VALUE,
    "--max-time": _FlagArity.VALUE,
    "--cacert": _FlagArity.VALUE,
    "-E": _FlagArity.VALUE,
    "--cert": _FlagArity.VALUE,
    "--key": _FlagArity.VALUE,
    "--connect-timeout": _FlagArity.VALUE,
    "--retry": _FlagArity.VALUE,
    "--resolve": _FlagArity.VALUE,
    "-G": _FlagArity.BOOLEAN,
    "--get": _FlagArity.BOOLEAN,
    "--next": _FlagArity.BOOLEAN,
    "-k": _FlagArity.BOOLEAN,
    "--insecure": _FlagArity.BOOLEAN,
    "-L": _FlagArity.BOOLEAN,
    "--location": _FlagArity.BOOLEAN,
    "-s": _FlagArity.BOOLEAN,
    "--silent": _FlagArity.BOOLEAN,
    "-S": _FlagArity.BOOLEAN,
    "--show-error": _FlagArity.BOOLEAN,
    "-v": _FlagArity.BOOLEAN,
    "--verbose": _FlagArity.BOOLEAN,
    "-i": _FlagArity.BOOLEAN,
    "--include": _FlagArity.BOOLEAN,
    "--compressed": _FlagArity.BOOLEAN,
    "-0": _FlagArity.BOOLEAN,
    "--http1.0": _FlagArity.BOOLEAN,
    "--http1.1": _FlagArity.BOOLEAN,
    "--http2": _FlagArity.BOOLEAN,
    "-4": _FlagArity.BOOLEAN,
    "--ipv4": _FlagArity.BOOLEAN,
    "-6": _FlagArity.BOOLEAN,
    "--ipv6": _FlagArity.BOOLEAN,
    "-f": _FlagArity.BOOLEAN,
    "--fail": _FlagArity.BOOLEAN,
    "-g": _FlagArity.BOOLEAN,
    "--globoff": _FlagArity.BOOLEAN,
}


def _analyze_curl_segment(
    args: Sequence[ArgvToken],
) -> tuple[list[GitHubMutationRecord], str, str]:
    method: ArgvToken | None = None
    has_data = False
    force_get = False
    urls: list[ArgvToken] = []
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
        if matched or token.text in {"--request", "-X"}:
            if not matched or value is None:
                return ([], "missing_required_value", "curl method is missing")
            method = value
            i = next_i
            continue
        value, next_i, matched = _flag_value(args, i, long_name="--url")
        if matched or token.text == "--url":
            if not matched or value is None:
                return ([], "missing_required_value", "curl URL is missing")
            urls.append(value)
            i = next_i
            continue
        if token.text in {"-G", "--get"}:
            force_get = True
            i += 1
            continue
        if token.text == "--next":
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
            if (
                matched
                or token.text == long_name
                or (short_name is not None and token.text == short_name)
            ):
                if not matched or value is None:
                    return ([], "missing_required_value", f"{token.text} value is missing")
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
            if matched or token.text == long_name or token.text == short_name:
                if not matched or value is None:
                    return ([], "missing_required_value", f"{token.text} value is missing")
                i = next_i
                matched_value_flag = True
                break
        if matched_value_flag:
            continue
        if token.text.startswith("-"):
            value, next_i, recognized = _consume_argv_flag(args, i, _CURL_FLAG_SPEC)
            if not recognized:
                return (
                    [],
                    "unrecognized_curl_flag",
                    f"unrecognized curl flag: {token.text!r}",
                )
            # _consume_argv_flag returns (None, i + 1, True) for both BOOLEAN
            # arity and VALUE arity with a missing next token -- distinguish
            # them here so a stray `-A`/`--proxy` (no value) surfaces as
            # `missing_required_value` rather than silently passing.
            if value is None and _CURL_FLAG_SPEC.get(token.text) == _FlagArity.VALUE:
                return (
                    [],
                    "missing_required_value",
                    f"{token.text} value is missing",
                )
            i = next_i
            continue
        urls.append(token)
        i += 1

    if method is not None and _is_dynamic_shell_value(method):
        return ([], "dynamic_target", "curl method is dynamic")
    if any(_is_dynamic_shell_value(url) for url in urls):
        return ([], "dynamic_target", "curl URL is dynamic")
    github_urls = []
    for url in urls:
        hostname = urlsplit(url.text).hostname
        if hostname is not None and hostname.lower() in {"api.github.com", "github.com"}:
            github_urls.append(url)
    if not github_urls:
        return ([], "", "")
    effective_method = (
        method.text.upper()
        if method is not None and method.text
        else ("GET" if force_get else ("POST" if has_data else "GET"))
    )
    if effective_method not in _GITHUB_WRITE_METHODS:
        return ([], "", "")
    if saw_next or len(github_urls) != 1 or len(urls) != 1:
        return (
            [],
            "request_cardinality_unresolved",
            "curl mutation request count is indeterminate",
        )
    route = urlsplit(github_urls[0].text).path or "/"
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
    argv_tokens: Sequence[ArgvToken] | None = None,
) -> tuple[list[GitHubMutationRecord], str, str]:
    verb, args = _command_verb_and_args(list(segment))
    executable = _normalize_executable_call(verb)
    if argv_tokens is None:
        # Argv-payload segments (e.g. a parsed `subprocess.run([...])` list)
        # are Python literal tokens that never passed through shell parsing
        # at all -- no shell metacharacter interpretation is possible, so
        # they are provably inert by construction, not a fabricated default.
        argv_tokens = [ArgvToken(t, True, t) for t in segment]
    argv_args: list[ArgvToken] = []
    start = _verb_start_index(list(segment))
    if start is not None:
        argv_args = list(argv_tokens[start + 1 :])
    if executable == "gh":
        record, reason_code, reason = _analyze_gh_segment(
            args,
            argv_args=argv_args,
            cwd=_segment_cwd(segment, cwd),
            input_context_safe=input_context_safe,
            resolved_redirect_targets=resolved_redirect_targets,
            file_redirect_count=file_redirect_count,
        )
        return (([record] if record is not None else []), reason_code, reason)
    if executable == "curl":
        return _analyze_curl_segment(argv_args)
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
                    raw_segment,
                    cwd=current_cwd,
                    redirect_syntax=command_segment.redirect_syntax,
                )
            )
            executable_argv_tokens = _select_executable_argv_tokens(
                raw_segment,
                command_segment.argv_tokens,
                cwd=current_cwd,
                redirect_syntax=command_segment.redirect_syntax,
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
                # _verb_start_index has been determined to be non-None by
                # command_verb_and_args above (same list, same algorithm), so
                # this slice is always taken from the verb+1 position.
                cd_start = _verb_start_index(executable_tokens)
                assert cd_start is not None  # implied by verb == "cd" above
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
                resolved_redirect_targets=active_redirect_targets,
                file_redirect_count=active_file_redirect_count,
                argv_tokens=executable_argv_tokens,
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
