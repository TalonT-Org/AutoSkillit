"""Tests for tool classification sets (HEADLESS/EVIDENCE_READER/FREE_RANGE)."""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.layer("core"), pytest.mark.small]


def test_headless_tools_contains_expected_names() -> None:
    from autoskillit.core.types import HEADLESS_TOOLS

    assert HEADLESS_TOOLS == {
        "test_check",
        "unlock_agent_pack",
        "commit_files",
        "post_pr_review",
        "write_audit_semantic_result",
        "write_standalone_audit_evidence",
        "write_audit_disposition_bundle",
        "delegate_evidence_reader",
    }


def test_evidence_reader_tools_are_exact_internal_subset() -> None:
    import autoskillit.core as core
    import autoskillit.core.types as core_types
    from autoskillit.core.types import (
        ALL_VISIBILITY_TAGS,
        EVIDENCE_READER_TOOLS,
        FREE_RANGE_TOOLS,
        GATED_TOOLS,
        HEADLESS_TOOLS,
        TOOL_SUBSET_TAGS,
    )

    assert EVIDENCE_READER_TOOLS == frozenset(
        {
            "read_authorized_artifact",
            "get_authorized_artifact_page",
        }
    )
    assert EVIDENCE_READER_TOOLS <= GATED_TOOLS
    assert EVIDENCE_READER_TOOLS.isdisjoint(FREE_RANGE_TOOLS | HEADLESS_TOOLS)
    assert "delegate_evidence_reader" in HEADLESS_TOOLS
    assert "delegate_evidence_reader" not in GATED_TOOLS
    assert "delegate_evidence_reader" not in FREE_RANGE_TOOLS
    assert "evidence-reader" in ALL_VISIBILITY_TAGS
    assert {tool_name: TOOL_SUBSET_TAGS[tool_name] for tool_name in EVIDENCE_READER_TOOLS} == {
        "read_authorized_artifact": frozenset({"evidence-reader"}),
        "get_authorized_artifact_page": frozenset({"evidence-reader"}),
    }
    assert core.EVIDENCE_READER_TOOLS is EVIDENCE_READER_TOOLS
    assert core_types.EVIDENCE_READER_TOOLS is EVIDENCE_READER_TOOLS


def test_free_range_tools_contains_expected_names() -> None:
    from autoskillit.core.types import FREE_RANGE_TOOLS

    assert FREE_RANGE_TOOLS == {
        "open_kitchen",
        "close_kitchen",
        "disable_quota_guard",
        "enable_exploration",
        "reload_session",
        "configure_fleet",
        "configure_order",
        "lock_ingredients",
        "declare_join_batch",
    }
