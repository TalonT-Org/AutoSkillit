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
"""

from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

DEFAULT_LOG_ROOT = Path("~/.local/share/autoskillit/logs/codex-sessions").expanduser()
DEFAULT_SESSIONS_INDEX = Path("~/.local/share/autoskillit/logs/sessions.jsonl").expanduser()
DEFAULT_OUT = Path(".autoskillit/temp/codex_read_repetition_report.json")

# Leading-command bounded-read shapes. Anchored at ^ so a trailing
# `| head -c N` safety wrapper elsewhere in the command does not qualify it.
_LEADING_SED_N_PAT = re.compile(r"^\s*(?:\{\s*)?sed\s+-n\s+'[^']*'\s+\S+")
_LEADING_HEAD_PAT = re.compile(r"^\s*(?:\{\s*)?head\s+-[cn]\s*\d+\s+\S+")
_LEADING_TAIL_PAT = re.compile(r"^\s*(?:\{\s*)?tail\s+-[cn]\s*\d+\s+\S+")
_LEADING_NL_BA_PAT = re.compile(r"^\s*(?:\{\s*)?nl\s+-ba\s+\S+\s*\|\s*sed\s+-n\b")
_LEADING_RG_N_PAT = re.compile(r"^\s*(?:\{\s*)?rg\s+(?:-\S+\s+)*(-n\b|--line-number\b)")

_BOUNDED_PATTERNS = (
    _LEADING_SED_N_PAT,
    _LEADING_HEAD_PAT,
    _LEADING_TAIL_PAT,
    _LEADING_NL_BA_PAT,
    _LEADING_RG_N_PAT,
)

_PATH_TOKEN_PAT = re.compile(r"(?:[./~][\w./\-]+|[\w\-]+/[\w./\-]+)")

# The rarer custom_tool_call/exec shape embeds the command in a JS-template
# string: tools.exec_command({cmd:"..."}).
_CUSTOM_TOOL_CALL_CMD_PAT = re.compile(
    r"tools\.exec_command\(\{\s*cmd\s*:\s*[\"']((?:[^\"'\\]|\\.)*)[\"']"
)

_VERSION_V2_MARKER = "Context Intake Discipline v2:"
_VERSION_V1_MARKER = "Context Intake Discipline v1:"

_ROLLOUT_DATE_PAT = re.compile(r"(\d{4})/(\d{2})/(\d{2})/rollout-")


def is_bounded_read(cmd: str) -> bool:
    """Return True when cmd's *leading* command (before any pipe) is a bounded file read."""
    return any(pattern.search(cmd) for pattern in _BOUNDED_PATTERNS)


def extract_target_path(cmd: str) -> str | None:
    """Best-effort extraction of the file path a bounded-read command targets."""
    m = re.search(r"sed\s+-n\s+'[^']*'\s+([^\s|;]+)", cmd)
    if m:
        return m.group(1)
    m = re.search(r"\brg\s+[^|;]*?(-n\b|--line-number\b)[^|;]*", cmd)
    if m:
        # Strip a trailing `2>&1` (or `2>/dev/null`, `>&2`, ...) redirect token
        # before tokenizing -- an unstripped redirect was mis-attributed as a
        # path in 8.5% of matches during calibration.
        segment = re.sub(r"\d*[<>]&?\d*(/\S+)?\s*$", "", m.group(0)).strip()
        tokens = [t for t in segment.split() if not t.startswith("-")]
        candidates = [t for t in tokens if t != "rg" and not re.match(r"^\d*[<>]", t)]
        if candidates:
            return candidates[-1].strip("'\"")
    m = re.search(r"\b(?:head|tail)\s+-[cn]\s*\d+\s+([^\s|;]+)", cmd)
    if m:
        return m.group(1)
    m = re.search(r"\bnl\s+-ba\s+([^\s|;]+)", cmd)
    if m:
        return m.group(1)
    tokens = [t for t in _PATH_TOKEN_PAT.findall(cmd) if len(t) > 3]
    return max(tokens, key=len) if tokens else None


def classify_rollout_records(rollout_path: Path) -> tuple[list[tuple[int, str]], int]:
    """Return (exec_commands, unclassified_count) for one rollout JSONL file.

    Malformed JSONL lines are skipped, never fatal. A record that is
    exec-shaped (function_call/exec_command or custom_tool_call/exec) but
    whose command could not be extracted increments the unclassified count
    instead of silently vanishing.
    """
    commands: list[tuple[int, str]] = []
    unclassified = 0
    with rollout_path.open("r", encoding="utf-8") as f:
        for idx, line in enumerate(f):
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(rec, dict) or rec.get("type") != "response_item":
                continue
            payload = rec.get("payload")
            if not isinstance(payload, dict):
                continue
            ptype = payload.get("type")
            if ptype == "function_call" and payload.get("name") == "exec_command":
                cmd = _extract_function_call_cmd(payload.get("arguments"))
                if cmd:
                    commands.append((idx, cmd))
                else:
                    unclassified += 1
            elif ptype == "custom_tool_call" and payload.get("name") == "exec":
                raw_input = payload.get("input")
                match = (
                    _CUSTOM_TOOL_CALL_CMD_PAT.search(raw_input)
                    if isinstance(raw_input, str)
                    else None
                )
                if match:
                    commands.append((idx, match.group(1)))
                else:
                    unclassified += 1
    return commands, unclassified


def _extract_function_call_cmd(raw_args: Any) -> str | None:
    if not raw_args:
        return None
    try:
        parsed = json.loads(raw_args)
    except (json.JSONDecodeError, TypeError):
        return None
    return parsed.get("cmd") if isinstance(parsed, dict) else None


def classify_policy_cohort(rollout_path: Path) -> str:
    """Return 'v2', 'v1', or 'none' from the intake-discipline header seen on the wire."""
    text = rollout_path.read_text(encoding="utf-8", errors="ignore")
    if _VERSION_V2_MARKER in text:
        return "v2"
    if _VERSION_V1_MARKER in text:
        return "v1"
    return "none"


def measure_rollout(rollout_path: Path) -> dict[str, Any]:
    """Measure one rollout's bounded-read and repeat-read counts."""
    commands, unclassified = classify_rollout_records(rollout_path)
    seen: dict[str, int] = defaultdict(int)
    bounded = 0
    repeats = 0
    for _idx, cmd in commands:
        if not is_bounded_read(cmd):
            continue
        bounded += 1
        target = extract_target_path(cmd)
        if target is None:
            continue
        seen[target] += 1
        if seen[target] > 1:
            repeats += 1
    return {
        "session": rollout_path.name,
        "cohort": classify_policy_cohort(rollout_path),
        "exec_commands": len(commands),
        "bounded_reads": bounded,
        "repeat_reads": repeats,
        "unclassified": unclassified,
        "worst_paths": sorted(seen.items(), key=lambda kv: -kv[1])[:5],
    }


def aggregate_report(rollout_paths: list[Path]) -> dict[str, Any]:
    """Aggregate per-rollout measurements into a per-cohort report."""
    cohorts: dict[str, dict[str, Any]] = {}
    worst_overall: list[tuple[str, str, int]] = []
    total_unclassified = 0
    for path in rollout_paths:
        row = measure_rollout(path)
        agg = cohorts.setdefault(
            row["cohort"], {"session_count": 0, "bounded_read_count": 0, "repeat_count": 0}
        )
        agg["session_count"] += 1
        agg["bounded_read_count"] += row["bounded_reads"]
        agg["repeat_count"] += row["repeat_reads"]
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
    }


def _find_rollouts(log_root: Path, since: str | None, until: str | None) -> list[Path]:
    paths = sorted(log_root.glob("*/*/*/rollout-*.jsonl"))
    if since is None and until is None:
        return paths
    filtered = []
    for p in paths:
        m = _ROLLOUT_DATE_PAT.search(p.as_posix())
        date = "-".join(m.groups()) if m else None
        if date is not None:
            if since is not None and date < since:
                continue
            if until is not None and date > until:
                continue
        filtered.append(p)
    return filtered


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--log-root", type=Path, default=DEFAULT_LOG_ROOT)
    parser.add_argument("--since", default=None, help="Inclusive date prefix, e.g. 2026-07-18")
    parser.add_argument("--until", default=None, help="Inclusive date prefix, e.g. 2026-07-24")
    parser.add_argument("--sessions-index", type=Path, default=DEFAULT_SESSIONS_INDEX)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args(argv)

    rollouts = _find_rollouts(args.log_root, args.since, args.until)
    report = aggregate_report(rollouts)
    report["rollouts_scanned"] = len(rollouts)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2))
    print(f"CODEX_READ_REPETITION=PASS rollouts={len(rollouts)} out={args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
