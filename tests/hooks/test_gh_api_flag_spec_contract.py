"""Live-CLI contract tests for this module's four CLI flag-arity spec tables.

Each test shells out to a CLI's `--help` and asserts every value-taking flag
it lists is present (with VALUE arity) in the corresponding spec table --
catching a future CLI release adding a value-taking flag before it can
silently reintroduce Issue #4655 Defect 1's shape (an unrecognized flag's
value misread as a second route/positional). One shared diff/assert core
(`_assert_spec_covers_parsed_flags`) is parameterized by each CLI's own
help-line-parsing heuristic, since gh/curl/git/pip's `--help` formats are
structurally different (a per-line FLAGS listing, a bracketed usage
synopsis, a sectioned options list, ...) -- the parsing can't be shared,
only the comparison/assertion logic.

curl's flag surface (~250 flags) is deliberately *not* exhaustively covered
by `_CURL_FLAG_SPEC` (see that table's own module-level comment); its
contract test is scoped accordingly -- see
`test_curl_flag_spec_covers_this_rectifys_named_flags` for what it actually
checks and why.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from collections.abc import Mapping

import pytest

from autoskillit.hooks._command_classification import (
    _GIT_GLOBAL_FLAG_SPEC,
    _PIP_GLOBAL_FLAG_SPEC,
    _FlagArity,
)
from autoskillit.hooks._github_mutation_analysis import (
    _CURL_FLAG_SPEC,
    _GH_API_FLAG_SPEC,
)

pytestmark = [pytest.mark.layer("infra"), pytest.mark.small]


def _assert_spec_covers_parsed_flags(
    parsed_flags: set[str],
    spec: Mapping[str, _FlagArity],
    *,
    cli_label: str,
    sanity_floor: int = 5,
) -> None:
    """Shared live-CLI-help-diff assertion.

    Every member of *parsed_flags* (value-taking flags parsed from a live
    `--help` read) must be present in *spec* with VALUE arity. Fails loudly
    (never silently passes) if *parsed_flags* is empty or below
    *sanity_floor* -- that signals a broken parser, not the CLI genuinely
    having few value-taking flags.
    """
    assert len(parsed_flags) >= sanity_floor, (
        f"{cli_label} --help flag parser extracted only {len(parsed_flags)} value-taking "
        f"flags ({sorted(parsed_flags)}) -- the parser is likely broken, not {cli_label}'s "
        "help output actually shrinking below the sanity floor"
    )
    spec_value_flags = {flag for flag, arity in spec.items() if arity == _FlagArity.VALUE}
    missing = parsed_flags - spec_value_flags
    assert not missing, (
        f"{cli_label} --help lists value-taking flags absent from its spec table: "
        f"{sorted(missing)} -- add them in src/autoskillit/hooks/_command_classification.py"
    )


def _parse_help_line_placeholder_flags(help_text: str, *, section_header: str | None) -> set[str]:
    """Parse a per-line `FLAGS`-style help section (gh, curl, pip's shape).

    A value-taking flag has an extra whitespace-delimited placeholder token
    (e.g. `string`, `<path>`, `key=value`) between the flag name(s) and the
    right-padded description column boundary (always >= 2 spaces); a
    boolean flag does not. If *section_header* is given, only lines after
    that header (until the next unindented line) are scanned; if None, every
    indented, flag-shaped line in the text is scanned (curl's `--help all`
    has no distinct header -- its whole body past the usage line is flags).
    """
    value_flags: set[str] = set()
    in_section = section_header is None
    for line in help_text.splitlines():
        stripped = line.strip()
        if section_header is not None:
            if stripped == section_header:
                in_section = True
                continue
            if in_section and line and not line[0].isspace():
                break
            if not in_section:
                continue
        if not stripped or not stripped.startswith("-"):
            continue
        flag_part = re.split(r"\s{2,}", stripped, maxsplit=1)[0]
        tokens = flag_part.replace(",", "").split()
        flag_names = [t for t in tokens if t.startswith("-")]
        has_placeholder = len(tokens) > len(flag_names)
        if has_placeholder:
            value_flags.update(flag_names)
    return value_flags


def test_gh_api_flag_spec_covers_every_live_value_taking_flag() -> None:
    if shutil.which("gh") is None:
        pytest.skip("gh binary not available in this environment")

    result = subprocess.run(
        ["gh", "api", "--help"],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, f"gh api --help failed: {result.stderr}"

    parsed_flags = _parse_help_line_placeholder_flags(result.stdout, section_header="FLAGS")
    _assert_spec_covers_parsed_flags(parsed_flags, _GH_API_FLAG_SPEC, cli_label="gh api")


def test_pip_global_flag_spec_covers_every_live_value_taking_flag() -> None:
    if shutil.which("pip") is None:
        pytest.skip("pip binary not available in this environment")

    result = subprocess.run(
        ["pip", "--help"],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, f"pip --help failed: {result.stderr}"

    parsed_flags = _parse_help_line_placeholder_flags(
        result.stdout, section_header="General Options:"
    )
    _assert_spec_covers_parsed_flags(parsed_flags, _PIP_GLOBAL_FLAG_SPEC, cli_label="pip")


def _parse_git_help_value_flags(help_text: str) -> set[str]:
    """Parse git's top-level usage synopsis for value-taking global flags.

    `git --help`'s usage line uses bracket notation: `[--flag <value>]` or
    `[--flag=<value>]` for value-taking flags, `[--flag]` (no `<...>`/`=`)
    for boolean ones, `|` to separate short/long spellings within one
    bracket group (e.g. `[-v | --version]`), and nested brackets for a
    flag's own *optional* value (e.g. `[--exec-path[=<path>]]`) -- the
    latter is excluded from this check entirely (ambiguous, neither
    definitely boolean nor definitely value-taking; see
    _GIT_GLOBAL_FLAG_SPEC's own --exec-path comment for why this module
    treats it as BOOLEAN by default rather than guessing).
    """
    value_flags: set[str] = set()
    match = re.search(r"usage:\s*git\s+(.*?)\s*<command>", help_text, re.DOTALL)
    if not match:
        return value_flags
    usage_text = match.group(1).replace("\n", " ")

    groups: list[str] = []
    depth = 0
    current: list[str] = []
    for char in usage_text:
        if char == "[":
            if depth == 0:
                current = []
            else:
                current.append(char)
            depth += 1
            continue
        if char == "]":
            depth -= 1
            if depth == 0:
                groups.append("".join(current))
            else:
                current.append(char)
            continue
        if depth > 0:
            current.append(char)

    for group in groups:
        if "[" in group:
            continue
        if "<" not in group and "=" not in group:
            continue
        flag_part = re.split(r"[<=]", group, maxsplit=1)[0]
        for spelling in flag_part.split("|"):
            spelling = spelling.strip()
            if spelling.startswith("-"):
                value_flags.add(spelling)
    return value_flags


def test_git_global_flag_spec_covers_every_live_value_taking_flag() -> None:
    if shutil.which("git") is None:
        pytest.skip("git binary not available in this environment")

    result = subprocess.run(
        ["git", "--help"],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, f"git --help failed: {result.stderr}"

    parsed_flags = _parse_git_help_value_flags(result.stdout)
    _assert_spec_covers_parsed_flags(
        parsed_flags, _GIT_GLOBAL_FLAG_SPEC, cli_label="git", sanity_floor=3
    )


# curl's live surface (~250 flags across `curl --help all`) is deliberately
# *not* exhaustively mirrored in _CURL_FLAG_SPEC -- see that table's own
# module comment: an unrecognized curl flag now fails closed, so exhaustive
# coverage would need to weigh against the availability cost of denying
# every real-world curl invocation that happens to use one of curl's many
# rarely-used flags outside this rectify's named set. Rather than silently
# skip a live-contract check for curl entirely, this test makes the scope
# limitation explicit: it pins the specific flags this rectify's
# investigation named (plus curl's own already-covered special-cased
# flags), not curl's full surface.
_CURL_FLAGS_THIS_RECTIFY_MUST_COVER: frozenset[str] = frozenset(
    {
        "-X",
        "--request",
        "--url",
        "-d",
        "--data",
        "--data-raw",
        "--data-binary",
        "--data-urlencode",
        "-F",
        "--form",
        "-T",
        "--upload-file",
        "-H",
        "--header",
        "-u",
        "--user",
        "-o",
        "--output",
        "-A",
        "--user-agent",
        "-b",
        "--cookie",
        "-c",
        "--cookie-jar",
        "-x",
        "--proxy",
        "-w",
        "--write-out",
        "-m",
        "--max-time",
        "--cacert",
        "-E",
        "--cert",
        "--key",
        "--connect-timeout",
        "--retry",
        "--resolve",
    }
)


def test_curl_flag_spec_covers_this_rectifys_named_flags() -> None:
    if shutil.which("curl") is None:
        pytest.skip("curl binary not available in this environment")

    result = subprocess.run(
        ["curl", "--help", "all"],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, f"curl --help all failed: {result.stderr}"

    parsed_flags = _parse_help_line_placeholder_flags(result.stdout, section_header=None)
    assert len(parsed_flags) >= 20, (
        f"curl --help all flag parser extracted only {len(parsed_flags)} value-taking "
        "flags -- the parser is likely broken, not curl's help output shrinking"
    )

    named_but_removed = _CURL_FLAGS_THIS_RECTIFY_MUST_COVER - parsed_flags
    assert not named_but_removed, (
        "curl --help all no longer lists flags this rectify's investigation named "
        f"(possibly renamed/removed upstream): {sorted(named_but_removed)} -- update "
        "_CURL_FLAG_SPEC and this set together in "
        "src/autoskillit/hooks/_command_classification.py"
    )

    spec_value_flags = {
        flag for flag, arity in _CURL_FLAG_SPEC.items() if arity == _FlagArity.VALUE
    }
    not_yet_covered = _CURL_FLAGS_THIS_RECTIFY_MUST_COVER - spec_value_flags
    assert not not_yet_covered, (
        f"_CURL_FLAG_SPEC is missing flags this rectify's investigation named as VALUE "
        f"arity: {sorted(not_yet_covered)}"
    )

    out_of_scope = parsed_flags - spec_value_flags
    if out_of_scope:
        # Not a failure -- see the module-level comment on why curl's table
        # is deliberately bounded. Surfaced here (not silently dropped) so
        # the scope limitation stays visible in test output.
        print(
            f"\ncurl --help all lists {len(out_of_scope)} value-taking flags outside "
            "_CURL_FLAG_SPEC's deliberately-bounded coverage (an unrecognized curl "
            "flag now fails closed for these): "
            f"{sorted(out_of_scope)[:10]}{'...' if len(out_of_scope) > 10 else ''}"
        )
