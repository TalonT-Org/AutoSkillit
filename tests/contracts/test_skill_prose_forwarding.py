"""Prose-contract sweep: no bundled skill instructs forwarding an
EXECUTION_TUNING run_skill parameter (#4402).

Before #4402, sous-chef's PARAMETER FORWARDING and MODEL PROPAGATION
sections mandated forwarding ``stale_threshold``/``idle_output_timeout``/
``step_provider``/``model`` to ``run_skill`` — a channel the runtime
attestation gate guaranteed would deny (an int or non-empty str is never
``""``, the gate's undeclared-name exemption). This sweeps every bundled
skill for the same instruction pattern recurring.

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

import re
from pathlib import Path

import pytest

from autoskillit.core import ToolParamRole, get_tool_def

pytestmark = [pytest.mark.layer("contracts"), pytest.mark.small]

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SKILLS_ROOT = _REPO_ROOT / "src" / "autoskillit" / "skills"

# Generous scope-in window: does this passage concern run_skill at all?
_RUN_SKILL_WINDOW = 400
# Tight window for the actual forwarding-mandate trigger words.
_PROSE_TRIGGER_WINDOW = 60
_PROSE_TRIGGER_WORDS = ("parameter", "pass", "forward")


def _execution_tuning_param_names() -> tuple[str, ...]:
    tool_def = get_tool_def("run_skill")
    assert tool_def is not None, "run_skill must be a registered ToolDef"
    return tuple(
        sorted(
            param.name for param in tool_def.params if param.role is ToolParamRole.EXECUTION_TUNING
        )
    )


def _literal_kwarg_pattern(names: tuple[str, ...]) -> re.Pattern[str]:
    return re.compile(r"(?:" + "|".join(re.escape(name) for name in names) + r")=")


def _find_violations(text: str, names: tuple[str, ...]) -> list[str]:
    violations: list[str] = []

    for match in _literal_kwarg_pattern(names).finditer(text):
        window_start = max(0, match.start() - _RUN_SKILL_WINDOW)
        window_end = min(len(text), match.end() + _RUN_SKILL_WINDOW)
        if "run_skill" not in text[window_start:window_end]:
            continue
        violations.append(f"literal kwarg form {match.group(0)!r}")

    for name in names:
        for match in re.finditer(re.escape(name), text):
            local_start = max(0, match.start() - _PROSE_TRIGGER_WINDOW)
            local_end = min(len(text), match.end() + _PROSE_TRIGGER_WINDOW)
            local_window = text[local_start:local_end]
            normalized_local_window = local_window.lower()
            if not any(word in normalized_local_window for word in _PROSE_TRIGGER_WORDS):
                continue
            wide_start = max(0, match.start() - _RUN_SKILL_WINDOW)
            wide_end = min(len(text), match.end() + _RUN_SKILL_WINDOW)
            if "run_skill" not in text[wide_start:wide_end]:
                continue
            violations.append(f"prose form near {name!r}: {local_window!r}")

    return violations


def test_no_bundled_skill_instructs_forwarding_execution_tuning_params() -> None:
    names = _execution_tuning_param_names()
    assert names, (
        "no EXECUTION_TUNING-role run_skill params found — has the role registry drifted?"
    )

    offenders: dict[str, list[str]] = {}
    for skill_md in sorted(_SKILLS_ROOT.glob("*/SKILL.md")):
        text = skill_md.read_text(encoding="utf-8")
        violations = _find_violations(text, names)
        if violations:
            offenders[str(skill_md.relative_to(_REPO_ROOT))] = violations

    assert not offenders, (
        "bundled skill(s) instruct forwarding an EXECUTION_TUNING-role run_skill "
        "parameter — these are server-resolved from the recipe step and the runtime "
        f"attestation gate denies them if forwarded: {offenders!r}"
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
    names = _execution_tuning_param_names()
    assert _find_violations(passage, names), (
        "the detection heuristic no longer flags a known pre-#4402 forwarding "
        "mandate passage — it has gone blind, not merely found a clean codebase"
    )
