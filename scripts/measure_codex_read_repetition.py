#!/usr/bin/env python3
"""Measure the Codex intake-discipline repeat-read rate across rollout sessions (#4351).

Three repeat-read definitions have been published for this signal; this tool
implements the REPORT method, and states here how the other two differ:

  - report method (implemented here): counts, per session, only bounded-read
    command shapes whose *leading* verb (before any pipe) is `sed -n`,
    `head`/`tail` applied directly to a path, `nl -ba | sed -n`, or `rg -n`.
    A trailing `| head -c N` output-safety wrapper is NOT itself a
    bounded-read signal — nearly every command in this harness carries one,
    so counting it naively would classify the vast majority of all commands
    as "reads" and destroy the signal. A repeat is the 2nd+ bounded read of
    the same resolved path within one session (rollout file).
  - leading-command method (2026-07-24, 60-rollout sample): the same
    leading-shape classifier described above, applied to a smaller, earlier
    sample. This tool reproduces that method exactly at full corpus size, so
    its output supersedes that one-off run.
  - #4351 issue-body method (22,282 exec calls, 07-22 -> 07-24): counted
    every exec_command call as a "read" with no bounded-shape filter and no
    repeat-of-same-file grouping. It is not reproduced here because it does
    not isolate repeat reads of a file already resident in context, which is
    the friction signal this tool exists to measure.

PASS means a measurement was produced; there is no threshold. NO_DATA means
nothing was measured and exits 1. Unresolved commands and targets are counted,
not guessed.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any, BinaryIO, NamedTuple

from autoskillit.core import (
    CODEX_SESSIONS_SUBDIR,
    default_log_dir,
    parse_intake_discipline_versions,
)
from autoskillit.execution.backends._codex_parse import (
    _logical_rollout_reader,
    _rollout_files,
)
from autoskillit.hooks._classification._flag_arity_classification import _FlagArity
from autoskillit.hooks._classification._flags import _consume_str_flag, _spec_key_for_token
from autoskillit.hooks._classification._output_redirect import _partition_output_redirects
from autoskillit.hooks._classification._tokenizer import (
    _CommandSegment,
    _tokenize_command_segments_with_redirects,
)

DEFAULT_OUT = Path(".autoskillit/temp/codex_read_repetition_report.json")

# The filename carries day precision; directory depth is never interpreted.
_ROLLOUT_DATE_PAT = re.compile(r"rollout-(\d{4}-\d{2}-\d{2})T")
_EXEC_CALL_PAT = re.compile(r"tools\.exec_command\(\s*\{")
_EXEC_CMD_PAT = re.compile(r'(?:(?:"cmd")|\bcmd)\s*:\s*("(?:[^"\\]|\\.)*")')

_READ_VERBS: dict[str, Mapping[str, _FlagArity]] = {
    "rg": {
        **dict.fromkeys(
            "-A -B -C -M -m -e -f -g -t --glob --max-count --max-columns --context "
            "--after-context --before-context --regexp --file --type".split(),
            _FlagArity.VALUE,
        ),
        **dict.fromkeys(
            "-n -l -S -i -o -F -U -c -u -H -N -w -v --line-number --files --hidden "
            "--no-ignore --fixed-strings --pcre2 --no-filename --no-heading --ignore-case "
            "--smart-case --files-with-matches".split(),
            _FlagArity.BOOLEAN,
        ),
    },
    "sed": {
        **dict.fromkeys("-e -f --expression --file".split(), _FlagArity.VALUE),
        **dict.fromkeys("-n -E -r --quiet --silent".split(), _FlagArity.BOOLEAN),
    },
    "head": {
        **dict.fromkeys("-n -c --lines --bytes".split(), _FlagArity.VALUE),
        **dict.fromkeys("-q -v -f -F".split(), _FlagArity.BOOLEAN),
    },
    "tail": {
        **dict.fromkeys("-n -c --lines --bytes".split(), _FlagArity.VALUE),
        **dict.fromkeys("-q -v -f -F".split(), _FlagArity.BOOLEAN),
    },
    "nl": dict.fromkeys("-b --body-numbering".split(), _FlagArity.VALUE),
}


class RolloutRecords(NamedTuple):
    commands: list[str]
    policy_versions: frozenset[int]
    unclassified: int
    malformed_lines: int


class BoundedRead(NamedTuple):
    target: str | None


class _FlagScan(NamedTuple):
    flags_seen: dict[str, str | None]
    positionals: list[str]
    unknown: bool


def _response_item_payloads(handle: BinaryIO, drop_counter: list[int]) -> Iterator[dict[str, Any]]:
    """Stream response-item payloads, counting JSONL-parse drops on *drop_counter*.

    Previously this function silently continued past UnicodeDecodeError /
    json.JSONDecodeError, so corpus corruption produced no observable signal.
    The drop count is now exposed via RolloutRecords.malformed_lines so operators
    can detect a corpus-corruption spike. ``drop_counter`` is a single-element
    list used as a mutable cell — generator-local mutation propagates to the
    caller without changing the iterator's return type.
    """
    for raw in handle:
        if not raw.strip():
            continue
        try:
            record = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError):
            drop_counter[0] += 1
            continue
        if not isinstance(record, dict) or record.get("type") != "response_item":
            continue
        payload = record.get("payload")
        if isinstance(payload, dict):
            yield payload


def _message_text(payload: dict[str, Any]) -> str:
    content = payload.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            block["text"]
            for block in content
            if isinstance(block, dict) and isinstance(block.get("text"), str)
        )
    return ""


def _extract_function_call_cmd_with_diagnostic(
    raw_args: Any,
) -> _FunctionCallExtraction:
    """Extract a command string from function-call arguments, distinguishing
    the absent-cmd case from a corrupted-JSON-arguments case.

    Returns _FunctionCallExtraction.cmd=None for both cases, but sets
    ``malformed_args=True`` only when the arguments failed to parse as JSON —
    so aggregation can surface corrupted rollouts separately from legitimately
    missing cmd fields.
    """
    if not raw_args or not isinstance(raw_args, str):
        return _FunctionCallExtraction(cmd=None, malformed_args=False)
    try:
        parsed = json.loads(raw_args)
    except json.JSONDecodeError:
        return _FunctionCallExtraction(cmd=None, malformed_args=True)
    cmd = parsed.get("cmd") if isinstance(parsed, dict) else None
    return _FunctionCallExtraction(
        cmd=cmd if isinstance(cmd, str) else None,
        malformed_args=False,
    )


class _FunctionCallExtraction(NamedTuple):
    """Distinct outcomes of extracting a cmd from function-call arguments."""

    cmd: str | None
    malformed_args: bool


def _exec_call_commands(js: str) -> tuple[list[str], int]:
    """Extract ``tools.exec_command({...})`` literal calls from a JS template string.

    Returns ``(commands, unresolved)`` where ``unresolved`` counts the number
    of exec-shaped records we could not turn into a command. Note the
    asymmetry: when *no* exec-shaped record is found, we still return 1,
    representing the input record as a whole being unresolvable. When one
    or more records are found, the count reflects the unparseable subset.
    Operators reading the report should treat ``unresolved`` as
    'records we could not extract a cmd from' rather than 'commands
    we could not parse' — the latter would require a different counting rule.
    """
    calls = list(_EXEC_CALL_PAT.finditer(js))
    if not calls:
        # No exec-shaped record at all: the input record itself is unresolvable.
        return [], 1
    commands: list[str] = []
    unresolved = 0
    for index, call in enumerate(calls):
        end = calls[index + 1].start() if index + 1 < len(calls) else len(js)
        match = _EXEC_CMD_PAT.search(js, call.end(), end)
        if match is None:
            unresolved += 1
            continue
        try:
            cmd = json.loads(match.group(1))
        except json.JSONDecodeError:
            unresolved += 1
            continue
        if isinstance(cmd, str) and cmd:
            commands.append(cmd)
        else:
            unresolved += 1
    return commands, unresolved


def read_rollout(path: Path) -> RolloutRecords:
    commands: list[str] = []
    policy_versions: set[int] = set()
    unclassified = 0
    malformed_lines_cell: list[int] = [0]
    with _logical_rollout_reader(path) as handle:
        for payload in _response_item_payloads(handle, malformed_lines_cell):
            payload_type = payload.get("type")
            if payload_type == "message" and payload.get("role") != "assistant":
                policy_versions.update(parse_intake_discipline_versions(_message_text(payload)))
            elif payload_type == "function_call" and payload.get("name") == "exec_command":
                extracted = _extract_function_call_cmd_with_diagnostic(payload.get("arguments"))
                if extracted.cmd:
                    commands.append(extracted.cmd)
                elif extracted.malformed_args:
                    unclassified += 1
                else:
                    unclassified += 1
            elif payload_type == "custom_tool_call" and payload.get("name") == "exec":
                raw_input = payload.get("input")
                if not isinstance(raw_input, str):
                    unclassified += 1
                else:
                    js_commands, js_unresolved = _exec_call_commands(raw_input)
                    commands.extend(js_commands)
                    unclassified += js_unresolved
    return RolloutRecords(
        commands,
        frozenset(policy_versions),
        unclassified,
        malformed_lines_cell[0],
    )


def _is_boolean_cluster(token: str, spec: Mapping[str, _FlagArity]) -> bool:
    return (
        len(token) > 2
        and token.startswith("-")
        and not token.startswith("--")
        and all(spec.get(f"-{letter}") == _FlagArity.BOOLEAN for letter in token[1:])
    )


def _leading_argv(segments: list[_CommandSegment]) -> list[str]:
    # cwd="" is the documented sentinel meaning "do not anchor redirect targets
    # against any path" — see resolve_write_target's ``if cwd:`` short-circuit.
    # Required because _partition_output_redirects' cwd parameter has no default.
    argv = _partition_output_redirects(
        segments[0].tokens,
        cwd="",
        redirect_syntax=segments[0].redirect_syntax,
    )[0]
    return argv[1:] if argv and argv[0] == "{" else argv


def _scan_flags(spec: Mapping[str, _FlagArity], args: list[str]) -> _FlagScan:
    flags_seen: dict[str, str | None] = {}
    positionals: list[str] = []
    unknown = False
    options_done = False
    i = 0
    while i < len(args):
        token = args[i]
        if token == "--" and not options_done:
            options_done = True
            i += 1
            continue
        if options_done or token == "-" or not token.startswith("-"):
            positionals.append(token)
            i += 1
            continue
        value, next_i, recognized = _consume_str_flag(args, i, spec)
        if recognized:
            flags_seen[_spec_key_for_token(token, spec)] = value
            i = next_i
        elif _is_boolean_cluster(token, spec):
            for letter in token[1:]:
                flags_seen[f"-{letter}"] = None
            i += 1
        else:
            unknown = True
            i += 1
    return _FlagScan(flags_seen, positionals, unknown)


def _bounded_files(
    verb: str, scan: _FlagScan, segments: list[_CommandSegment]
) -> list[str] | None:
    flags = scan.flags_seen
    positionals = scan.positionals
    match verb:
        case "sed":
            if not any(flag in flags for flag in ("-n", "--quiet", "--silent")):
                return None
            expression_flags = ("-e", "-f", "--expression", "--file")
            return (
                positionals if any(flag in flags for flag in expression_flags) else positionals[1:]
            )
        case "head" | "tail":
            count_flags = ("-n", "-c", "--lines", "--bytes")
            if any(flag in flags for flag in count_flags) and positionals:
                return positionals
            return None
        case "nl":
            body_numbering = flags.get("-b", flags.get("--body-numbering"))
            if (
                body_numbering == "a"
                and len(segments) > 1
                and segments[1].piped_from_previous
                and segments[1].tokens[:2] == ["sed", "-n"]
            ):
                return positionals
            return None
        case "rg":
            if "-n" not in flags and "--line-number" not in flags:
                return None
            # Flags whose next token is a search pattern to be skipped from
            # positionals (so it is not misidentified as the file target).
            # Includes both --files and --files-with-matches: each makes rg
            # print file names instead of pattern output, so any following
            # positional is a path, not a pattern.
            pattern_flags = ("-e", "-f", "--regexp", "--file", "--files", "--files-with-matches")
            return positionals if any(flag in flags for flag in pattern_flags) else positionals[1:]
        case _:
            return None


def classify_command(cmd: str) -> BoundedRead | None:
    segments = _tokenize_command_segments_with_redirects(cmd)
    if not segments:
        return None
    argv = _leading_argv(segments)
    if not argv or argv[0] not in _READ_VERBS:
        return None
    verb = argv[0]
    scan = _scan_flags(_READ_VERBS[verb], argv[1:])
    files = _bounded_files(verb, scan, segments)
    if files is None:
        return None
    if len(files) == 1 and not scan.unknown:
        target = files[0]
        if target != "-" and "$" not in target and "`" not in target:
            return BoundedRead(target)
    return BoundedRead(None)


def measure_rollout(rollout_path: Path) -> dict[str, Any]:
    try:
        records = read_rollout(rollout_path)
    except Exception as exc:  # noqa: BLE001 - operator-safety net: never abort a batch
        # Preserve error type/message so operators investigating unreadable rollouts
        # can distinguish 'truncated file' from 'permission denied' from 'zstd
        # decompression failed'. Previously a narrow (OSError, RuntimeError,
        # ZstdError) tuple swallowed these into a single unreadable=True boolean,
        # while every other exception type (e.g., ValueError on a corrupt header)
        # propagated and aborted the entire batch run.
        return {
            "session": rollout_path.name,
            "unreadable": True,
            "error_type": type(exc).__name__,
            "error_message": str(exc),
        }
    if len(records.policy_versions) == 1:
        cohort = f"v{next(iter(records.policy_versions))}"
    elif records.policy_versions:
        cohort = "mixed"
    else:
        cohort = "none"
    target_counts: dict[str, int] = {}
    bounded_reads = 0
    repeat_reads = 0
    unresolved_targets = 0
    for cmd in records.commands:
        result = classify_command(cmd)
        if result is None:
            continue
        bounded_reads += 1
        if result.target is None:
            unresolved_targets += 1
            continue
        target_counts[result.target] = target_counts.get(result.target, 0) + 1
        if target_counts[result.target] > 1:
            repeat_reads += 1
    return {
        "session": rollout_path.name,
        "cohort": cohort,
        "exec_commands": len(records.commands),
        "bounded_reads": bounded_reads,
        "repeat_reads": repeat_reads,
        "unresolved_targets": unresolved_targets,
        "unclassified": records.unclassified,
        "worst_paths": sorted(target_counts.items(), key=lambda item: -item[1])[:5],
    }


def aggregate_report(rollout_paths: list[Path]) -> dict[str, Any]:
    cohorts: dict[str, dict[str, Any]] = {}
    worst_overall: list[tuple[str, str, int]] = []
    total_unclassified = 0
    unreadable_rollout_count = 0
    unreadable_errors: list[dict[str, str]] = []
    for path in rollout_paths:
        row = measure_rollout(path)
        if row.get("unreadable"):
            unreadable_rollout_count += 1
            unreadable_errors.append(
                {
                    "session": row["session"],
                    "error_type": row.get("error_type", "Unknown"),
                    "error_message": row.get("error_message", ""),
                }
            )
            continue
        agg = cohorts.setdefault(
            row["cohort"],
            {
                "session_count": 0,
                "bounded_read_count": 0,
                "repeat_count": 0,
                "unresolved_target_count": 0,
            },
        )
        agg["session_count"] += 1
        agg["bounded_read_count"] += row["bounded_reads"]
        agg["repeat_count"] += row["repeat_reads"]
        agg["unresolved_target_count"] += row["unresolved_targets"]
        total_unclassified += row["unclassified"]
        for path_name, count in row["worst_paths"]:
            if count > 1:
                worst_overall.append((row["session"], path_name, count))
    for cohort_stats in cohorts.values():
        bounded = cohort_stats["bounded_read_count"]
        cohort_stats["repeat_read_rate"] = (
            cohort_stats["repeat_count"] / bounded if bounded else None
        )
    worst_overall.sort(key=lambda row: -row[2])
    return {
        "cohorts": cohorts,
        "worst_offenders": worst_overall[:20],
        "unclassified_record_count": total_unclassified,
        "unreadable_rollout_count": unreadable_rollout_count,
        "unreadable_errors": unreadable_errors,
    }


def _date_within_bound(date: str, bound: str, *, is_lower: bool) -> bool:
    """Compare a day-precision date against a since/until bound of either precision.

    A month-precision bound (`YYYY-MM`) is matched against the date's month only, so
    every day in that month satisfies it. A day-precision bound (`YYYY-MM-DD`)
    compares in full, per the inclusive-boundary contract documented on --since/--until.
    """
    if len(bound) == len("YYYY-MM"):
        date = date[: len(bound)]
    return date >= bound if is_lower else date <= bound


def _find_rollouts(log_root: Path, since: str | None, until: str | None) -> list[Path]:
    paths = list(_rollout_files(log_root))
    if since is None and until is None:
        return paths
    filtered = []
    for path in paths:
        match = _ROLLOUT_DATE_PAT.search(path.name)
        date = match.group(1) if match else None
        if date is not None:
            if since is not None and not _date_within_bound(date, since, is_lower=True):
                continue
            if until is not None and not _date_within_bound(date, until, is_lower=False):
                continue
        filtered.append(path)
    return filtered


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--log-root", type=Path, default=None)
    parser.add_argument("--since", default=None, help="Inclusive date prefix, e.g. 2026-07-18")
    parser.add_argument("--until", default=None, help="Inclusive date prefix, e.g. 2026-07-24")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args(argv)

    log_root = args.log_root or default_log_dir() / CODEX_SESSIONS_SUBDIR
    rollouts = _find_rollouts(log_root, args.since, args.until)
    report = aggregate_report(rollouts)
    report["rollouts_scanned"] = len(rollouts)
    report["log_root"] = str(log_root)
    bounded_reads = sum(cohort["bounded_read_count"] for cohort in report["cohorts"].values())
    outcome = "PASS" if bounded_reads else "NO_DATA"
    report["outcome"] = outcome
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2))
    if outcome == "NO_DATA":
        print(
            f"CODEX_READ_REPETITION=NO_DATA rollouts={len(rollouts)} out={args.out}",
            file=sys.stderr,
        )
        return 1
    print(f"CODEX_READ_REPETITION=PASS rollouts={len(rollouts)} out={args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
