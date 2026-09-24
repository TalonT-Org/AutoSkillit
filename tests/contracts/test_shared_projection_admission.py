"""The shared plugin projection is session-invariant across launch attestations.

Launch-bound managed-join evidence admits skills in the per-launch session catalog.
The shared projection accepts no such evidence: it defers evidence-dependent skills
to the attested generated home instead of refusing them once per launch.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import structlog

from autoskillit.core import (
    SkillExecutionRole,
    SkillVisibilitySpec,
    managed_home_for,
)
from autoskillit.execution.backends import CodexBackend
from autoskillit.workspace import (
    DefaultSkillResolver,
    compile_session_skill_catalog,
    project_default_plugin_authority,
)
from tests._helpers import _flush_structlog_proxy_caches
from tests.contracts._skill_admission_ledger import _production_managed_codex_context

pytestmark = [pytest.mark.layer("contracts"), pytest.mark.medium]


def test_shared_projection_partitions_deferrals_and_is_attestation_invariant(
    tmp_path: Path,
) -> None:
    source_catalog = DefaultSkillResolver().list_effective(
        None,
        SkillExecutionRole.SESSION,
        visibility=SkillVisibilitySpec(),
        cook_session=True,
    )
    ctx_a = _production_managed_codex_context(parent_session_id="launch-a")
    ctx_b = _production_managed_codex_context(parent_session_id="launch-b")
    assert ctx_a.digest != ctx_b.digest

    compiled_a = compile_session_skill_catalog(
        source_catalog, CodexBackend(), adaptation_context=ctx_a
    )
    compiled_b = compile_session_skill_catalog(
        source_catalog, CodexBackend(), adaptation_context=ctx_b
    )
    compiled_unattested = compile_session_skill_catalog(
        source_catalog, CodexBackend(), adaptation_context=None
    )

    plans = {}
    logs_by_launch = {}
    for label, compiled in (("a", compiled_a), ("b", compiled_b)):
        authority = project_default_plugin_authority(
            home=managed_home_for(tmp_path / f"home-{label}"),
            base_branch=None,
            catalog=compiled.catalog,
            cwd=tmp_path,
        )
        _flush_structlog_proxy_caches()
        try:
            with structlog.testing.capture_logs() as logs:
                plans[label] = authority._plan(CodexBackend())
        finally:
            _flush_structlog_proxy_caches()
        logs_by_launch[label] = logs

    plan_a, plan_b = plans["a"], plans["b"]
    assert plan_a.semantic_key == plan_b.semantic_key

    admitted = {skill.name for skill in plan_a.catalog.skills}
    assert admitted == {skill.name for skill in compiled_unattested.catalog.skills}
    attested = {skill.name for skill in compiled_a.catalog.skills}
    assert admitted <= attested
    assert attested - admitted == {deferral.skill for deferral in plan_a.deferred}
    assert plan_a.deferred
    assert plan_a.unavailable == ()

    logs = logs_by_launch["a"]
    assert not [
        entry for entry in logs if entry.get("event") == "projected_plugin_skill_unavailable"
    ]
    assert not [entry for entry in logs if entry.get("log_level") == "warning"]
    deferred_events = [
        entry
        for entry in logs
        if entry.get("event") == "projected_plugin_skills_deferred_to_session"
    ]
    assert len(deferred_events) == 1
    assert deferred_events[0]["log_level"] == "debug"
    assert deferred_events[0]["count"] == len(plan_a.deferred)
