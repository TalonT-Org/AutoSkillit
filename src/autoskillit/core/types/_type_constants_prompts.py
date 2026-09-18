"""Prompt-clause constants surfaced to the LLM via recipe/orchestrator rendering.

Single-source for the doctrinal prose blocks that the orchestrator prompt,
the fleet prompt, and the api_orchestration stop-step text all render to the
LLM. A doctrinal update touches one constant here rather than three Python
call sites and one markdown file.
"""

from __future__ import annotations

from collections.abc import Sequence


def _render_stop_step_evidence_doctrine(indent: str = "") -> str:
    """Render the canonical STOP-STEP EVIDENCE doctrine as bullet points.

    Each tuple entry becomes one ``- ...`` bullet. Pass ``indent`` to
    prefix every line (e.g., ``"  "`` to align with the fleet prompt's
    nested bullets under its H3b heading).
    """
    return "\n".join(f"{indent}- {line}" for line in STOP_STEP_EVIDENCE_DOCTRINE_BULLETS)


STOP_STEP_EVIDENCE_DOCTRINE_BULLETS: Sequence[str] = (
    "Preserve the original failed run_skill response: use its exact result, reason_kind, "
    "and outcome_fields verbatim; a diagnostic result is supplemental.",
    "With no structured original reason, do not infer.",
    "Use static stop message only when no tool evidence exists.",
)


# Bullet-rendered text with no indent — used by the orchestrator prompt and
# the api_orchestration stop-step text.
STOP_STEP_EVIDENCE_DOCTRINE: str = _render_stop_step_evidence_doctrine()

# Same text, indented two spaces — used by the fleet prompt where the
# doctrine sits under an H3b heading as nested bullets.
STOP_STEP_EVIDENCE_DOCTRINE_INDENTED: str = _render_stop_step_evidence_doctrine("  ")


__all__ = [
    "STOP_STEP_EVIDENCE_DOCTRINE",
    "STOP_STEP_EVIDENCE_DOCTRINE_BULLETS",
    "STOP_STEP_EVIDENCE_DOCTRINE_INDENTED",
]
