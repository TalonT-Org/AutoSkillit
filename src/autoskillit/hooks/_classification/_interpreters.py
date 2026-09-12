"""Interpreter and nested-shell payload classification helpers."""

from __future__ import annotations

import os
import re
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from autoskillit.hooks._classification._flag_arity import _FlagArity
    from autoskillit.hooks._classification._python_program_analysis import (
        _InterpreterCommandSpec,
        _python_program_command_specs,
    )
    from autoskillit.hooks._classification._substitution_scanning import (
        _extract_process_substitution_occurrences,
        _iter_substitution_occurrences,
    )
    from autoskillit.hooks._runtime._command_classification import (
        _INTERPRETER_RE,
        _LITERAL_OPEN_PATH_RE,
        _LITERAL_PATH_CONSTRUCTOR_RE,
        _WRITE_APIS_RE,
        _WRITE_CALL_SITE_RE,
        StdinLiteral,
        _command_position_candidate_spans,
        _CommandSegment,
        _tokenize_command_segments_with_redirects,
        command_verb_and_args,
        strip_heredoc_bodies,
        tokenize_command_segments,
    )

    _SHELL_INVOCATION_FLAG_SPEC: dict[str, _FlagArity]
    _PYTHON_INVOCATION_FLAG_SPEC: dict[str, _FlagArity]
else:
    # _flag_arity, _python_program_analysis, and _substitution_scanning are
    # leaf siblings (no back-reference into the facade), so they can be
    # imported at the top of the file without the circular-bootstrap hazard
    # that governs the _flags import at the bottom of this module. _FlagArity
    # used to come from _flags directly (PR #4983 review: that forced a
    # bidirectional _flags<->_interpreters coupling -- _flags needed
    # all_evaluated_segments/live_command_text from here, and here needed
    # _FlagArity from _flags); it now comes from this dependency-free leaf
    # instead, leaving only the _flags -> _interpreters edge.
    if __package__:
        from . import _flag_arity, _python_program_analysis, _substitution_scanning
    else:
        import _flag_arity
        import _python_program_analysis
        import _substitution_scanning

    _FlagArity = _flag_arity._FlagArity
    _InterpreterCommandSpec = _python_program_analysis._InterpreterCommandSpec
    _python_program_command_specs = _python_program_analysis._python_program_command_specs
    _extract_process_substitution_occurrences = (
        _substitution_scanning._extract_process_substitution_occurrences
    )
    _iter_substitution_occurrences = _substitution_scanning._iter_substitution_occurrences

    # Flags that consume a following value when the shell/Python interpreter
    # itself (not a heredoc/herestring body) reads a script from a
    # positional operand. Only VALUE-arity flags are listed: any other
    # `-`/`+`-prefixed token is skipped as boolean by the default bucket in
    # `_walk_invocation_flags`. Defined here (rather than at the bottom of
    # the file) now that _FlagArity no longer forces a deferred, re-entrant
    # import -- see the comment above.
    _SHELL_INVOCATION_FLAG_SPEC: dict[str, _FlagArity] = {
        "-o": _FlagArity.VALUE,
        "+o": _FlagArity.VALUE,
        "-O": _FlagArity.VALUE,
        "+O": _FlagArity.VALUE,
        "--rcfile": _FlagArity.VALUE,
        "--init-file": _FlagArity.VALUE,
        # Bash 5.2 help does not print --rcfile/--init-file's operand arity;
        # covered by the generative flag-spec test instead of the
        # live-`--help` contract test (see
        # tests/hooks/test_gh_api_flag_spec_contract.py).
        "-c": _FlagArity.VALUE,
    }
    _PYTHON_INVOCATION_FLAG_SPEC: dict[str, _FlagArity] = {
        "-c": _FlagArity.VALUE,
        "-m": _FlagArity.VALUE,
        "-W": _FlagArity.VALUE,
        "-X": _FlagArity.VALUE,
        "--check-hash-based-pycs": _FlagArity.VALUE,
    }


def has_interpreter_write(command: str) -> bool:
    """Return True when a live (executed) interpreter payload writes a file.

    Scans `live_command_text(command)` -- the occurrence-aware projection
    that blanks an inert heredoc/herestring body -- rather than the raw
    command, so a `python3 script.py <<'EOF'` whose body merely contains
    `open(..., "w")` as inert stdin data (the script has an operand and
    never executes its stdin) is not mistaken for a live write.
    """
    text = live_command_text(command)
    if not _INTERPRETER_RE.search(text):
        return False
    return bool(_WRITE_APIS_RE.search(text))


def extract_interpreter_write_paths(command: str) -> list[str] | None:
    """Extract literal file paths from an interpreter write command.

    Scans `live_command_text(command)` (see `has_interpreter_write`) so an
    inert stdin body's mention of a write call never fabricates a target.

    Returns:
        None    — command is not an interpreter write (no prefix or no write API).
        []      — interpreter write detected but not all paths are static literals
                  (dynamic variable, f-string, shutil two-arg, or mixed).
        [paths] — all write target paths are static literals (may be relative).
    """
    text = live_command_text(command)
    if not _INTERPRETER_RE.search(text):
        return None
    if not _WRITE_APIS_RE.search(text):
        return None

    call_site_count = len(_WRITE_CALL_SITE_RE.findall(text))

    paths: list[str] = []
    for m in _LITERAL_OPEN_PATH_RE.finditer(text):
        paths.append(m.group(2))
    for m in _LITERAL_PATH_CONSTRUCTOR_RE.finditer(text):
        paths.append(m.group(2))

    if len(paths) < call_site_count:
        return []

    return paths if paths else []


_SHELL_INTERPRETERS: frozenset[str] = frozenset({"bash", "sh", "zsh", "dash"})


class StdinConsumer(StrEnum):
    """What a segment's verb does with stdin fed to it via a heredoc/herestring/pipe.

    SHELL — the body is executed as shell text (bash/sh/zsh/dash reading its
    own script from stdin). PYTHON — the body is executed as a Python
    program (`python3 -` or bare `python3` with no script operand). TEXT —
    the body is executed by some other recognized interpreter whose stdin
    program cannot be tokenized into argv segments here (perl/ruby/node).
    INERT — the verb does not execute its stdin as code (`cat`, `tee`, a
    `-c`-driven shell/Python whose own stdin is data, an interpreter given a
    script-file operand, or anything unrecognized).
    """

    SHELL = "shell"
    PYTHON = "python"
    TEXT = "text"
    INERT = "inert"


def _normalize_executable(token: str) -> str:
    return os.path.basename(token).lower()


def _is_shell_interpreter(token: str) -> bool:
    base = _normalize_executable(token)
    if base in _SHELL_INTERPRETERS:
        return True
    # Versioned forms: bash5, sh4, dash0.5, zsh5
    for name in _SHELL_INTERPRETERS:
        if base.startswith(name) and base[len(name) :].isdigit():
            return True
    return False


def _walk_invocation_flags(
    args: Sequence[str],
    *,
    spec: dict[str, _FlagArity],
    inert_flags: frozenset[str],
    consumer_flags: frozenset[str],
    end_result: StdinConsumer,
) -> StdinConsumer:
    """Shared four-bucket argv walk for shell and Python invocation flags.

    (1) a token in *inert_flags* (`-c`, or Python's `-c`/`-m`) means the
    program executes and its own stdin is data -- INERT. (2) a token in
    *consumer_flags* (`-s`/`-`/`--`/`/dev/stdin` for shells, `-` for
    Python) means the interpreter reads its script from stdin --
    *end_result*. (3) a *spec* VALUE flag consumes the next token. (4) any
    other `-`/`+`-prefixed token is a no-value flag and is skipped. The
    first token matching none of these is a script-file operand -- INERT.
    Reaching the end of *args* with no operand found means the interpreter
    reads its script from stdin by default -- *end_result*.
    """
    i = 0
    n = len(args)
    while i < n:
        token = args[i]
        if token in inert_flags:
            return StdinConsumer.INERT
        if token in consumer_flags:
            return end_result
        if spec.get(token) == _FlagArity.VALUE:
            i += 2
            continue
        if token.startswith("-") or token.startswith("+"):
            i += 1
            continue
        return StdinConsumer.INERT
    return end_result


_SHELL_CONSUMER_FLAGS: frozenset[str] = frozenset({"-s", "-", "--", "/dev/stdin"})
_PYTHON_CONSUMER_FLAGS: frozenset[str] = frozenset({"-"})
_GENERIC_TEXT_INTERPRETERS: frozenset[str] = frozenset({"perl", "ruby", "node"})


def _generic_interpreter_stdin_consumer(args: Sequence[str]) -> StdinConsumer:
    for arg in args:
        if arg == "-":
            return StdinConsumer.TEXT
        if arg.startswith(("-", "+")):
            continue
        return StdinConsumer.INERT
    return StdinConsumer.TEXT


def _stdin_consumer_for_verb(tokens: Sequence[str]) -> StdinConsumer:
    verb, args = command_verb_and_args(list(tokens))
    if not verb:
        return StdinConsumer.INERT
    if _is_shell_interpreter(verb):
        return _walk_invocation_flags(
            args,
            spec=_SHELL_INVOCATION_FLAG_SPEC,
            inert_flags=frozenset({"-c"}),
            consumer_flags=_SHELL_CONSUMER_FLAGS,
            end_result=StdinConsumer.SHELL,
        )
    executable = _normalize_executable(verb)
    if re.fullmatch(r"python(?:3(?:\.\d+)?)?", executable) is not None:
        return _walk_invocation_flags(
            args,
            spec=_PYTHON_INVOCATION_FLAG_SPEC,
            inert_flags=frozenset({"-c", "-m"}),
            consumer_flags=_PYTHON_CONSUMER_FLAGS,
            end_result=StdinConsumer.PYTHON,
        )
    if executable in _GENERIC_TEXT_INTERPRETERS:
        return _generic_interpreter_stdin_consumer(args)
    return StdinConsumer.INERT


def stdin_consumer(tokens: Sequence[str]) -> StdinConsumer:
    """Return what *tokens* (a segment's argv) does with stdin fed to it.

    Walks every candidate span `_command_position_candidate_spans` exposes
    for *tokens* -- the segment's own verb position, and (for `name() { ...`
    or `{ ...`) the wrapped body's verb position too -- so a heredoc bound
    to `f() { bash <<'EOF' ... EOF }; f` is recognized as SHELL through the
    body's `bash`, not misread as INERT through the outer `f()`/`{` token
    that the primary span alone would resolve to.
    """
    for start, end in _command_position_candidate_spans(list(tokens)):
        result = _stdin_consumer_for_verb(tokens[start:end])
        if result != StdinConsumer.INERT:
            return result
    return StdinConsumer.INERT


@dataclass(frozen=True, slots=True)
class EvaluatedPayload:
    """One text payload that will actually be evaluated, and by whom.

    `origin` names the syntactic source: `"-c"`, `"eval"`, `"python-c"`,
    `"heredoc"`, `"herestring"`, `"pipe"`, or `"substitution"`.
    `consumer_index` is the owning segment's index in the outer command's
    segment list, or `None` when no segment could be identified.
    `source_span` is the payload's exact occurrence span in the original
    command string; `None` for a payload synthesized from another segment's
    tokens (a `-c`/`eval` argument, a resolved pipe payload) rather than
    read directly from a source span.
    """

    text: str
    kind: StdinConsumer
    consumer_index: int | None
    origin: str
    source_span: tuple[int, int] | None


def extract_shell_command_payloads(command: str) -> list[str]:
    """Return shell text payloads that will actually be evaluated.

    A thin projection of `evaluated_payloads`: every payload whose consumer
    is a shell. See `evaluated_payloads` for the two-fact rule this
    reflects -- outer-shell expansion (whether a heredoc/herestring body can
    itself contain live substitutions) is independent of consumer execution
    (whether the text that reaches the consumer is executed as shell).
    Single-quoted text, escaped substitutions, and an inert
    heredoc/herestring body are never included.
    """
    return [
        payload.text
        for payload in evaluated_payloads(command)
        if payload.kind == StdinConsumer.SHELL
    ]


def tokenize_shell_payload_segments(
    command: str,
    *,
    include_process_substitutions: bool = False,
) -> list[list[str]] | None:
    """Return tokenized segments for every evaluated shell payload in *command*.

    Walks the outer command and every distinct extracted payload recursively.
    Each successfully parsed segment of every payload is appended to the
    result so callers can apply verb-position policies like
    ``command_verb_and_args`` to each segment. Process-substitution bodies
    are traversed only when ``include_process_substitutions`` is true; the
    default preserves the historic shell-command-substitution-only behavior.

    Returns ``None`` when the outer command or any non-empty evaluated
    payload cannot be tokenized; callers interpret ``None`` as no deny
    match (fail-open). Returns ``[]`` when the command has no evaluated
    shell payload to traverse.
    """
    outer = tokenize_command_segments(command)
    if not outer and command.strip():
        return None

    result: list[list[str]] = []
    seen: set[str] = set()
    queue: list[tuple[str, bool]] = [
        (payload, False) for payload in extract_shell_command_payloads(command)
    ]
    if include_process_substitutions:
        for _kind, _start, _end, body, balanced in _extract_process_substitution_occurrences(
            command
        ):
            if not balanced:
                return None
            queue.append((body, True))
    while queue:
        payload, preserve_occurrence = queue.pop(0)
        if not preserve_occurrence and payload in seen:
            continue
        if not preserve_occurrence:
            seen.add(payload)
        if not payload.strip():
            continue
        segments = tokenize_command_segments(payload)
        if not segments and payload.strip():
            return None
        result.extend(segments)
        queue.extend(
            (nested, preserve_occurrence) for nested in extract_shell_command_payloads(payload)
        )
        if include_process_substitutions:
            for _kind, _start, _end, body, balanced in _extract_process_substitution_occurrences(
                payload
            ):
                if not balanced:
                    return None
                queue.append((body, True))
    return result


def _occurrence_owner_index(command: str, start: int, segment_count: int) -> int | None:
    """Map a substitution occurrence's start offset to its owning segment.

    Mirrors `_github_mutation_analysis._process_occurrence_owner_index`:
    retokenizing the text preceding the occurrence and counting how many
    segments that prefix produced identifies which segment the occurrence's
    `$(...)`/backtick span sits inside, independent of any two segments
    sharing identical payload text.
    """
    if not segment_count:
        return None
    preceding = _tokenize_command_segments_with_redirects(command[:start])
    return min(max(len(preceding) - 1, 0), segment_count - 1)


def _upstream_pipe_text(segments: Sequence[_CommandSegment], index: int) -> str | None:
    """Resolve the text a piped-into segment reads from upstream segment *index*.

    Recurses upstream through a chain of inert `cat` pipes with no stdin
    literal of their own (`a | cat | cat | consumer`). Any other upstream
    producer yields nothing -- textless sources (`curl ... | sh`) are a
    documented non-goal.
    """
    segment = segments[index]
    verb, args = command_verb_and_args(list(segment.tokens))
    executable = _normalize_executable(verb)
    if executable == "cat" and (not args or args == ["-"]):
        if segment.stdin_literals:
            return "\n".join(literal.body for literal in segment.stdin_literals)
        if segment.piped_from_previous and index > 0:
            return _upstream_pipe_text(segments, index - 1)
        return None
    if executable == "echo":
        return " ".join(arg for arg in args if arg not in ("-n", "-e", "-E"))
    if executable == "printf":
        # printf's first argument is its format string, applied to (and
        # consuming) the remaining arguments; joining it into the piped
        # text would inject a garbage leading token ("%s\n git push...")
        # that breaks verb detection downstream. A single-argument
        # invocation has no separate format/value split -- the sole
        # argument is the literal text.
        return " ".join(args[1:]) if len(args) > 1 else (args[0] if args else "")
    return None


def _python_c_program(tokens: Sequence[str]) -> str | None:
    verb, args = command_verb_and_args(list(tokens))
    executable = _normalize_executable(verb)
    if re.fullmatch(r"python(?:3(?:\.\d+)?)?", executable) is None:
        return None
    try:
        index = args.index("-c")
    except ValueError:
        return None
    if index + 1 >= len(args):
        return None
    return args[index + 1]


def evaluated_payloads(command: str) -> list[EvaluatedPayload]:
    """Return every text payload that will actually be evaluated, and by whom.

    The single semantic authority every scanner in this package should
    consume instead of re-deriving liveness from the raw command: a `-c`/
    `eval` argument, a `python -c` program, a heredoc/herestring body bound
    to its consuming segment (via `stdin_consumer`), a resolved pipe
    payload, and every `$(...)`/backtick substitution -- each independently
    mapped to the segment that owns it.
    """
    segments = _tokenize_command_segments_with_redirects(command)
    payloads: list[EvaluatedPayload] = []

    for index, segment in enumerate(segments):
        verb, args = command_verb_and_args(list(segment.tokens))
        if _is_shell_interpreter(verb) and args and args[0] == "-c" and len(args) >= 2:
            payloads.append(EvaluatedPayload(args[1], StdinConsumer.SHELL, index, "-c", None))
        elif verb == "eval" and args:
            payloads.append(
                EvaluatedPayload(" ".join(args), StdinConsumer.SHELL, index, "eval", None)
            )
        else:
            program = _python_c_program(segment.tokens)
            if program is not None:
                payloads.append(
                    EvaluatedPayload(program, StdinConsumer.PYTHON, index, "python-c", None)
                )

        consumer = stdin_consumer(segment.tokens)
        for literal in segment.stdin_literals:
            if consumer != StdinConsumer.INERT:
                origin = "heredoc" if literal.kind == "heredoc" else "herestring"
                payloads.append(
                    EvaluatedPayload(literal.body, consumer, index, origin, literal.source_span)
                )
            if literal.outer_expansion and consumer != StdinConsumer.SHELL:
                for rel_start, sub in _iter_substitution_occurrences(literal.body):
                    abs_span = (
                        (
                            literal.source_span[0] + rel_start,
                            literal.source_span[0] + rel_start + len(sub),
                        )
                        if literal.source_span is not None
                        else None
                    )
                    payloads.append(
                        EvaluatedPayload(sub, StdinConsumer.SHELL, index, "substitution", abs_span)
                    )

        if segment.piped_from_previous and consumer != StdinConsumer.INERT and index > 0:
            upstream_text = _upstream_pipe_text(segments, index - 1)
            if upstream_text is not None:
                payloads.append(EvaluatedPayload(upstream_text, consumer, index, "pipe", None))

    # Scan the inert-body-aware projection, not the raw command: a heredoc
    # or herestring body is erased by strip_heredoc_bodies before this scan
    # ever runs, so prose inside an inert `cat`/`tee` body (a `$(...)` in a
    # fenced code block, an inline backtick example) can no longer be
    # mistaken for a live substitution. A substitution that sits outside
    # every heredoc body is untouched by stripping and is still found here.
    # Per-literal substitutions inside a body are covered separately above
    # via `literal.outer_expansion`, which is independent of this scan.
    stripped_command = strip_heredoc_bodies(command)
    for start, sub in _iter_substitution_occurrences(stripped_command):
        owner = _occurrence_owner_index(stripped_command, start, len(segments))
        payloads.append(EvaluatedPayload(sub, StdinConsumer.SHELL, owner, "substitution", None))

    return payloads


def all_evaluated_segments(
    command: str, *, include_process_substitutions: bool = False
) -> list[list[str]] | None:
    """Return every segment that will actually execute, across every consumer.

    Outer segments, every recursively tokenized SHELL payload, every
    literal-argv Python subprocess spec as its own segment, and every
    resolved string spec whose `invokes_shell` flag is true (`os.system`,
    `subprocess.run(..., shell=True)`) tokenized as shell text. A plain
    string `subprocess.run("...")` spec (no shell) is excluded -- it never
    reaches an argv-splitting shell. Returns `None` when the outer command
    or any evaluated shell payload cannot be tokenized (fail-open).
    ``include_process_substitutions`` is threaded through to
    `tokenize_shell_payload_segments` for callers (e.g.
    `planner_gh_discovery_guard.py`) that must also see `<(...)`/`>(...)`
    bodies; the default preserves the historic shell-substitution-only reach.
    """
    outer = tokenize_command_segments(command)
    if not outer and command.strip():
        return None
    shell_segments = tokenize_shell_payload_segments(
        command, include_process_substitutions=include_process_substitutions
    )
    if shell_segments is None:
        return None

    segments: list[list[str]] = [*outer, *shell_segments]
    for payload in evaluated_payloads(command):
        if payload.kind != StdinConsumer.PYTHON:
            continue
        specs, _has_unresolved = _python_program_command_specs(payload.text)
        for spec in specs:
            if isinstance(spec.payload, list):
                segments.append(spec.payload)
            elif spec.invokes_shell:
                segments.extend(tokenize_command_segments(spec.payload))
    return segments


def live_command_text(command: str) -> str:
    """Return an occurrence-aware live-text projection of *command*.

    A heredoc occurrence is blanked at its original position; one whose
    consumer executes it (per `stdin_consumer`) is appended once, in source
    order, so a regex-based scanner sees its content exactly once without
    ever re-deriving liveness from the raw string. An inert heredoc's body is
    blanked and never appended, so it cannot trigger a raw-text match. A
    `-c`/`eval`/`python -c` payload is already present verbatim in the base
    text at its natural position and is never duplicated by re-appending it.

    A herestring occurrence is never blanked: the tokenizer does not track
    its `source_span` (only a heredoc's placeholder-substitution pass does),
    so it is left exactly once at its natural position in `base`, live or
    inert, and is excluded below rather than re-appended. This differs from
    heredoc's inert-body blanking -- a `#4983` follow-up tracks closing that
    gap -- but it is not a false positive/negative for any current caller: a
    herestring's raw source text already reads identically to its evaluated
    payload text, so a scanner matching against `base` alone sees the same
    content a second, appended copy would have added.
    """
    segments = _tokenize_command_segments_with_redirects(command)
    payloads = evaluated_payloads(command)

    blanked = list(command)
    for segment in segments:
        for literal in segment.stdin_literals:
            if literal.source_span is None:
                continue
            start, end = literal.source_span
            for i in range(start, min(end, len(blanked))):
                blanked[i] = " "
    base = "".join(blanked)

    appended = [
        payload.text
        for payload in payloads
        if payload.origin not in ("-c", "eval", "python-c", "herestring") and payload.text
    ]
    return " ".join([base, *appended]) if appended else base


def _extract_interpreter_segment_specs(
    segment: Sequence[str],
    *,
    stdin_literals: Sequence[StdinLiteral] = (),
) -> tuple[list[_InterpreterCommandSpec], bool]:
    verb, args = command_verb_and_args(list(segment))
    executable = os.path.basename(verb).casefold()
    if re.fullmatch(r"python(?:3(?:\.\d+)?)?", executable) is None:
        return ([], False)

    specs: list[_InterpreterCommandSpec] = []
    has_unresolved = False
    try:
        command_index = args.index("-c")
    except ValueError:
        command_index = None
    if command_index is not None:
        if command_index + 1 >= len(args):
            has_unresolved = True
        else:
            found, unresolved = _python_program_command_specs(args[command_index + 1])
            specs.extend(found)
            has_unresolved = has_unresolved or unresolved

    if stdin_literals and stdin_consumer(list(segment)) == StdinConsumer.PYTHON:
        for literal in stdin_literals:
            found, unresolved = _python_program_command_specs(literal.body)
            specs.extend(found)
            has_unresolved = has_unresolved or unresolved

    return (specs, has_unresolved)


def extract_interpreter_command_payloads(command: str) -> tuple[list[str | list[str]], bool]:
    """Return literal subprocess payloads and whether any were unresolved.

    Maps every PYTHON-consumer payload from `evaluated_payloads` (a `python
    -c` program, or a heredoc/herestring/pipe body a bare `python3`/`python3
    -` executes) through `_python_program_command_specs`, so a subprocess
    call written inside a piped-in or heredoc-fed Python program is found
    the same way one written inline after `-c` is.
    """
    specs: list[str | list[str]] = []
    has_unresolved = False
    for payload in evaluated_payloads(command):
        if payload.kind != StdinConsumer.PYTHON:
            continue
        found, unresolved = _python_program_command_specs(payload.text)
        specs.extend(spec.payload for spec in found)
        has_unresolved = has_unresolved or unresolved
    return (specs, has_unresolved)


def interpreter_invokes(command: str, *, target: Sequence[str]) -> bool:
    """Return True when a PYTHON-consumer payload resolves to invoking *target*.

    Successor to the deleted `has_interpreter_wrapped_command`'s raw
    substring scan: walks every PYTHON-consumer payload from
    `evaluated_payloads` (a `python -c` program, or a heredoc/herestring/pipe
    body a bare `python3`/`python3 -` executes) through
    `_python_program_command_specs`. A literal argv spec
    (`subprocess.run(["gh", "pr", "create", ...])`) matches when its leading
    tokens equal *target*, regardless of `shell=`. A string spec
    (`os.system("gh pr create ...")`, or `subprocess.run("...", shell=True)`)
    is tokenized as shell text and matched only when
    `_InterpreterCommandSpec.invokes_shell` is true -- a plain string passed
    to `subprocess.run` without `shell=True` never reaches an argv-splitting
    shell and must not match even when its leading words equal *target*.
    """
    target_list = list(target)
    width = len(target_list)
    for payload in evaluated_payloads(command):
        if payload.kind != StdinConsumer.PYTHON:
            continue
        specs, _has_unresolved = _python_program_command_specs(payload.text)
        for spec in specs:
            if isinstance(spec.payload, list):
                if spec.payload[:width] == target_list:
                    return True
            elif spec.invokes_shell:
                for segment in tokenize_command_segments(spec.payload):
                    if segment[:width] == target_list:
                        return True
    return False


if not TYPE_CHECKING:
    if __package__ == "autoskillit.hooks._classification":
        from .._runtime import _command_classification as _classification
    else:
        import _command_classification as _classification

    _INTERPRETER_RE = _classification._INTERPRETER_RE
    _LITERAL_OPEN_PATH_RE = _classification._LITERAL_OPEN_PATH_RE
    _LITERAL_PATH_CONSTRUCTOR_RE = _classification._LITERAL_PATH_CONSTRUCTOR_RE
    _WRITE_APIS_RE = _classification._WRITE_APIS_RE
    _WRITE_CALL_SITE_RE = _classification._WRITE_CALL_SITE_RE
    StdinLiteral = _classification.StdinLiteral
    strip_heredoc_bodies = _classification.strip_heredoc_bodies
    _command_position_candidate_spans = _classification._command_position_candidate_spans
    _CommandSegment = _classification._CommandSegment
    _tokenize_command_segments_with_redirects = (
        _classification._tokenize_command_segments_with_redirects
    )
    command_verb_and_args = _classification.command_verb_and_args
    tokenize_command_segments = _classification.tokenize_command_segments
