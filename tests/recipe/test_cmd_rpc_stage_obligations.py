"""Publication gates consume pinned allocations before observing the live PR."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from autoskillit.core import PlanSetState, run_gh
from autoskillit.recipe.cmd_rpc import _cmd_rpc_merge as merge
from tests.core.types.test_plan_set_authority import _authority

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.medium]


def _sealed(tmp_path: Path, *, stages: bool = True, sealed: bool = True):
    from autoskillit.core import AllocationKind, AllocationRowDef, StageObligation

    authority = _authority(tmp_path)
    allocations = authority.allocations
    if stages:
        allocations += tuple(
            AllocationRowDef(f"P-{i}", "P1", AllocationKind.STAGE, None, obligation)
            for i, obligation in enumerate(StageObligation, 2)
        )
    authority = _authority(
        tmp_path,
        allocations=allocations,
        state=PlanSetState.SEALED if sealed else PlanSetState.OPEN,
    )
    path = tmp_path / "authority.json"
    path.write_bytes(authority.canonical_bytes)
    return path, authority.authority_digest


def _observer(monkeypatch: pytest.MonkeyPatch, observations: list[object]):
    calls = []

    def runner(cmd, **kwargs):
        calls.append((cmd, kwargs))
        value = observations[min(len(calls) - 1, len(observations) - 1)]
        if isinstance(value, BaseException):
            raise value
        if isinstance(value, subprocess.CompletedProcess):
            return value
        return subprocess.CompletedProcess(cmd, 0, json.dumps(value))

    monkeypatch.setattr(merge, "run_gh", lambda args, **kw: run_gh(args, runner=runner, **kw))
    monkeypatch.setattr(merge.time, "sleep", lambda _: None)
    return calls


def _verify(path: Path, digest: str, **kwargs):
    return getattr(merge, "verify_stage_obligations")(
        str(path), digest, str(path.parent), "main", **kwargs
    )


def test_no_stage_obligations_needs_no_observation(tmp_path, monkeypatch):
    path, digest = _sealed(tmp_path, stages=False)
    calls = _observer(monkeypatch, [])
    result = _verify(path, digest)
    assert result == {"success": "true", "detail": "no stage obligations"}
    assert calls == []


@pytest.mark.parametrize(
    ("observation", "success"),
    [
        ({"state": "OPEN", "baseRefName": "main"}, "true"),
        ({"state": "MERGED", "baseRefName": "main"}, "false"),
        ({"state": "CLOSED", "baseRefName": "main"}, "false"),
        ({"state": "OPEN", "baseRefName": "other"}, "false"),
    ],
)
def test_live_pr_state_and_base(tmp_path, monkeypatch, observation, success):
    path, digest = _sealed(tmp_path)
    calls = _observer(monkeypatch, [observation])
    result = _verify(path, digest, pr_number=42)
    assert result["success"] == success
    assert result["detail"]
    if success == "false":
        assert "pr_left_unmerged" in result["detail"]
    assert calls[0][0] == ["gh", "pr", "view", "42", "--json", "state,baseRefName"]
    assert calls[0][1]["cwd"] == str(tmp_path)
    assert calls[0][1]["timeout"] == 30


@pytest.mark.parametrize("kwargs", [{}, {"pr_number": ""}])
def test_missing_pr_fails_without_observation(tmp_path, monkeypatch, kwargs):
    path, digest = _sealed(tmp_path)
    calls = _observer(monkeypatch, [])
    result = _verify(path, digest, **kwargs)
    assert result["success"] == "false"
    assert "no pull request was published" in result["detail"]
    assert calls == []


@pytest.mark.parametrize(
    "failure",
    [
        subprocess.CompletedProcess([], 1, "", "offline"),
        subprocess.TimeoutExpired("gh", 30),
        OSError("missing gh"),
        subprocess.CompletedProcess([], 0, "invalid json"),
        {"state": "OPEN"},
        {"state": 1, "baseRefName": "main"},
        [],
    ],
)
@pytest.mark.parametrize("recovers", [True, False])
def test_observation_failures_retry_and_fail_closed(tmp_path, monkeypatch, failure, recovers):
    path, digest = _sealed(tmp_path)
    observations = [failure, failure]
    if recovers:
        observations.append({"state": "OPEN", "baseRefName": "main"})
    calls = _observer(monkeypatch, observations)
    result = _verify(path, digest, pr_number="42")
    assert result["success"] == ("true" if recovers else "false")
    assert len(calls) == 3
    assert result["detail"]
    if not recovers:
        assert "pr_opened" in result["detail"]


@pytest.mark.parametrize("defect", ["digest", "unsealed", "noncanonical", "missing"])
def test_invalid_authority_prevents_observation(tmp_path, monkeypatch, defect):
    path, digest = _sealed(tmp_path, sealed=defect != "unsealed")
    if defect == "digest":
        digest = "sha256:wrong"
    elif defect == "noncanonical":
        path.write_bytes(path.read_bytes() + b"\n")
    elif defect == "missing":
        path.unlink()
    calls = _observer(monkeypatch, [])
    result = _verify(path, digest, pr_number="42")
    assert result["success"] == "false"
    assert result["detail"]
    assert calls == []
