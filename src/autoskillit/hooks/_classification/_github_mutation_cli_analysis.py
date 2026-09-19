"""GitHub CLI and curl parsing for mutation analysis."""

from __future__ import annotations

import csv
import re
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

if TYPE_CHECKING:
    from autoskillit.hooks._classification._github_mutation_request_analysis import (
        _GITHUB_WRITE_METHODS,
        GitHubMutationKind,
        GitHubMutationRecord,
        _analyze_gh_api,
        _github_mutation_kind,
        _is_dynamic_shell_value,
    )
    from autoskillit.hooks._runtime._command_classification import (
        ArgvToken,
        _consume_argv_flag,
        _FlagArity,
    )
else:
    if __package__ == "autoskillit.hooks._classification":
        from .._runtime._command_classification import ArgvToken, _consume_argv_flag, _FlagArity
    else:
        from _command_classification import ArgvToken, _consume_argv_flag, _FlagArity
    from ._github_mutation_request_analysis import (
        _GITHUB_WRITE_METHODS,
        GitHubMutationKind,
        GitHubMutationRecord,
        _analyze_gh_api,
        _github_mutation_kind,
        _is_dynamic_shell_value,
    )


_GH_HELP_FLAGS: frozenset[str] = frozenset({"--help", "-h"})
_GH_ISSUE_EDIT_FLAG_SPEC: Mapping[str, _FlagArity] = {
    **{
        flag: _FlagArity.VALUE
        for flag in (
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
            "-b",
            "-F",
            "-m",
            "-R",
            "-t",
            "--add-sub-issue",
            "--remove-sub-issue",
            "--add-blocked-by",
            "--remove-blocked-by",
            "--add-blocking",
            "--remove-blocking",
            "--parent",
            "--type",
        )
    },
    "--remove-milestone": _FlagArity.BOOLEAN,
    "--remove-parent": _FlagArity.BOOLEAN,
    "--remove-type": _FlagArity.BOOLEAN,
}
_GH_ISSUE_EDIT_REFERENCE_LIST_FLAGS: frozenset[str] = frozenset(
    {
        "--add-sub-issue",
        "--remove-sub-issue",
        "--add-blocked-by",
        "--remove-blocked-by",
        "--add-blocking",
        "--remove-blocking",
    }
)
_GH_ISSUE_EDIT_REFERENCE_FLAGS: frozenset[str] = _GH_ISSUE_EDIT_REFERENCE_LIST_FLAGS | {"--parent"}
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
    | {flag for flag, arity in _GH_ISSUE_EDIT_FLAG_SPEC.items() if arity == _FlagArity.VALUE}
)
_GH_ISSUE_URL_RE = re.compile(r"^/[^/\s]+/[^/\s]+/issues/\d+/?$")


def _is_static_issue_edit_reference_text(value: str) -> bool:
    if value.isdecimal():
        return True
    parsed = urlsplit(value)
    return bool(
        parsed.scheme in {"http", "https"}
        and parsed.netloc
        and _GH_ISSUE_URL_RE.fullmatch(parsed.path)
    )


def _is_static_issue_edit_target(value: ArgvToken) -> bool:
    return bool(
        value.text
        and not _is_dynamic_shell_value(value)
        and _is_static_issue_edit_reference_text(value.text)
    )


def _is_static_issue_edit_reference_list(value: ArgvToken) -> bool:
    if not value.text or _is_dynamic_shell_value(value):
        return False
    try:
        references = next(csv.reader([value.text], strict=True))
    except csv.Error:
        return False
    return bool(references) and all(
        reference and _is_static_issue_edit_reference_text(reference) for reference in references
    )


def _is_static_issue_edit_flag_reference(flag: str, value: ArgvToken) -> bool:
    if flag in _GH_ISSUE_EDIT_REFERENCE_LIST_FLAGS:
        return _is_static_issue_edit_reference_list(value)
    return _is_static_issue_edit_target(value)


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
            value, next_i, recognized = _consume_argv_flag(args, i, _GH_ISSUE_EDIT_FLAG_SPEC)
            if recognized:
                flag = token.text.partition("=")[0]
                if flag not in _GH_ISSUE_EDIT_FLAG_SPEC and len(token.text) > 2:
                    flag = token.text[:2]
                if _GH_ISSUE_EDIT_FLAG_SPEC[flag] == _FlagArity.VALUE and (
                    value is None
                    or not value.text
                    or value.text == "--"
                    or value.text.startswith("-")
                ):
                    return (
                        None,
                        "missing_required_value",
                        f"gh issue edit flag {flag} is missing a value",
                    )
                if (
                    flag in _GH_ISSUE_EDIT_REFERENCE_FLAGS
                    and value is not None
                    and not _is_static_issue_edit_flag_reference(flag, value)
                ):
                    return (
                        None,
                        "dynamic_target",
                        f"gh issue edit flag {flag} has an unresolved reference",
                    )
                i = next_i
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
        "watch;search:code commits issues prs repos;secret:list;ssh-key:list;variable:list;"
        "workflow:list view"
    ).split(";")
}


def _gh_args_have_bare_help_flag(args: Sequence[str]) -> bool:
    i = 0
    while i < len(args):
        token = args[i]
        if token in _GH_HELP_FLAGS:
            previous = args[i - 1] if i else None
            if (
                previous is not None
                and previous.startswith("-")
                and previous not in _GH_HELP_FLAGS
                and previous not in _GH_KNOWN_VALUE_FLAGS
            ):
                i += 1
                continue
            return True
        i += 2 if token in _GH_KNOWN_VALUE_FLAGS else 1
    return False


def _analyze_gh_segment(
    args: Sequence[str],
    *,
    argv_args: Sequence[ArgvToken],
    cwd: str,
    input_context_safe: bool,
    resolved_redirect_targets: Sequence[str],
    file_redirect_count: int,
) -> tuple[GitHubMutationRecord | None, str, str, bool]:
    if not args or _gh_args_have_bare_help_flag(args):
        return (None, "", "", True)
    noun = args[0]
    verb = args[1] if len(args) >= 2 else None
    if noun == "pr" and verb == "create":
        return (None, "", "", False)
    if noun == "pr" and verb == "review":
        return (
            GitHubMutationRecord("POST", "/gh/pr/review", GitHubMutationKind.PULL_REVIEW, 1, None),
            "",
            "",
            False,
        )
    if noun == "issue" and verb == "edit":
        request_count, reason_code, reason = _issue_edit_request_count(argv_args[2:])
        if request_count is None:
            return (None, reason_code, reason, False)
        return (
            GitHubMutationRecord(
                "POST", "/gh/issue/edit", GitHubMutationKind.OTHER, request_count, None
            ),
            "",
            "",
            False,
        )
    read_verbs = _GH_READ_ONLY_SUBCOMMANDS.get(noun, frozenset())
    if verb in read_verbs:
        return (None, "", "", True)
    if noun == "search":
        selector = verb if verb is not None else "<missing>"
        return (
            None,
            "unsupported_grammar",
            f"gh search {selector} classification is unresolved",
            False,
        )
    mutation_verbs = _GH_MUTATION_SUBCOMMANDS.get(noun)
    if mutation_verbs is not None and verb is not None:
        if verb not in mutation_verbs:
            return (
                None,
                "unsupported_grammar",
                f"gh {noun} {verb} mutation classification is unresolved",
                False,
            )
        return (
            GitHubMutationRecord("POST", f"/gh/{noun}/{verb}", GitHubMutationKind.OTHER, 1, None),
            "",
            "",
            False,
        )
    if noun != "api":
        return (None, "", "", False)
    return _analyze_gh_api(
        argv_args[1:],
        cwd=cwd,
        input_context_safe=input_context_safe,
        resolved_redirect_targets=resolved_redirect_targets,
        file_redirect_count=file_redirect_count,
    )


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
) -> tuple[list[GitHubMutationRecord], str, str, bool]:
    method: ArgvToken | None = None
    has_data = force_get = saw_next = False
    urls: list[ArgvToken] = []
    i = 0
    data_flags = {
        "--data",
        "-d",
        "--data-raw",
        "--data-binary",
        "--data-urlencode",
        "--form",
        "-F",
        "--upload-file",
        "-T",
    }
    while i < len(args):
        token = args[i]
        if token.text.startswith("-"):
            value, next_i, recognized = _consume_argv_flag(args, i, _CURL_FLAG_SPEC)
            if not recognized:
                return (
                    [],
                    "unrecognized_curl_flag",
                    f"unrecognized curl flag: {token.text!r}",
                    False,
                )
            flag = token.text
            if flag not in _CURL_FLAG_SPEC:
                flag = flag.partition("=")[0] if flag.startswith("--") else flag[:2]
            if value is None and _CURL_FLAG_SPEC[flag] == _FlagArity.VALUE:
                if flag in {"--request", "-X"}:
                    return ([], "missing_required_value", "curl method is missing", False)
                if flag == "--url":
                    return ([], "missing_required_value", "curl URL is missing", False)
                return ([], "missing_required_value", f"{token.text} value is missing", False)
            if flag in {"--request", "-X"}:
                method = value
            elif flag == "--url":
                assert value is not None
                urls.append(value)
            elif flag in data_flags:
                has_data = True
            elif flag in {"-G", "--get"}:
                force_get = True
            elif flag == "--next":
                saw_next = True
            i = next_i
            continue
        urls.append(token)
        i += 1
    if method is not None and _is_dynamic_shell_value(method):
        return ([], "dynamic_target", "curl method is dynamic", False)
    if any(_is_dynamic_shell_value(url) for url in urls):
        return ([], "dynamic_target", "curl URL is dynamic", False)
    github_urls = [
        url
        for url in urls
        if (hostname := urlsplit(url.text).hostname) is not None
        and hostname.lower() in {"api.github.com", "github.com"}
    ]
    if not github_urls:
        return ([], "", "", True)
    effective_method = (
        method.text.upper()
        if method is not None and method.text
        else ("GET" if force_get else ("POST" if has_data else "GET"))
    )
    if effective_method not in _GITHUB_WRITE_METHODS:
        return ([], "", "", True)
    if saw_next or len(github_urls) != 1 or len(urls) != 1:
        return (
            [],
            "request_cardinality_unresolved",
            "curl mutation request count is indeterminate",
            False,
        )
    route = urlsplit(github_urls[0].text).path or "/"
    return (
        [GitHubMutationRecord(effective_method, route, _github_mutation_kind(route), 1, None)],
        "",
        "",
        False,
    )
