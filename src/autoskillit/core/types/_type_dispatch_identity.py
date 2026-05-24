"""Dispatch identity value object — single source of truth for all sentinel strings.

Zero autoskillit imports outside this sub-package. IL-0 type contract.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

__all__ = ["DispatchIdentity", "PromptContractError", "assert_prompt_sentinel"]


class PromptContractError(RuntimeError):
    """Raised when a prompt violates the sentinel contract."""


def _build_sentinel_contract(dispatch_id: str, short: str) -> str:
    return f"""\
--- SECTION 8: FINAL OUTPUT CONTRACT ---

When the pipeline completes (success or failure), emit this EXACT sentinel block
as your final output. No other text after the sentinel.

```
---l3-result::{dispatch_id}---
{{"success": <true|false>, "reason": "<completion_reason>", "summary": "<one_line_summary>"}}
---end-l3-result::{dispatch_id}---
%%L3_DONE::{short}%%
```

Fields:
- success: true if all mandatory steps completed without unresolved failures
- reason: "completed", "failed", "fleet_quota_exhausted", "timeout",
  "open_kitchen_failed", "missing_on_failure"
- summary: One-line description of what happened

The sentinel markers ---l3-result::{dispatch_id}--- and ---end-l3-result::{dispatch_id}---
are parsed by the fleet dispatcher. The %%L3_DONE::{short}%% marker
signals session completion to the process monitor.
"""


@dataclass(frozen=True, slots=True)
class DispatchIdentity:
    dispatch_id: str
    completion_marker: str
    sentinel_open: str
    sentinel_close: str
    sentinel_contract: str

    @classmethod
    def fresh(cls) -> DispatchIdentity:
        did = str(uuid4())
        return cls._from_id(did)

    @classmethod
    def from_dispatch_id(cls, dispatch_id: str) -> DispatchIdentity:
        return cls._from_id(dispatch_id)

    @classmethod
    def _from_id(cls, did: str) -> DispatchIdentity:
        short = did[:8]
        return cls(
            dispatch_id=did,
            completion_marker=f"%%L3_DONE::{short}%%",
            sentinel_open=f"---l3-result::{did}---",
            sentinel_close=f"---end-l3-result::{did}---",
            sentinel_contract=_build_sentinel_contract(did, short),
        )


def assert_prompt_sentinel(prompt: str, identity: DispatchIdentity) -> None:
    """Assert that the prompt contains all sentinel markers from the given identity."""
    if identity.sentinel_open not in prompt:
        raise PromptContractError(f"Prompt missing sentinel open marker: {identity.sentinel_open}")
    if identity.sentinel_close not in prompt:
        raise PromptContractError(
            f"Prompt missing sentinel close marker: {identity.sentinel_close}"
        )
    if identity.completion_marker not in prompt:
        raise PromptContractError(
            f"Prompt missing completion marker: {identity.completion_marker}"
        )
