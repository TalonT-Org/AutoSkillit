"""Tests for scripts/measure_codex_read_repetition.py (#4351)."""

from __future__ import annotations

import importlib.util
import json
from datetime import date
from pathlib import Path
from types import ModuleType

import pytest
import zstandard

from autoskillit.core import CODEX_INTAKE_DISCIPLINE_VERSION, render_intake_digest

pytestmark = pytest.mark.small

SCRIPT = Path(__file__).parents[2] / "scripts" / "measure_codex_read_repetition.py"


def _load_measurer() -> ModuleType:
    spec = importlib.util.spec_from_file_location("codex_read_repetition", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


measurer = _load_measurer()


def _exec(cmd: str) -> dict:
    return {
        "type": "response_item",
        "payload": {
            "type": "function_call",
            "name": "exec_command",
            "arguments": json.dumps({"cmd": cmd}),
        },
    }


def _exec_js(js: str) -> dict:
    return {
        "type": "response_item",
        "payload": {"type": "custom_tool_call", "name": "exec", "input": js},
    }


def _developer(text: str) -> dict:
    return {
        "type": "response_item",
        "payload": {
            "type": "message",
            "role": "developer",
            "content": [{"type": "input_text", "text": text}],
        },
    }


def _tool_output(text: str) -> dict:
    return {
        "type": "response_item",
        "payload": {"type": "custom_tool_call_output", "output": text},
    }


def _native_path(root: Path, day: date, thread: str, suffix: str = ".jsonl") -> Path:
    # CodexSessionStore promotion preserves Codex's native YYYY/MM/DD rollout layout.
    return (
        root
        / day.strftime("%Y")
        / day.strftime("%m")
        / day.strftime("%d")
        / f"rollout-{day.isoformat()}T10-00-00-{thread}{suffix}"
    )


def _write_records(path: Path, records: list[dict]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    contents = "".join(json.dumps(record) + "\n" for record in records).encode("utf-8")
    if path.suffix == ".zst":
        path.write_bytes(zstandard.ZstdCompressor().compress(contents))
    else:
        path.write_bytes(contents)
    return path


def test_discovery_is_depth_agnostic_and_includes_zst(tmp_path: Path) -> None:
    root = tmp_path / "codex-sessions"
    depth_1 = root / "2026" / "rollout-depth-one.jsonl"
    depth_2 = root / "2026" / "07" / "rollout-depth-two.jsonl"
    depth_3 = _native_path(root, date(2026, 7, 15), "depth-three")
    depth_4 = root / "2026" / "07" / "15" / "nested" / "rollout-depth-four.jsonl"
    compressed = _native_path(root, date(2026, 7, 16), "compressed", suffix=".jsonl.zst")
    rollouts = [depth_1, depth_2, depth_3, depth_4, compressed]
    for path in rollouts[:-1]:
        _write_records(path, [])
    _write_records(compressed, [_exec("sed -n '1,5p' a.py")])
    (root / "run-skill-in-progress-x.marker").write_text("incomplete", encoding="utf-8")

    assert measurer._find_rollouts(root, None, None) == sorted(rollouts)
    assert measurer.read_rollout(compressed).commands == ["sed -n '1,5p' a.py"]


@pytest.mark.parametrize("root_state", ["missing", "empty"])
def test_main_reports_no_data_for_missing_or_empty_root(
    root_state: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "codex-sessions"
    if root_state == "empty":
        root.mkdir()
    out = tmp_path / "report.json"

    assert measurer.main(["--log-root", str(root), "--out", str(out)]) == 1
    captured = capsys.readouterr()
    assert "CODEX_READ_REPETITION=NO_DATA" in captured.err
    assert "=PASS" not in captured.out
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["outcome"] == "NO_DATA"
    assert report["rollouts_scanned"] == 0


def test_main_reports_no_data_when_nothing_is_a_bounded_read(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "codex-sessions"
    rollout = _native_path(root, date(2026, 7, 15), "no-bounded-read")
    _write_records(rollout, [_exec("gh pr view 1 | head -c 100")])
    out = tmp_path / "report.json"

    assert measurer.main(["--log-root", str(root), "--out", str(out)]) == 1
    captured = capsys.readouterr()
    assert "CODEX_READ_REPETITION=NO_DATA" in captured.err
    assert "=PASS" not in captured.out


def test_main_scans_every_rollout_in_the_native_tree(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "codex-sessions"
    rollouts = [
        _native_path(root, date(2026, 7, 15), "session-one"),
        _native_path(root, date(2026, 7, 15), "session-two"),
        _native_path(root, date(2026, 7, 16), "session-three"),
    ]
    for path in rollouts:
        _write_records(path, [_exec("sed -n '1,5p' a.py")])
    out = tmp_path / "report.json"

    assert measurer.main(["--log-root", str(root), "--out", str(out)]) == 0
    captured = capsys.readouterr()
    assert "CODEX_READ_REPETITION=PASS rollouts=3 " in captured.out
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["rollouts_scanned"] == len(list(root.rglob("rollout-*")))


def test_date_filter_uses_filename_day_precision(tmp_path: Path) -> None:
    root = tmp_path / "codex-sessions"
    days = [
        date(2026, 6, 15),
        date(2026, 7, 1),
        date(2026, 7, 15),
        date(2026, 7, 31),
        date(2026, 8, 5),
    ]
    rollouts = [_write_records(_native_path(root, day, day.isoformat()), []) for day in days]
    june, july_01, july_15, july_31, august = rollouts

    assert measurer._find_rollouts(root, "2026-07", "2026-07") == [
        july_01,
        july_15,
        july_31,
    ]
    assert measurer._find_rollouts(root, "2026-07-18", None) == [july_31, august]
    assert measurer._find_rollouts(root, None, "2026-07-18") == [
        june,
        july_01,
        july_15,
    ]
    assert measurer._find_rollouts(root, "2026-07-15", "2026-07-15") == [july_15]


@pytest.mark.parametrize(
    "version", (1, 2, CODEX_INTAKE_DISCIPLINE_VERSION, CODEX_INTAKE_DISCIPLINE_VERSION + 1)
)
def test_cohort_is_the_rendered_policy_version(version: int, tmp_path: Path) -> None:
    path = _native_path(tmp_path, date(2026, 7, 15), f"policy-v{version}")
    _write_records(
        path,
        [_developer(render_intake_digest(version=version)), _exec("sed -n '1,5p' a.py")],
    )

    assert measurer.measure_rollout(path)["cohort"] == f"v{version}"


def test_cohort_is_scoped_to_injected_messages(tmp_path: Path) -> None:
    current_version = CODEX_INTAKE_DISCIPLINE_VERSION
    with_output = _native_path(tmp_path, date(2026, 7, 15), "tool-output")
    _write_records(
        with_output,
        [
            _developer(render_intake_digest()),
            _tool_output(render_intake_digest(version=1)),
            _exec("sed -n '1,5p' a.py"),
        ],
    )
    output_only = _native_path(tmp_path, date(2026, 7, 15), "output-only")
    _write_records(output_only, [_tool_output(render_intake_digest(version=1))])
    mixed = _native_path(tmp_path, date(2026, 7, 15), "mixed")
    _write_records(
        mixed,
        [
            _developer(render_intake_digest(version=2)),
            _developer(render_intake_digest(version=3)),
        ],
    )

    assert measurer.measure_rollout(with_output)["cohort"] == f"v{current_version}"
    assert measurer.measure_rollout(output_only)["cohort"] == "none"
    assert measurer.measure_rollout(mixed)["cohort"] == "mixed"


_CLASSIFICATION_CASES = [
    ("sed -n '1,10p' /a/b.py", True, "/a/b.py"),
    ("sed -n 1,10p a.py > out.txt", True, "a.py"),
    ("{ sed -n '1,20p' a.py; } 2>&1 | head -c 100", True, "a.py"),
    ("sed -n -e '1,5p' a.py", True, "a.py"),
    ("sed -n '1,5p' \"$f\"", True, None),
    ("head -n 50 src/x.py", True, "src/x.py"),
    ("tail -c 2000 log.txt 2>/dev/null", True, "log.txt"),
    ("nl -ba src/x.py | sed -n '1,40p'", True, "src/x.py"),
    ("rg -n 'foo|bar' src/autoskillit/file.py", True, "src/autoskillit/file.py"),
    (
        'rg -n "foo|bar|baz" src/autoskillit/file.py',
        True,
        "src/autoskillit/file.py",
    ),
    (
        "rg -n 'foo|bar' src/autoskillit/file.py | head -c 18000",
        True,
        "src/autoskillit/file.py",
    ),
    ("rg -n -M 500 'a|b' src/x.py 2>&1 | head -c 5000", True, "src/x.py"),
    ("rg -n -M 500 'pat' \"$file\"", True, None),
    ("rg -M 500 -n pat a.py", True, "a.py"),
    ("rg -n -M500 pat a.py", True, "a.py"),
    ("rg -n --max-columns=500 pat a.py", True, "a.py"),
    ("rg -n -C 3 -g '*.py' pat a.py", True, "a.py"),
    ("rg -n -A 3 -B 2 pat a.py", True, "a.py"),
    ("rg -nuu pat a.py", True, "a.py"),
    ("rg -n -e foo -e bar a.py", True, "a.py"),
    ("rg -n pat", True, None),
    ("rg -n pat a.py b.py", True, None),
    ("rg -n --no-such-flag pat a.py", True, None),
    ("gh pr view 123 | head -c 18000", False, None),
    ("head -c 100", False, None),
    ("set -o pipefail; rg -n x a.py", False, None),
    ("rg -n 'unbalanced", False, None),
]


def _unquoted_pipe(cmd: str) -> bool:
    """Return True if *cmd* contains a shell pipe (`|`) outside of any quote.

    Plain `"|" in cmd` would falsely flag ripgrep regex alternation like
    `rg -n 'foo|bar' ...` (the `|` is inside single quotes — it's a regex
    alternation, not a shell pipe). Walk the string tracking quote state.
    """
    in_single = False
    in_double = False
    for index, char in enumerate(cmd):
        if char == "\\" and index + 1 < len(cmd):
            # Skip the escaped char regardless of quote state.
            continue
        if char == "'" and not in_double:
            in_single = not in_single
        elif char == '"' and not in_single:
            in_double = not in_double
        elif char == "|" and not in_single and not in_double:
            return True
    return False


def _classification_case_ids() -> list[str]:
    """Build descriptive pytest parametrize IDs for _CLASSIFICATION_CASES.

    A single failing case shows up as ``sed-redirect`` or ``rg-pipe`` rather
    than ``case-19`` — the offending shell shape is right there in the
    failure report, no need to scroll back to the parametrization site.

    Caveats baked into the labels:
      - `>` triggers `redirect` even when it's a stderr redirect like
        `2>/dev/null`; this is acceptable because stderr redirect does
        affect classification, just not the way the test asserts.
      - Pipe detection uses _unquoted_pipe so ripgrep regex `|` does NOT
        trigger `pipe` (e.g. `rg -n 'foo|bar' a.py` is `rg`, not `rg-pipe`).
    """
    ids: list[str] = []
    seen: dict[str, int] = {}
    for cmd, bounded, target in _CLASSIFICATION_CASES:
        verb = cmd.lstrip(" {").split()[0] if cmd.lstrip() else "empty"
        has_redirect = ">" in cmd
        has_pipe = _unquoted_pipe(cmd)
        shape_bits = [verb]
        if has_pipe:
            shape_bits.append("pipe")
        if has_redirect:
            shape_bits.append("redirect")
        if target is None and bounded:
            shape_bits.append("unresolved-target")
        if not bounded:
            shape_bits.append("unbounded")
        base = "-".join(shape_bits)
        # Disambiguate colliding IDs (e.g. two distinct sed-redirect cases).
        if base in seen:
            seen[base] += 1
            ids.append(f"{base}-{seen[base]}")
        else:
            seen[base] = 1
            ids.append(base)
    return ids


@pytest.mark.parametrize(
    ("cmd", "bounded", "target"),
    _CLASSIFICATION_CASES,
    ids=_classification_case_ids(),
)
def test_classify_command(cmd: str, bounded: bool, target: str | None) -> None:
    result = measurer.classify_command(cmd)
    assert (result is not None) is bounded
    if bounded:
        assert result == measurer.BoundedRead(target)


def _value_flag_commands() -> list[str]:
    # Multi-value flags where the "value" is itself a search pattern or path that
    # rg already consumes positionally — skipping them here avoids re-testing the
    # "next token is a pattern" branch (covered by the rg-pipe-alternation cases
    # in _CLASSIFICATION_CASES). Without this carve-out the loop would test
    # `rg -n -e pat 7 pat a.py`, which fails for reasons unrelated to value-flag
    # consumption.
    omitted_rg_flags = {"-e", "--regexp", "-f", "--file"}
    rg_flags = [
        flag
        for flag, arity in measurer._READ_VERBS["rg"].items()
        if arity == measurer._FlagArity.VALUE and flag not in omitted_rg_flags
    ]
    head_flags = [
        flag
        for flag, arity in measurer._READ_VERBS["head"].items()
        if arity == measurer._FlagArity.VALUE
    ]
    return [
        *(f"rg -n {flag} 7 pat a.py" for flag in rg_flags),
        *(f"head -n 5 {flag} 7 a.py" for flag in head_flags),
    ]


def test_every_value_flag_consumes_its_value() -> None:
    for command in _value_flag_commands():
        assert measurer.classify_command(command) == measurer.BoundedRead("a.py"), command


def test_no_resolved_target_is_purely_numeric() -> None:
    commands = [case[0] for case in _CLASSIFICATION_CASES] + _value_flag_commands()
    classifications = [measurer.classify_command(command) for command in commands]
    bounded_reads = [c for c in classifications if isinstance(c, measurer.BoundedRead)]

    # Lock classify_command's return contract: every outcome is BoundedRead or
    # None. Iterating over the unfiltered list (not bounded_reads) makes the
    # check non-vacuous — bounded_reads is already filtered by isinstance, so
    # re-checking it here would silently always pass.
    assert all(isinstance(c, (measurer.BoundedRead, type(None))) for c in classifications), (
        f"unexpected classification outcome: {classifications!r}"
    )
    assert all(
        not target.isdigit() for target in (c.target for c in bounded_reads) if target is not None
    )


def test_custom_tool_call_extracts_every_literal_exec_call(tmp_path: Path) -> None:
    path = _native_path(tmp_path, date(2026, 7, 15), "custom-tool-call")
    js = r"""tools.exec_command({cmd:"sed -n '1,5p' a.py"});
tools.exec_command({"cmd":"sed -n '6,9p' \"/x y/b.py\"","workdir":"/w"});
for (const c of cmds) await tools.exec_command({cmd:c});"""
    _write_records(path, [_exec_js(js), _exec_js("text(ALL_TOOLS.slice(0,2))")])

    records = measurer.read_rollout(path)
    assert records.commands == ["sed -n '1,5p' a.py", "sed -n '6,9p' \"/x y/b.py\""]
    assert records.unclassified == 2
    assert ("/x y/b.py", 1) in measurer.measure_rollout(path)["worst_paths"]
    assert measurer.aggregate_report([path])["unclassified_record_count"] == 2


def test_unparseable_lines_are_skipped_not_fatal(tmp_path: Path) -> None:
    path = _native_path(tmp_path, date(2026, 7, 15), "corrupt-line")
    path.parent.mkdir(parents=True)
    path.write_text(
        "{not valid json\n" + json.dumps(_exec("sed -n '1,5p' a.py")) + "\n",
        encoding="utf-8",
    )

    records = measurer.read_rollout(path)
    assert records.commands == ["sed -n '1,5p' a.py"]
    assert records.unclassified == 0


def test_unreadable_rollout_is_counted_not_misfiled(tmp_path: Path) -> None:
    unreadable = tmp_path / "rollout-x.jsonl"
    unreadable.mkdir()

    report = measurer.aggregate_report([unreadable])

    assert report["unreadable_rollout_count"] == 1
    assert report["cohorts"] == {}


def test_repeat_reads_are_counted_per_session_not_per_corpus(tmp_path: Path) -> None:
    same_session = _native_path(tmp_path, date(2026, 7, 15), "same-session")
    _write_records(
        same_session,
        [
            _exec("sed -n '1,10p' /a/b.py"),
            _exec("sed -n '11,20p' /a/b.py"),
        ],
    )
    row = measurer.measure_rollout(same_session)
    assert row["bounded_reads"] == 2
    assert row["repeat_reads"] == 1

    rollout_1 = _write_records(
        _native_path(tmp_path, date(2026, 7, 16), "rollout-one"),
        [_exec("sed -n '1,10p' /a/b.py")],
    )
    rollout_2 = _write_records(
        _native_path(tmp_path, date(2026, 7, 17), "rollout-two"),
        [_exec("sed -n '1,10p' /a/b.py")],
    )
    report = measurer.aggregate_report([rollout_1, rollout_2])
    assert report["cohorts"]["none"]["bounded_read_count"] == 2
    assert report["cohorts"]["none"]["repeat_count"] == 0
