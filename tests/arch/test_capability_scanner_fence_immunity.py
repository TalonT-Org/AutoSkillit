"""Regression guards for production capability-scanner fence immunity."""

from __future__ import annotations

import pytest

from autoskillit.workspace import classify_skill_capability_evidence, detect_skill_capabilities

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]


def test_documentary_fence_is_artifact_not_genuine_evidence() -> None:
    content = "## Requirements\n```bash\ngit commit -m 'example'\n```\n"
    evidence = classify_skill_capability_evidence(content)
    commit_evidence = [item for item in evidence if item.capability == "git_metadata_write"]
    assert commit_evidence
    assert all(item.artifact for item in commit_evidence)
    assert "git_metadata_write" not in detect_skill_capabilities(content)


def test_nested_executable_fence_is_genuine_evidence() -> None:
    content = (
        "### Step 2: Inspect history\n"
        "#### Part A — Read session artifacts\n"
        "```bash\n"
        'find "$PROJECT_ROOT/.claude/projects" -name "*.jsonl"\n'
        "```\n"
    )
    evidence = classify_skill_capability_evidence(content)
    claude_evidence = [item for item in evidence if item.capability == "claude_dir"]
    assert claude_evidence
    assert all(item.executable for item in claude_evidence)
    assert "claude_dir" in detect_skill_capabilities(content)
