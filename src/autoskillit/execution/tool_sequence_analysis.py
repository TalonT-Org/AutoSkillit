from __future__ import annotations

import json
import pathlib
import statistics
from collections import Counter
from dataclasses import dataclass, field
from itertools import islice
from typing import Iterator, Sequence

_TOOL_USE_CAP = 8
_MAX_NGRAM_LEN = 5


@dataclass
class TurnSequence:
    session_id: str
    recipe_name: str
    turns: list[list[str]]
    timestamps: list[str] = field(default_factory=list)


@dataclass
class GapStats:
    median: float
    p25: float
    p75: float
    maximum: int


@dataclass
class DFG:
    bigrams: Counter  # type: ignore[type-arg]
    ngrams: Counter  # type: ignore[type-arg]
    pair_gaps: dict[tuple[str, str], list[int]]
    total_turns: int


@dataclass
class AnalysisResult:
    global_dfg: DFG
    by_recipe: dict[str, DFG]
    session_count: int


def parse_raw_cc_jsonl(
    jsonl_path: pathlib.Path, *, cap: int = _TOOL_USE_CAP
) -> list[list[str]]:
    """Parse a Claude Code session JSONL into per-turn tool call lists."""
    result: list[list[str]] = []
    seen: set[str] = set()
    try:
        text = jsonl_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return result
    for raw_line in text.splitlines():
        raw_line = raw_line.strip()
        if not raw_line:
            continue
        try:
            rec = json.loads(raw_line)
        except json.JSONDecodeError:
            continue
        if not isinstance(rec, dict) or rec.get("type") != "assistant":
            continue
        rid = rec.get("requestId", "")
        if rid and rid in seen:
            continue
        if rid:
            seen.add(rid)
        message = rec.get("message")
        content = message.get("content", []) if isinstance(message, dict) else []
        tools = [
            str(blk["name"])
            for blk in content
            if isinstance(blk, dict)
            and blk.get("type") == "tool_use"
            and isinstance(blk.get("name"), str)
            and blk["name"]
        ]
        result.append(tools[:cap])
    return result


def parse_sessions_from_summary_dir(log_root: pathlib.Path) -> Iterator[TurnSequence]:
    """Scan log_root/sessions/*/summary.json and load TurnSequences."""
    for summary_path in sorted(log_root.glob("sessions/*/summary.json")):
        try:
            data = json.loads(summary_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        turns = data.get("turn_tool_calls", [])
        if not turns:
            continue
        yield TurnSequence(
            session_id=summary_path.parent.name,
            recipe_name=data.get("recipe_name", ""),
            turns=turns,
            timestamps=data.get("turn_timestamps", []),
        )


def build_dfg(sessions: Sequence[TurnSequence]) -> DFG:
    """Build bigrams (cross-turn), n-grams (within-turn), and gap analysis."""
    bigrams: Counter[tuple[str, str]] = Counter()
    ngrams: Counter[tuple[str, ...]] = Counter()
    pair_gaps: dict[tuple[str, str], list[int]] = {}
    total_turns = 0

    for seq in sessions:
        total_turns += len(seq.turns)

        # Bigrams: across the flattened sequence (including cross-turn)
        flat: list[str] = []
        for turn in seq.turns:
            flat.extend(turn)
        for i in range(len(flat) - 1):
            bigrams[(flat[i], flat[i + 1])] += 1

        # N-grams: within each turn (length 2-5)
        for turn in seq.turns:
            for length in range(2, _MAX_NGRAM_LEN + 1):
                for start in range(len(turn) - length + 1):
                    ngrams[tuple(turn[start : start + length])] += 1

        # Gap analysis: for each (A, B) pair, record turn distance
        # For each turn i, for each tool A in turn i, find the next turn j > i
        # where B appears, record j - i.
        last_seen: dict[str, int] = {}
        for turn_idx, turn in enumerate(seq.turns):
            for tool in turn:
                # Check if any prior tool has been waiting for this tool
                for prev_tool, prev_turn in last_seen.items():
                    pair = (prev_tool, tool)
                    gap = turn_idx - prev_turn
                    if gap > 0:
                        pair_gaps.setdefault(pair, []).append(gap)
            for tool in turn:
                last_seen[tool] = turn_idx

    return DFG(bigrams=bigrams, ngrams=ngrams, pair_gaps=pair_gaps, total_turns=total_turns)


def build_dfg_by_recipe(sessions: Sequence[TurnSequence]) -> dict[str, DFG]:
    """Return per-recipe DFG keyed by recipe_name (empty string = unrecorded)."""
    by_recipe: dict[str, list[TurnSequence]] = {}
    for seq in sessions:
        by_recipe.setdefault(seq.recipe_name, []).append(seq)
    return {recipe: build_dfg(seqs) for recipe, seqs in by_recipe.items()}


def compute_analysis(sessions: Sequence[TurnSequence]) -> AnalysisResult:
    """Convenience wrapper: build global + per-recipe DFGs."""
    global_dfg = build_dfg(sessions)
    by_recipe = build_dfg_by_recipe(sessions)
    return AnalysisResult(
        global_dfg=global_dfg,
        by_recipe=by_recipe,
        session_count=len(sessions),
    )


def compute_gap_stats(gaps: list[int]) -> GapStats:
    """Compute median, p25, p75, max from a list of turn gaps."""
    sorted_gaps = sorted(gaps)
    n = len(sorted_gaps)
    median = statistics.median(sorted_gaps)

    def _percentile(data: list[int], pct: float) -> float:
        idx = (n - 1) * pct
        lo = int(idx)
        hi = lo + 1
        if hi >= n:
            return float(data[lo])
        frac = idx - lo
        return data[lo] + frac * (data[hi] - data[lo])

    return GapStats(
        median=float(median),
        p25=_percentile(sorted_gaps, 0.25),
        p75=_percentile(sorted_gaps, 0.75),
        maximum=sorted_gaps[-1],
    )


def _safe_id(name: str) -> str:
    """Convert a tool name to a Mermaid-safe node ID."""
    return name.replace("-", "_").replace(".", "_").replace("/", "_").replace(":", "_")


def render_mermaid(dfg: DFG, *, min_count: int = 1, top_n: int = 30) -> str:
    """Render bigram DFG as Mermaid flowchart LR. Returns the diagram string."""
    lines = ["flowchart LR"]
    top = [
        (pair, count)
        for pair, count in dfg.bigrams.most_common(top_n)
        if count >= min_count
    ]
    if not top:
        lines.append("    empty[No data]")
        return "\n".join(lines)
    for (a, b), count in top:
        aid = _safe_id(a)
        bid = _safe_id(b)
        lines.append(f'    {aid}["{a}"] -->|"{count}"| {bid}["{b}"]')
    return "\n".join(lines)


def render_adjacency_table(dfg: DFG, *, top_n: int = 20) -> str:
    """Render top-N bigrams as a plain-text ASCII table."""
    top = list(islice(dfg.bigrams.most_common(top_n), top_n))
    col_a = max((len(a) for (a, _), _ in top), default=6)
    col_b = max((len(b) for (_, b), _ in top), default=6)
    col_a = max(col_a, len("tool_a"))
    col_b = max(col_b, len("tool_b"))
    header = f"{'tool_a':<{col_a}}  {'tool_b':<{col_b}}  count"
    sep = "-" * len(header)
    rows = [header, sep]
    for (a, b), count in top:
        rows.append(f"{a:<{col_a}}  {b:<{col_b}}  {count}")
    return "\n".join(rows)


def render_dot(dfg: DFG, *, min_count: int = 1, top_n: int = 30) -> str:
    """Render bigram DFG as Graphviz DOT digraph."""
    lines = ["digraph tool_sequences {", '    rankdir="LR";']
    for (a, b), count in dfg.bigrams.most_common(top_n):
        if count < min_count:
            continue
        lines.append(f'    "{a}" -> "{b}" [label="{count}"];')
    lines.append("}")
    return "\n".join(lines)
