"""Registration-based ownership of every funnel-spawned descendant of an owner.

An owner opens a scope under a unique token and hands ``OwnerScope.child_env()``
to the process it launches. ``spawn_owned_process`` stamps every tether written
anywhere in that process tree with the token, so the owner settles exactly its
own descendants — selected by registration, never by ancestry, process group, or
an environment scan — before control returns to it. Sealing the scope makes the
funnel refuse any later spawn into it. See docs/decisions/0016-owner-scope.md.
"""

from __future__ import annotations

import sys
import time
import uuid
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, replace
from functools import partial
from pathlib import Path
from typing import Final

import anyio
import regex as re

from autoskillit.core import (
    OWNER_SCOPE_DIR_ENV_VAR,
    OWNER_SCOPE_ENV_VAR,
    ProcessCleanupResult,
    SkillResult,
    get_logger,
    read_versioned_json,
)
from autoskillit.execution.process._process_tether import (
    TetherRecord,
    TetherSweepOutcome,
    _settle_tether_targets,
    _tether_record_from_dict,
    _tether_target_statuses,
    _tether_targets,
    default_tether_dir,
    remove_tether,
    seal_owner_scope,
)

logger = get_logger(__name__)

OWNER_SCOPE_SETTLE_TIMEOUT_SECONDS: Final = 30.0
OWNER_SCOPE_PASS_INTERVAL_SECONDS: Final = 0.2
# Exceeds the PTY workload-resolution window of the managed runner, so a
# workload identity recorded after spawn is still examined before removal.
OWNER_SCOPE_WORKLOAD_RESOLVE_SECONDS: Final = 3.0

_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9._-]+")
_DISPATCH_TOKEN_PREFIX: Final = "dispatch-"
_TOKEN_NONCE_HEX_CHARS: Final = 12
# Dispositions that keep a pass from counting as stable.
_PENDING_OUTCOMES: Final = frozenset({"reaped", "unsettled", "awaiting_workload"})


def _validated_token(token: str) -> str:
    if not _TOKEN_PATTERN.fullmatch(token):
        raise ValueError(f"invalid owner scope token: {token!r}")
    return token


def new_dispatch_owner_scope_token(dispatch_id: str) -> str:
    """Mint a token unique to one dispatch run, so a resume is never blocked by a prior seal."""
    nonce = uuid.uuid4().hex[:_TOKEN_NONCE_HEX_CHARS]
    return _validated_token(f"{_DISPATCH_TOKEN_PREFIX}{dispatch_id or 'anon'}-{nonce}")


def _iter_tether_records(tether_dir: Path) -> Iterator[tuple[Path, TetherRecord]]:
    if not tether_dir.is_dir():
        return
    for path in sorted(tether_dir.glob("*.json")):
        record = _read_tether(path)
        if record is not None:
            yield path, record


def _read_tether(path: Path) -> TetherRecord | None:
    try:
        return _tether_record_from_dict(read_versioned_json(path, 1))
    except (OSError, ValueError, KeyError, TypeError):
        return None


def dispatch_scope_tokens(tether_dir: Path, dispatch_id: str) -> frozenset[str]:
    """Tokens of the scoped tethers that runs of *dispatch_id* left in *tether_dir*."""
    run_token = re.compile(
        re.escape(f"{_DISPATCH_TOKEN_PREFIX}{dispatch_id}-")
        + f"[0-9a-f]{{{_TOKEN_NONCE_HEX_CHARS}}}"
    )
    return frozenset(
        record.owner_scope
        for _, record in _iter_tether_records(tether_dir)
        if record.owner_scope is not None and run_token.fullmatch(record.owner_scope)
    )


@dataclass(frozen=True, slots=True)
class OwnerScopeSettlement:
    """Evidence from one settlement of an owner scope."""

    token: str
    supported: bool = True
    reaped_pids: tuple[int, ...] = ()
    survivor_pids: tuple[int, ...] = ()
    passes: int = 0
    converged: bool = False
    outcomes: tuple[TetherSweepOutcome, ...] = ()
    error: str = ""

    @property
    def complete(self) -> bool:
        return self.supported and self.converged and not self.survivor_pids

    def to_log_fields(self) -> dict[str, object]:
        return {
            "scope": self.token,
            "supported": self.supported,
            "complete": self.complete,
            "converged": self.converged,
            "passes": self.passes,
            "reaped_pids": list(self.reaped_pids),
            "survivor_pids": list(self.survivor_pids),
            "outcomes": [f"{o.child_pid}:{o.outcome}" for o in self.outcomes],
            "error": self.error,
        }


@dataclass(slots=True)
class _SettlementEvidence:
    reaped: set[int] = field(default_factory=set)
    # Kill survivors and access-denied pids stay reported until a later kill
    # confirms them gone; unobservable survivors keep the settlement incomplete.
    survivors: set[int] = field(default_factory=set)
    unsettled: set[int] = field(default_factory=set)

    def absorb(self, result: ProcessCleanupResult) -> None:
        self.reaped.update(result.terminated_pids)
        self.survivors.difference_update(result.terminated_pids)
        self.survivors.update(result.survivor_pids, result.access_denied_pids)


def _settle_scoped_tether(path: Path, record: TetherRecord, evidence: _SettlementEvidence) -> str:
    """Settle one scoped tether and return its disposition for this pass."""
    killed = False
    while True:
        for result in _settle_tether_targets(record, _tether_target_statuses(record)):
            killed = True
            evidence.absorb(result)
        # A workload identity recorded concurrently is settled in this same pass.
        refreshed = _read_tether(path)
        if refreshed is None:
            return "reaped" if killed else "vanished"
        workload = (refreshed.workload_pid, refreshed.workload_starttime_ticks)
        if workload == (record.workload_pid, record.workload_starttime_ticks):
            break
        record = refreshed
    statuses = _tether_target_statuses(record)
    live = [pid for name, pid, _ in _tether_targets(record) if statuses[name] == "live"]
    if live:
        evidence.unsettled.update(live)
        return "unsettled"
    if record.workload_pid is None:
        try:
            age = time.time() - path.stat().st_mtime
        except OSError:
            return "reaped" if killed else "vanished"
        if age < OWNER_SCOPE_WORKLOAD_RESOLVE_SECONDS:
            return "awaiting_workload"
    remove_tether(path)
    if killed:
        return "reaped"
    return "identity_mismatch" if "mismatch" in statuses.values() else "dead_child"


def settle_owner_scope(
    tether_dir: Path,
    token: str,
    *,
    seal: bool,
    timeout: float = OWNER_SCOPE_SETTLE_TIMEOUT_SECONDS,
) -> OwnerScopeSettlement:
    """End every live funnel-spawned process registered under *token*.

    Repeats identity-fenced passes until one finds no live scoped target, no
    kill to confirm, and no tether still awaiting its PTY workload identity, or
    until *timeout* elapses. With *seal*, the seal is written first so nothing
    can spawn into the scope while it settles.
    """
    if sys.platform != "linux":
        return OwnerScopeSettlement(token=token, supported=False)
    _validated_token(token)
    if seal:
        seal_owner_scope(tether_dir, token)
    deadline = time.monotonic() + timeout
    evidence = _SettlementEvidence()
    outcomes: dict[str, TetherSweepOutcome] = {}
    passes = 0
    converged = False
    while True:
        passes += 1
        evidence.unsettled.clear()
        pending = False
        for path, record in _iter_tether_records(tether_dir):
            if record.owner_scope != token:
                continue
            outcome = _settle_scoped_tether(path, record, evidence)
            outcomes[str(path)] = TetherSweepOutcome(str(path), record.child_pid, outcome)
            pending = pending or outcome in _PENDING_OUTCOMES
        if not pending:
            converged = True
            break
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(OWNER_SCOPE_PASS_INTERVAL_SECONDS, remaining))
    return OwnerScopeSettlement(
        token=token,
        reaped_pids=tuple(sorted(evidence.reaped)),
        survivor_pids=tuple(sorted(evidence.unsettled | evidence.survivors)),
        passes=passes,
        converged=converged,
        outcomes=tuple(outcomes.values()),
    )


@dataclass(slots=True)
class OwnerScope:
    """An open owner scope: its token, the owner's tether directory, and its evidence."""

    token: str
    tether_dir: Path
    settlements: list[OwnerScopeSettlement] = field(default_factory=list)

    def child_env(self) -> dict[str, str]:
        return {OWNER_SCOPE_ENV_VAR: self.token, OWNER_SCOPE_DIR_ENV_VAR: str(self.tether_dir)}

    async def settle_descendants(self, *, seal: bool) -> OwnerScopeSettlement:
        """Settle the scope off the event loop; shielded, and never raises."""
        settlement = OwnerScopeSettlement(token=self.token, error="settlement did not run")
        with anyio.CancelScope(shield=True):
            settlement = await self._run_settlement(seal=seal)
        self._log(settlement)
        self.settlements.append(settlement)
        return settlement

    async def _run_settlement(self, *, seal: bool) -> OwnerScopeSettlement:
        settle = partial(settle_owner_scope, self.tether_dir, self.token, seal=seal)
        try:
            return await anyio.to_thread.run_sync(settle, abandon_on_cancel=False)
        except Exception:
            logger.warning("owner_scope_settlement_retry", scope=self.token, exc_info=True)
        try:
            return await anyio.to_thread.run_sync(settle, abandon_on_cancel=False)
        except Exception as exc:
            logger.error("owner_scope_settlement_failed", scope=self.token, exc_info=True)
            return OwnerScopeSettlement(token=self.token, error=f"{type(exc).__name__}: {exc}")

    def _log(self, settlement: OwnerScopeSettlement) -> None:
        if not settlement.supported:
            if all(prior.supported for prior in self.settlements):
                logger.warning("owner_scope_unsupported_platform", scope=self.token)
            return
        if settlement.reaped_pids:
            logger.warning("owner_scope_reaped_descendants", **settlement.to_log_fields())
        if not settlement.complete:
            logger.error("owner_scope_settlement_incomplete", **settlement.to_log_fields())

    def fold_cleanup_evidence(self, skill_result: SkillResult) -> SkillResult:
        """Mark the result's cleanup incomplete when any supported settlement was."""
        if not any(s.supported and not s.complete for s in self.settlements):
            return skill_result
        return replace(skill_result, infra=replace(skill_result.infra, cleanup_incomplete=True))


@asynccontextmanager
async def owner_scope(token: str) -> AsyncIterator[OwnerScope]:
    """Open a scope bound to the owner's own default tether directory.

    Scoped tethers land where fleet-session boot sweeps also look, so they are
    still found if the owner dies. On every exit the scope is sealed and settled
    before control returns; an exceptional exit carries the evidence as a note.
    """
    scope = OwnerScope(token=_validated_token(token), tether_dir=default_tether_dir())
    try:
        yield scope
    except BaseException as exc:
        settlement = await scope.settle_descendants(seal=True)
        exc.add_note(f"owner scope settlement evidence: {settlement.to_log_fields()}")
        raise
    await scope.settle_descendants(seal=True)
