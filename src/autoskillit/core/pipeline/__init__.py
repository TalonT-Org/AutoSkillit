"""IL-0 pipeline tracker authority, tool-sequence analysis, execution marker,
and step-context primitives.

Exposes the canonical public surface of ``pipeline_tracker``,
``tool_sequence_analysis``, ``_execution_marker``, and ``_step_context``
through the ``autoskillit.core.pipeline`` namespace. Backward-compat
shims at ``core/pipeline_tracker.py``, ``core/tool_sequence_analysis.py``,
``core/_execution_marker.py``, and ``core/_step_context.py`` preserve old
import paths (issue #4671 Phase B decomposition).
"""

from __future__ import annotations

from autoskillit.core.pipeline._execution_marker import execution_marker
from autoskillit.core.pipeline._step_context import current_order_id, current_step_name
from autoskillit.core.pipeline.pipeline_tracker import (
    TrackerAuthorityReadResult,
    TrackerAuthorityTarget,
    TrackerParticipantKey,
    initialize_kitchen_tracker,
    initialize_manual_tracker,
    mutate_tracker,
    pipeline_tracker_directory,
    pipeline_tracker_path,
    read_tracker_authority,
    release_tracker_lease,
    retain_tracker_lease,
    tracker_lease_path,
    try_retire_tracker,
)
from autoskillit.core.pipeline.tool_sequence_analysis import (
    DFG,
    AnalysisResult,
    AssistantTurn,
    GapStats,
    TurnSequence,
    build_dfg,
    build_dfg_by_recipe,
    compute_analysis,
    compute_gap_stats,
    filter_sessions_by_recipe,
    format_top_bigrams,
    iter_merged_assistant_turns,
    parse_raw_cc_jsonl,
    parse_sessions_from_summary_dir,
    render_adjacency_table,
    render_dot,
    render_mermaid,
)

__all__ = [
    "AnalysisResult",
    "AssistantTurn",
    "DFG",
    "GapStats",
    "TrackerAuthorityReadResult",
    "TrackerAuthorityTarget",
    "TrackerParticipantKey",
    "TurnSequence",
    "build_dfg",
    "build_dfg_by_recipe",
    "compute_analysis",
    "compute_gap_stats",
    "current_order_id",
    "current_step_name",
    "execution_marker",
    "filter_sessions_by_recipe",
    "format_top_bigrams",
    "initialize_kitchen_tracker",
    "initialize_manual_tracker",
    "iter_merged_assistant_turns",
    "mutate_tracker",
    "parse_raw_cc_jsonl",
    "parse_sessions_from_summary_dir",
    "pipeline_tracker_directory",
    "pipeline_tracker_path",
    "read_tracker_authority",
    "release_tracker_lease",
    "render_adjacency_table",
    "render_dot",
    "render_mermaid",
    "retain_tracker_lease",
    "tracker_lease_path",
    "try_retire_tracker",
]
