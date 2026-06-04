"""Tests for sous-chef SKILL.md content invariants."""

from __future__ import annotations

import pytest

from autoskillit.cli._prompts import _read_full_sous_chef

pytestmark = [pytest.mark.layer("cli"), pytest.mark.small]


def test_sous_chef_prohibits_raw_recipe_reading():
    content = _read_full_sous_chef()
    assert "NEVER read recipe YAML files from the filesystem" in content
