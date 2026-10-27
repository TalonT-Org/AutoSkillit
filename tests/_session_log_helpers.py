"""Shared builders for session log artifacts in tests."""

from __future__ import annotations

from pathlib import Path


def _snap(
    *,
    captured_at: str | None = "2026-03-03T12:00:00+00:00",
    state: str = "sleeping",
    vm_rss_kb: int = 100000,
    oom_score: int = 50,
    fd_count: int = 10,
    fd_soft_limit: int = 1024,
    sig_pnd: str = "0000000000000000",
    sig_blk: str = "0000000000000000",
    sig_cgt: str = "0000000000000000",
    threads: int = 4,
    wchan: str = "",
    ctx_switches_voluntary: int = 500,
    ctx_switches_involuntary: int = 20,
    cpu_percent: float = 0.0,
) -> dict[str, object]:
    snapshot: dict[str, object] = {
        "state": state,
        "vm_rss_kb": vm_rss_kb,
        "oom_score": oom_score,
        "fd_count": fd_count,
        "fd_soft_limit": fd_soft_limit,
        "sig_pnd": sig_pnd,
        "sig_blk": sig_blk,
        "sig_cgt": sig_cgt,
        "threads": threads,
        "wchan": wchan,
        "ctx_switches_voluntary": ctx_switches_voluntary,
        "ctx_switches_involuntary": ctx_switches_involuntary,
        "cpu_percent": cpu_percent,
    }
    if captured_at is not None:
        snapshot["captured_at"] = captured_at
    return snapshot


def _flush(
    tmp_path: Path, *, backend: str = "claude-code", session_locator=None, **overrides
) -> None:
    from autoskillit.core import ExecutionIdentity, FaultDomain
    from autoskillit.core.types.results._type_results import (
        InfraOutcome,
        ModelIdentity,
        ProviderOutcome,
    )
    from autoskillit.core.types.results._type_results_execution import (
        RecipeIdentity,
        SessionTelemetry,
    )
    from autoskillit.execution.session_log.session_log import flush_session_log

    defaults: dict = {
        "log_dir": str(tmp_path),
        "cwd": "/home/test/project",
        "evidence_worktree": None,
        "session_id": "test-session-001",
        "pid": 12345,
        "skill_command": "/autoskillit:investigate some error",
        "success": True,
        "needs_retry": False,
        "retry_reason": "none",
        "infra": InfraOutcome(
            exit_category="completed",
            fault_domain=FaultDomain.LOGIC,
        ),
        "api_error_status": None,
        "is_error": False,
        "subtype": "completed",
        "exit_code": 0,
        "start_ts": "2026-03-03T12:00:00+00:00",
        "proc_snapshots": [_snap(), _snap(), _snap()],
        "dispatch_id": "",
        "caller_session_id": "",
        "github_api_log": None,
        "token_usage": None,
        "turn_usage": [],
        "timing_seconds": None,
        "audit_record": None,
        "loc_insertions": 0,
        "loc_deletions": 0,
        "subagent_model_outcomes": (),
        "child_outcomes": (),
        "model_identity": ModelIdentity.unknown(),
        "execution_identity": ExecutionIdentity.empty(),
    }
    defaults.update(overrides)

    # Convert legacy model_identifier + profile_name to ModelIdentity if callers pass them
    _model_identifier = defaults.pop("model_identifier", None)
    _profile_name = defaults.pop("profile_name", None)
    if _model_identifier is not None or _profile_name is not None:
        _mid = _model_identifier or ""
        _pn = _profile_name or ""
        if _pn and _pn != "anthropic":
            defaults["model_identity"] = ModelIdentity.for_provider(
                configured=_mid, effective="", profile=_pn
            )
        elif _mid:
            defaults["model_identity"] = ModelIdentity.anthropic(_mid)
        else:
            defaults["model_identity"] = ModelIdentity.unknown()

    # Extract telemetry kwargs and build SessionTelemetry before forwarding
    _github_api_log = defaults.pop("github_api_log")
    _session_id = defaults.get("session_id", "")
    _api_usage = _github_api_log.drain(_session_id) if _github_api_log is not None else None
    telemetry = SessionTelemetry(
        token_usage=defaults.pop("token_usage"),
        turn_usage=defaults.pop("turn_usage"),
        timing_seconds=defaults.pop("timing_seconds"),
        audit_record=defaults.pop("audit_record"),
        github_api_usage=_api_usage,
        github_api_requests=_api_usage.get("total_requests", 0) if _api_usage else 0,
        loc_insertions=defaults.pop("loc_insertions"),
        loc_deletions=defaults.pop("loc_deletions"),
        subagent_model_outcomes=defaults.pop("subagent_model_outcomes"),
        child_outcomes=defaults.pop("child_outcomes"),
        execution_identity=defaults.pop("execution_identity"),
    )

    # Extract provider/recipe kwargs and build typed containers
    _provider_used = defaults.pop("provider_used", "")
    _provider_fallback = defaults.pop("provider_fallback", False)
    _recipe_name = defaults.pop("recipe_name", "")
    _recipe_content_hash = defaults.pop("recipe_content_hash", "")
    _recipe_composite_hash = defaults.pop("recipe_composite_hash", "")
    _recipe_version = defaults.pop("recipe_version", "")
    provider_outcome = ProviderOutcome(
        provider_used=_provider_used,
        fallback_activated=_provider_fallback,
    )
    recipe_identity = RecipeIdentity(
        name=_recipe_name,
        content_hash=_recipe_content_hash,
        composite_hash=_recipe_composite_hash,
        version=_recipe_version,
    )
    flush_session_log(
        **defaults,
        telemetry=telemetry,
        provider_outcome=provider_outcome,
        recipe_identity=recipe_identity,
        backend=backend,
        session_locator=session_locator,
    )
