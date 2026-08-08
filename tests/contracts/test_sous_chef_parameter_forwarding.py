"""Architectural contract: sous-chef SKILL.md forwarding/resolution guidance.

``step_name`` and ``output_dir`` remain LLM-forwarded — the gate always needs
these from the caller. ``model``, ``stale_threshold``, ``idle_output_timeout``,
and ``step_provider`` are the opposite case: they are server-resolved from the
recipe step and must NOT be forwarded on attested ``run_skill`` calls — the
runtime attestation gate denies them (see #4402's rectify plan).
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("contracts"), pytest.mark.small]


def test_sous_chef_skill_md_mentions_output_dir():
    """Sous-chef SKILL.md must instruct the LLM to forward output_dir."""
    skill_md = Path("src/autoskillit/skills/sous-chef/SKILL.md").read_text()
    assert "output_dir" in skill_md, (
        "sous-chef SKILL.md does not mention output_dir. "
        "The LLM must be instructed to forward output_dir from recipe "
        "step with: blocks to run_skill."
    )


def test_sous_chef_skill_md_states_step_provider_is_server_resolved():
    """Sous-chef SKILL.md must state step_provider is server-resolved, not forwarded."""
    skill_md = (
        Path(__file__).parents[2] / "src/autoskillit/skills/sous-chef/SKILL.md"
    ).read_text()
    assert "step_provider" in skill_md, "sous-chef SKILL.md does not mention step_provider."
    assert "server-side" in skill_md, (
        "sous-chef SKILL.md must state that step_provider (along with model, "
        "stale_threshold, and idle_output_timeout) is resolved server-side from "
        "the recipe step, not forwarded by the LLM — forwarding these is denied "
        "by the runtime attestation gate, not merely redundant."
    )
    assert "Do not include any of them in an attested" in skill_md, (
        "sous-chef SKILL.md must instruct the LLM to omit step_provider (and "
        "model/stale_threshold/idle_output_timeout) from attested run_skill calls."
    )


def test_sous_chef_skill_md_mentions_step_name():
    """Sous-chef SKILL.md must instruct the LLM to forward step_name."""
    skill_md = (
        Path(__file__).parents[2] / "src/autoskillit/skills/sous-chef/SKILL.md"
    ).read_text()
    assert "step_name" in skill_md, (
        "sous-chef SKILL.md does not mention step_name. "
        "The LLM must be instructed to forward step_name from recipe "
        "step with: blocks to run_skill for lock enforcement."
    )
