"""Architectural contract: sous-chef SKILL.md must instruct LLM to forward recipe parameters."""

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
