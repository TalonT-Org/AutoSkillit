"""Interactive review writes use the loaded skills' projected write scopes."""

from __future__ import annotations

import pytest

from autoskillit.hooks._write_scope import WriteScope, WriteScopeKind
from tests.hooks._interactive_guard_harness import (
    bind,
    make_runtime,
    manifest_entry,
    run_guard,
    temp_target,
    write_guard,
)

pytestmark = [pytest.mark.layer("skills"), pytest.mark.medium]

_REVIEW_PR = WriteScope(WriteScopeKind.BOUNDED, ("{{AUTOSKILLIT_TEMP}}/review-pr/",))


def test_review_pr_boundary_denies_outside_and_allows_inside(tmp_path) -> None:
    runtime = make_runtime(tmp_path, {"review-pr": manifest_entry(_REVIEW_PR)})
    bind(runtime, ("review-pr",))

    assert write_guard(runtime, runtime.project / "src" / "outside.py").decision == "deny"
    assert write_guard(runtime, temp_target(runtime, "review-pr/report.md")).decision == "allow"


def test_unmatched_session_id_binding_leaves_skill_scope_inactive(tmp_path) -> None:
    runtime = make_runtime(tmp_path, {"review-pr": manifest_entry(_REVIEW_PR)})
    ordinary = runtime.project / "src" / "ordinary.py"
    assert write_guard(runtime, ordinary).reason_code == "no_scope"
    protected = tmp_path / "site-packages/autoskillit/__init__.py"
    integrity = run_guard(
        runtime,
        "installation_integrity_guard.py",
        {
            "tool_name": "Write",
            "session_id": "interactive",
            "cwd": str(runtime.project),
            "tool_input": {"file_path": str(protected)},
        },
    )
    assert '"deny"' in integrity.stdout

    bind(runtime, ("review-pr",), session_id="stale")
    outcome = write_guard(runtime, ordinary)
    assert (outcome.decision, outcome.reason_code) == ("allow", "no_scope")


@pytest.mark.parametrize(
    ("second", "target_suffix", "allowed"),
    [
        pytest.param(
            WriteScope(WriteScopeKind.BOUNDED, ("{{AUTOSKILLIT_TEMP}}/other/",)),
            "other/ok.md",
            True,
            id="bounded-disjoint-admits-second",
        ),
        pytest.param(
            WriteScope(WriteScopeKind.BOUNDED, ("{{AUTOSKILLIT_TEMP}}/other/",)),
            "review-pr/ok.md",
            True,
            id="bounded-disjoint-keeps-first",
        ),
        pytest.param(
            WriteScope(WriteScopeKind.BOUNDED, ("{{AUTOSKILLIT_TEMP}}/review-pr/nested/",)),
            "review-pr/outer.md",
            True,
            id="bounded-nested-does-not-narrow",
        ),
        pytest.param(
            WriteScope(WriteScopeKind.INHERIT), "review-pr/ok.md", True, id="inherit-abstains"
        ),
        pytest.param(
            WriteScope(WriteScopeKind.UNRESTRICTED),
            "../../src/anywhere.py",
            True,
            id="unrestricted-dominates",
        ),
        pytest.param(
            WriteScope(WriteScopeKind.BOUNDED, ("{{AUTOSKILLIT_TEMP}}/other/",)),
            "elsewhere/x.md",
            False,
            id="outside-every-scope",
        ),
        pytest.param(
            WriteScope(WriteScopeKind.INHERIT),
            "elsewhere/x.md",
            False,
            id="inherit-does-not-widen",
        ),
    ],
)
def test_loaded_skill_boundaries_compose_by_union(
    tmp_path, second: WriteScope, target_suffix: str, allowed: bool
) -> None:
    runtime = make_runtime(
        tmp_path,
        {"review-pr": manifest_entry(_REVIEW_PR), "second": manifest_entry(second)},
    )
    bind(runtime, ("review-pr", "second"))

    outcome = write_guard(runtime, temp_target(runtime, target_suffix))

    assert (outcome.decision == "allow") is allowed, outcome
