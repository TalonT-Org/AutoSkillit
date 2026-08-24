"""Prose-contract sweep: no orchestrator-facing instruction surface instructs
forwarding an EXECUTION_TUNING run_skill parameter (#4402, recurred as #4707).

Before #4402, sous-chef's PARAMETER FORWARDING and MODEL PROPAGATION
sections mandated forwarding ``stale_threshold``/``idle_output_timeout``/
``step_provider``/``model`` to ``run_skill`` — a channel the runtime
attestation gate guaranteed would deny (an int or non-empty str is never
``""``, the gate's undeclared-name exemption). This sweeps every registered
orchestrator-facing instruction surface for the same instruction pattern
recurring.

**#4707 was a scope failure, not a heuristic-quality failure.** This exact
sweep already existed and was already correct — its only flaw was that it
scanned ``skills/*/SKILL.md`` alone, while the actual offending sentence
lived in ``server/tools/tools_recipe.py``'s ``load_recipe`` docstring, a
surface the original glob never reached. The sweep now iterates
``ORCHESTRATOR_FACING_INSTRUCTION_SURFACES``
(core/types/_type_orchestrator_instruction_surfaces.py)
instead of a hardcoded skills-only glob, honoring each surface's declared
extraction mode, so a defense aimed at the wrong file cannot recur silently.

**Literal ``param=`` regex alone is insufficient** — verified during
authoring: the only pre-#4402 literal-form hit in the whole tree was
sous-chef's worked example (``stale_threshold=2400``); the actual mandates
were prose-form with no ``=`` at all ("pass it as the corresponding
``run_skill`` parameter", "apply it to the ``model`` parameter of ALL
``run_skill`` calls"). Two pattern families, both scoped to passages
mentioning ``run_skill`` nearby, so an unrelated doc using these words in a
different context isn't flagged:

  (a) literal kwarg forms: ``(model|stale_threshold|idle_output_timeout|step_provider)=``
  (b) prose forms: an EXECUTION_TUNING param name within ~60 characters of
      "parameter"/"pass"/"forward"

The forbidden name list is derived from the live role registry
(``role is EXECUTION_TUNING``), never a hardcoded copy — a future re-role
changes what this test enforces automatically.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.core import ORCHESTRATOR_FACING_INSTRUCTION_SURFACES
from tests._helpers import (
    execution_tuning_param_names,
    extract_orchestrator_surface_texts,
    find_execution_tuning_forwarding_violations,
)

pytestmark = [pytest.mark.layer("contracts"), pytest.mark.small]

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SRC_ROOT = _REPO_ROOT / "src" / "autoskillit"


def test_no_orchestrator_facing_surface_instructs_forwarding_execution_tuning_params() -> None:
    names = execution_tuning_param_names()
    assert names, (
        "no EXECUTION_TUNING-role run_skill params found — has the role registry drifted?"
    )

    offenders: dict[str, list[str]] = {}
    for surface_name, surface in ORCHESTRATOR_FACING_INSTRUCTION_SURFACES.items():
        texts = extract_orchestrator_surface_texts(surface, _SRC_ROOT)
        for identifier, text in texts.items():
            violations = find_execution_tuning_forwarding_violations(text, names)
            if violations:
                offenders[f"{surface_name} :: {identifier}"] = violations

    assert not offenders, (
        "orchestrator-facing instruction surface(s) instruct forwarding an "
        "EXECUTION_TUNING-role run_skill parameter — these are server-resolved "
        "from the recipe step and the runtime attestation gate denies them if "
        f"forwarded: {offenders!r}"
    )


# Calibration pins: the detection heuristic must actually catch the four
# passages that motivated this test (they existed verbatim in sous-chef
# SKILL.md before #4402's rewrite). If these regress to non-detection, the
# sweep above has gone blind, not merely "found nothing to report."
@pytest.mark.parametrize(
    "passage",
    [
        pytest.param(
            "When a recipe step has a top-level `stale_threshold` or "
            "`idle_output_timeout` field, pass it as the corresponding "
            "`run_skill` parameter. These control session kill thresholds.",
            id="stale_threshold_idle_output_timeout_prose_mandate",
        ),
        pytest.param(
            "When a recipe step has a top-level `provider` field, pass the "
            "value as the `step_provider` parameter of `run_skill`. This "
            "controls which LLM provider (e.g., Minimax, Bedrock) the "
            "session uses.",
            id="step_provider_prose_mandate",
        ),
        pytest.param(
            "**MODEL PROPAGATION** — When the user specifies a model "
            '(e.g. "use opus"), apply it to the `model` parameter of ALL '
            "`run_skill` calls for steps that declare a `model:` field",
            id="model_prose_mandate",
        ),
        pytest.param(
            "Forward the `model` Parameter to `run_skill` when selected.",
            id="mixed_case_trigger_words",
        ),
        pytest.param(
            "Call: `run_skill(skill_command=..., cwd=..., "
            'step_name="implement", output_dir="...", stale_threshold=2400, '
            'step_provider="minimax")`',
            id="worked_example_literal_kwargs",
        ),
    ],
)
def test_detection_heuristic_catches_the_original_defect(passage: str) -> None:
    names = execution_tuning_param_names()
    assert find_execution_tuning_forwarding_violations(passage, names), (
        "the detection heuristic no longer flags a known pre-#4402 forwarding "
        "mandate passage — it has gone blind, not merely found a clean codebase"
    )
