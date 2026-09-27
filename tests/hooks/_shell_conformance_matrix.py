"""Declarative shell-capture conformance cases and their named baselines."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Final


class ConformanceBaseline(StrEnum):
    RAW_BASH = "raw_bash"
    NATIVE_CODEX = "native_codex"
    DOCUMENTED_DIFFERENCE = "documented_difference"


class ConformanceMode(StrEnum):
    CAPTURE = "capture"
    DIRECT = "direct"


class ConformanceDriver(StrEnum):
    COMMAND = "command"
    HOST_SIGKILL = "host_sigkill"
    HOST_SIGTERM = "host_sigterm"


class ConformanceExpectation(StrEnum):
    RAW_OUTPUT_AND_STATUS = "raw_output_and_status"
    SHELL_SIGNAL_STATUS = "shell_signal_status"
    LATE_PIPE_BYTES_INCLUDED = "late_pipe_bytes_included"
    LATE_PIPE_BYTES_SETTLED = "late_pipe_bytes_settled"
    MARKER_SETTLED = "marker_settled"
    MARKER_WRITTEN = "marker_written"
    USER_GROUP_SETTLED = "user_group_settled"


@dataclass(frozen=True, slots=True)
class ConformanceInvariantDef:
    id: str
    baseline: ConformanceBaseline
    statement: str


@dataclass(frozen=True, slots=True)
class ConformanceCaseDef:
    id: str
    invariant: str
    driver: ConformanceDriver
    command: str
    expect: Mapping[ConformanceMode, ConformanceExpectation]


CONFORMANCE_INVARIANTS: Final[dict[str, ConformanceInvariantDef]] = {
    invariant.id: invariant
    for invariant in (
        ConformanceInvariantDef(
            "command-text",
            ConformanceBaseline.RAW_BASH,
            "The runner executes the command text verbatim as `bash -c <command>`.",
        ),
        ConformanceInvariantDef(
            "initial-trap-state",
            ConformanceBaseline.RAW_BASH,
            "The command starts with the same trap state as raw `bash -c`.",
        ),
        ConformanceInvariantDef(
            "user-trap-semantics",
            ConformanceBaseline.RAW_BASH,
            (
                "User-installed, replaced, and cleared traps run with raw-compatible "
                "output and status."
            ),
        ),
        ConformanceInvariantDef(
            "exit-status",
            ConformanceBaseline.RAW_BASH,
            (
                "The shell-compatible status comes from the leader's own wait "
                "status; the runner injects no finalization into the shell."
            ),
        ),
        ConformanceInvariantDef(
            "signal-status",
            ConformanceBaseline.DOCUMENTED_DIFFERENCE,
            "A command that dies by signal n reports shell-compatible status 128+n.",
        ),
        ConformanceInvariantDef(
            "merged-descriptors",
            ConformanceBaseline.DOCUMENTED_DIFFERENCE,
            (
                "Capture mode merges stderr into the managed stdout pipe; direct "
                "mode keeps the streams separate."
            ),
        ),
        ConformanceInvariantDef(
            "pipe-eof-completion",
            ConformanceBaseline.DOCUMENTED_DIFFERENCE,
            (
                "In capture mode, same-group pipe holders are waited for until "
                "actual pipe EOF whatever their trap state or job style; in direct "
                "mode they are settled at leader exit."
            ),
        ),
        ConformanceInvariantDef(
            "descendant-settlement",
            ConformanceBaseline.NATIVE_CODEX,
            (
                "Same-group descendants that do not hold the pipe are settled after "
                "completion whatever their trap state or job style."
            ),
        ),
        ConformanceInvariantDef(
            "setsid-escape",
            ConformanceBaseline.DOCUMENTED_DIFFERENCE,
            "A descendant that calls setsid() leaves the owned group and is not settled.",
        ),
        ConformanceInvariantDef(
            "host-lifetime",
            ConformanceBaseline.NATIVE_CODEX,
            "When the host kills or terminates the runner, the user process group ends with it.",
        ),
    )
}

NATIVE_BASELINE_CODEX_VERSION: Final = "0.156.1"


def _case(
    case_id: str,
    invariant: str,
    command: str,
    capture: ConformanceExpectation = ConformanceExpectation.RAW_OUTPUT_AND_STATUS,
    direct: ConformanceExpectation = ConformanceExpectation.RAW_OUTPUT_AND_STATUS,
    *,
    driver: ConformanceDriver = ConformanceDriver.COMMAND,
) -> ConformanceCaseDef:
    return ConformanceCaseDef(
        id=case_id,
        invariant=invariant,
        driver=driver,
        command=command,
        expect={ConformanceMode.CAPTURE: capture, ConformanceMode.DIRECT: direct},
    )


_PIPE_HOLDER_EXPECTATIONS = {
    ConformanceMode.CAPTURE: ConformanceExpectation.LATE_PIPE_BYTES_INCLUDED,
    ConformanceMode.DIRECT: ConformanceExpectation.LATE_PIPE_BYTES_SETTLED,
}
_MARKER_SETTLED_EXPECTATIONS = {
    ConformanceMode.CAPTURE: ConformanceExpectation.MARKER_SETTLED,
    ConformanceMode.DIRECT: ConformanceExpectation.MARKER_SETTLED,
}
_MARKER_WRITTEN_EXPECTATIONS = {
    ConformanceMode.CAPTURE: ConformanceExpectation.MARKER_WRITTEN,
    ConformanceMode.DIRECT: ConformanceExpectation.MARKER_WRITTEN,
}
_USER_GROUP_SETTLED_EXPECTATIONS = {
    ConformanceMode.CAPTURE: ConformanceExpectation.USER_GROUP_SETTLED,
    ConformanceMode.DIRECT: ConformanceExpectation.USER_GROUP_SETTLED,
}


CONFORMANCE_CASES: Final[tuple[ConformanceCaseDef, ...]] = (
    _case(
        "execution-string",
        "command-text",
        "printf '%s' \"$BASH_EXECUTION_STRING\"",
    ),
    _case("subshell-depth", "command-text", 'echo "subshell=$BASH_SUBSHELL"'),
    _case("trailing-backslash", "command-text", "echo one \\"),
    _case(
        "pipe_wc",
        "command-text",
        "find . -path ./.autoskillit -prune -o -type f -print 2>&1 | wc -l | head -c 4000",
    ),
    _case("ls_wc", "command-text", "ls . 2>&1 | wc -l"),
    _case(
        "heredoc_append",
        "command-text",
        "cat >> .autoskillit/temp/investigate/report.md <<'MARKER'\nsome content\nMARKER",
    ),
    _case("multi_stmt", "command-text", "cd /tmp && echo done # comment"),
    _case(
        "heredoc_no_newline",
        "command-text",
        "cat <<'END'\nsome text\nEND",
    ),
    _case("stderr_only", "command-text", "echo err >&2"),
    _case("true_cmd", "command-text", "true"),
    _case("large_output", "command-text", "seq 1 200000"),
    _case("rg_sort", "command-text", "rg pat . 2>&1 | sort | uniq -c | head -c 3000"),
    _case("jq_keys", "command-text", "jq -c 'keys' x.jsonl 2>&1 | head -1 | head -c 1000"),
    _case(
        "unicode_heavy",
        "command-text",
        "python3 -c \"import sys; sys.stdout.buffer.write(b'\\xc3\\xa9' * 8000)\"",
    ),
    _case("trap-print-exit", "initial-trap-state", "trap -p EXIT; echo end"),
    _case("trap-print-all", "initial-trap-state", "trap; echo end"),
    _case(
        "trap-install-status",
        "user-trap-semantics",
        "trap 'echo status=$?' EXIT; false",
    ),
    _case(
        "trap-replace-exit",
        "user-trap-semantics",
        "trap 'echo first' EXIT; trap 'echo user-exit; exit 9' EXIT; exit 4",
    ),
    _case(
        "trap-reset",
        "user-trap-semantics",
        "trap 'echo never' EXIT; trap - EXIT; echo done",
    ),
    _case(
        "trap-cleanup-idiom",
        "user-trap-semantics",
        (
            't=$(mktemp "$PWD/tmp.XXXXXX"); trap \'rm -f "$t"; echo cleaned\' '
            'EXIT; test -f "$t" && echo exists'
        ),
    ),
    _case("exit_3", "exit-status", "exit 3"),
    _case("false_cmd", "exit-status", "false"),
    _case("mid_exit", "exit-status", "echo pre; exit 7; echo post"),
    _case("errexit", "exit-status", "set -e; false; echo unreachable"),
    _case("exec-status", "exit-status", "exec bash -c 'echo replaced; exit 5'"),
    _case(
        "self-signal",
        "signal-status",
        "echo pre; kill -TERM $$",
        ConformanceExpectation.SHELL_SIGNAL_STATUS,
        ConformanceExpectation.SHELL_SIGNAL_STATUS,
    ),
    _case(
        "fatal-leader-with-pipe-holder",
        "signal-status",
        "{ sleep 0.3; echo late; } & echo early; kill -KILL $$",
        ConformanceExpectation.LATE_PIPE_BYTES_INCLUDED,
        ConformanceExpectation.LATE_PIPE_BYTES_SETTLED,
    ),
    _case(
        "separate-streams",
        "merged-descriptors",
        "printf stdout; printf stderr >&2; exit 7",
    ),
    _case(
        "tracked-pipe-holder",
        "pipe-eof-completion",
        "{ sleep 0.3; echo late; } & echo started",
        *_PIPE_HOLDER_EXPECTATIONS.values(),
    ),
    _case(
        "trap-cleared-pipe-holder",
        "pipe-eof-completion",
        "trap - EXIT; { sleep 0.3; echo late; } & echo started",
        *_PIPE_HOLDER_EXPECTATIONS.values(),
    ),
    _case(
        "user-trap-pipe-holder",
        "pipe-eof-completion",
        "trap 'echo user-exit' EXIT; { sleep 0.3; echo late; } & echo started",
        *_PIPE_HOLDER_EXPECTATIONS.values(),
    ),
    _case(
        "nested-subshell-pipe-holder",
        "pipe-eof-completion",
        "( { sleep 0.3; echo late; } & ); echo started",
        *_PIPE_HOLDER_EXPECTATIONS.values(),
    ),
    _case(
        "nested-bash-pipe-holder",
        "pipe-eof-completion",
        "bash -c '{ sleep 0.3; echo late; } &'; echo started",
        *_PIPE_HOLDER_EXPECTATIONS.values(),
    ),
    _case(
        "disowned-pipe-holder",
        "pipe-eof-completion",
        "{ sleep 0.3; echo late; } & disown; echo started",
        *_PIPE_HOLDER_EXPECTATIONS.values(),
    ),
    _case(
        "exec-pipe-holder",
        "pipe-eof-completion",
        "{ sleep 0.3; echo late; } & exec echo started",
        *_PIPE_HOLDER_EXPECTATIONS.values(),
    ),
    _case(
        "redirected-job",
        "descendant-settlement",
        "J() { (sleep 0.3; echo ok > marker) >/dev/null 2>&1; }; J & echo started",
        *_MARKER_SETTLED_EXPECTATIONS.values(),
    ),
    _case(
        "redirected-job-trap-cleared",
        "descendant-settlement",
        "J() { (sleep 0.3; echo ok > marker) >/dev/null 2>&1; }; trap - EXIT; J & echo started",
        *_MARKER_SETTLED_EXPECTATIONS.values(),
    ),
    _case(
        "redirected-job-user-trap",
        "descendant-settlement",
        (
            "J() { (sleep 0.3; echo ok > marker) >/dev/null 2>&1; }; trap 'echo "
            "user-exit' EXIT; J & echo started"
        ),
        *_MARKER_SETTLED_EXPECTATIONS.values(),
    ),
    _case(
        "redirected-job-cleanup-trap",
        "descendant-settlement",
        (
            "J() { (sleep 0.3; echo ok > marker) >/dev/null 2>&1; }; t=$(mktemp "
            '"$PWD/tmp.XXXXXX"); trap \'rm -f "$t"\' EXIT; J & echo started'
        ),
        *_MARKER_SETTLED_EXPECTATIONS.values(),
    ),
    _case(
        "redirected-job-nested-subshell",
        "descendant-settlement",
        "J() { (sleep 0.3; echo ok > marker) >/dev/null 2>&1; }; ( J & ); echo started",
        *_MARKER_SETTLED_EXPECTATIONS.values(),
    ),
    _case(
        "redirected-job-nested-bash",
        "descendant-settlement",
        "bash -c '(sleep 0.3; echo ok > marker) >/dev/null 2>&1 &'; echo started",
        *_MARKER_SETTLED_EXPECTATIONS.values(),
    ),
    _case(
        "redirected-job-disown",
        "descendant-settlement",
        "J() { (sleep 0.3; echo ok > marker) >/dev/null 2>&1; }; J & disown; echo started",
        *_MARKER_SETTLED_EXPECTATIONS.values(),
    ),
    _case(
        "redirected-job-exec",
        "descendant-settlement",
        "J() { (sleep 0.3; echo ok > marker) >/dev/null 2>&1; }; J & exec true",
        *_MARKER_SETTLED_EXPECTATIONS.values(),
    ),
    _case(
        "setsid-redirected-job",
        "setsid-escape",
        (
            "python3 -c \"import os,time; os.setsid(); open('ready','w').close(); "
            "time.sleep(0.3); open('marker','w').write('ok')\" </dev/null "
            ">/dev/null 2>&1 & until [ -e ready ]; do sleep 0.01; done; echo "
            "started"
        ),
        *_MARKER_WRITTEN_EXPECTATIONS.values(),
    ),
    _case(
        "host-sigkill-mid-command",
        "host-lifetime",
        "echo $$ > leader.pid; sleep 1; echo survived > marker",
        *_USER_GROUP_SETTLED_EXPECTATIONS.values(),
        driver=ConformanceDriver.HOST_SIGKILL,
    ),
    _case(
        "host-sigterm-mid-command",
        "host-lifetime",
        "echo $$ > leader.pid; sleep 1; echo survived > marker",
        *_USER_GROUP_SETTLED_EXPECTATIONS.values(),
        driver=ConformanceDriver.HOST_SIGTERM,
    ),
)
