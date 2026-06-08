"""Unit and functional tests for _strip_doc_fenced_blocks section-aware code fence filter."""

from __future__ import annotations

import pytest

from tests.arch._helpers import _strip_doc_fenced_blocks
from tests.arch.test_skill_backend_annotations import _detect_capabilities

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]


def test_strips_fence_in_doc_section():
    body = "## Requirements\n```\ngit commit -m 'example'\n```\n"
    assert "git commit" not in _strip_doc_fenced_blocks(body)


def test_keeps_fence_in_step_section():
    body = "### Step 1: Commit\n```bash\ngit commit -m 'real'\n```\n"
    assert "git commit" in _strip_doc_fenced_blocks(body)


def test_keeps_fence_in_substep_section():
    body = "#### 4.2 — Continue rebase\n```bash\ngit rebase --continue\n```\n"
    assert "git rebase" in _strip_doc_fenced_blocks(body)


def test_preserves_inline_code():
    body = "## Docs\nUse `git commit -m` to commit.\n"
    assert "git commit" in _strip_doc_fenced_blocks(body)


def test_preserves_prose_outside_fences():
    body = "## Docs\nAlways run git commit -m for changes.\n"
    assert "git commit" in _strip_doc_fenced_blocks(body)


def test_step_decimal_heading_recognized():
    body = "### Step 0.5: Commit\n```\ngit commit -m 'msg'\n```\n"
    assert "git commit" in _strip_doc_fenced_blocks(body)


def test_numbered_non_step_heading_treated_as_step():
    body = "### 1. Component Diagrams\n```\ngit commit -m 'x'\n```\n"
    assert "git commit" in _strip_doc_fenced_blocks(body)


def test_step_2a_heading_recognized():
    body = "### Step 2a: Read CI Context\n```\ngit commit -m 'x'\n```\n"
    assert "git commit" in _strip_doc_fenced_blocks(body)


def test_detect_capabilities_ignores_doc_fence_git_commit():
    body = (
        "## Conflict-Resolution Plan Requirements\n"
        "**ALWAYS use linear approaches:**\n"
        "```\n"
        'git commit -m "feat: apply changes"\n'
        "```\n"
    )
    detected = _detect_capabilities(body, "test-skill")
    assert "git_metadata_write" not in detected


def test_detect_capabilities_finds_step_fence_git_commit():
    body = '### Step 4: Apply Fixes\n```bash\ngit commit -m "fix: resolve issue"\n```\n'
    detected = _detect_capabilities(body, "test-skill")
    assert "git_metadata_write" in detected
