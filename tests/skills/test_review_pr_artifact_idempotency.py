"""Structural tests for review-pr artifact idempotency and receipt ownership."""

from __future__ import annotations

import ast
import builtins
import re
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("skills"), pytest.mark.small]

_SKILL_PATH = (
    Path(__file__).parent.parent.parent
    / "src"
    / "autoskillit"
    / "skills_extended"
    / "review-pr"
    / "SKILL.md"
)

_IDEMPOTENT_KEYWORDS = frozenset(
    ["read it first", "already exists", "Bash redirect", "jq -n", "atomically rename"]
)

_VULNERABLE_FILES = [
    "prior_threads_{pr_number}.json",
    "diff_context_{pr_number}.json",
    "raw_findings_{pr_number}.json",
    "local_findings_{pr_number}.json",
]

_WINDOW = 600
_RECEIPT_PREFIX = "batch_review_response_"
_UPSTREAM_BINDINGS: dict[str, str] = {
    "FINAL_SNAPSHOT_STATE": "Bind `FINAL_SNAPSHOT_STATE` from the one printed word",
    "AUDIT_FINALIZATION": "AUDIT_FINALIZATION = finalize_review_audit(",
    "FINAL_REVIEW_FINDINGS": "`FINAL_REVIEW_FINDINGS = FILTERED_FINDINGS = survivors`",
    "UNPOSTABLE_FINDINGS": "`UNPOSTABLE_FINDINGS = unpostable`",
    "REVIEW_LEVEL_FINDINGS": "`REVIEW_LEVEL_FINDINGS = review_level_findings`",
    "RAW_LEDGER": "Before Step 6, finish `RAW_LEDGER` and `HANDOFF_METADATA` in memory",
    "HANDOFF_METADATA": "Before Step 6, finish `RAW_LEDGER` and `HANDOFF_METADATA` in memory",
    "GATE_AUTHORITY": "parse the one printed JSON object as `GATE_AUTHORITY`",
    "ANNOTATION_GENERATION_ID": (
        "`ANNOTATION_GENERATION_ID` from its `snapshot` and generation fields"
    ),
    "MODE": 'MODE="github"',
    "REVIEW_GENERATION_ID": "REVIEW_GENERATION_ID = PUBLICATION_SEED[",
    "pr_number": "read -r pr_number pr_url",
}


def _all_surrounding_windows(text: str, target: str) -> list[str]:
    """Return surrounding text windows for all occurrences of target in text."""
    windows: list[str] = []
    start = 0
    while True:
        idx = text.find(target, start)
        if idx == -1:
            break
        w_start = max(0, idx - _WINDOW)
        w_end = min(len(text), idx + len(target) + _WINDOW)
        windows.append(text[w_start:w_end])
        start = idx + 1
    return windows


def _python_names_in_fence(source: str) -> tuple[set[str], set[str]]:
    nodes = list(ast.walk(ast.parse(source)))
    loaded = {
        node.id for node in nodes if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
    }
    bound = {
        node.id for node in nodes if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store)
    }
    for node in nodes:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            bound.update(
                alias.asname
                or (alias.name.partition(".")[0] if isinstance(node, ast.Import) else alias.name)
                for alias in node.names
            )
        elif isinstance(node, ast.ExceptHandler) and node.name:
            bound.add(node.name)
    return loaded, bound


@pytest.mark.parametrize("filename_pattern", _VULNERABLE_FILES)
def test_write_instruction_uses_dynamic_output_dir(filename_pattern: str) -> None:
    """Write instructions use the resolved output directory pasted as a literal path.

    The recipe may scope output_dir to an iteration subdirectory.
    """
    text = _SKILL_PATH.read_text()
    windows = _all_surrounding_windows(text, filename_pattern)
    assert windows, f"Pattern {filename_pattern!r} not found in review-pr/SKILL.md"
    found = any("{review_output_dir}" in window for window in windows)
    assert found, (
        f"No {{review_output_dir}} path found near {filename_pattern!r} in "
        f"review-pr/SKILL.md. First window: {windows[0][:300]!r}"
    )


@pytest.mark.parametrize("filename_pattern", _VULNERABLE_FILES)
def test_write_instruction_includes_idempotent_guidance(filename_pattern: str) -> None:
    """Write instructions for collision-risk files must include idempotent-write guidance.

    Each file that is written on every review-loop iteration (and therefore risks
    colliding on the second pass) must have guidance near the write instruction
    explaining how to safely overwrite or skip pre-existing files.
    """
    text = _SKILL_PATH.read_text()
    windows = _all_surrounding_windows(text, filename_pattern)
    assert windows, f"Pattern {filename_pattern!r} not found in review-pr/SKILL.md"
    found = any(
        any(kw.lower() in window.lower() for kw in _IDEMPOTENT_KEYWORDS) for window in windows
    )
    assert found, (
        f"No idempotent-write guidance found near any occurrence of {filename_pattern!r} in "
        f"review-pr/SKILL.md. Expected one of {sorted(_IDEMPOTENT_KEYWORDS)!r} within "
        f"{_WINDOW} chars of at least one occurrence. "
        f"First window: {windows[0][:300]!r}"
    )


def test_publication_uses_same_directory_temporary_and_atomic_rename() -> None:
    text = _SKILL_PATH.read_text()
    step8 = text[text.index("### Step 8") :]
    assert "same-directory temporary file" in step8
    assert "atomic rename" in step8
    assert "local_findings_{pr_number}.json last" in step8
    assert "`github_review_binding_{pr_number}.json` last" in step8
    assert "review_generation_id" in step8
    assert "annotation_generation_id" in step8
    assert step8.index("**Write Raw Findings JSON (first):**") < step8.index(
        "**Write Diff-Scoped Context Handoff"
    )
    assert step8.index("2. diff context;") < step8.index(
        "`github_review_binding_{pr_number}.json` last"
    )


def test_step8_python_names_are_bound() -> None:
    text = _SKILL_PATH.read_text()
    step8 = text[text.index("### Step 8") :]
    fences = re.findall(r"```python\s*\n(.*?)\n```", step8, flags=re.DOTALL)
    assert fences, "Step 8 must contain executable Python fences"

    loaded: set[str] = set()
    for fence_index, source in enumerate(fences, start=1):
        loaded_in_fence, stored_or_imported = _python_names_in_fence(source)
        loaded.update(loaded_in_fence)
        unresolved = loaded_in_fence - stored_or_imported - set(dir(builtins))
        undocumented = unresolved - set(_UPSTREAM_BINDINGS)
        assert not undocumented, (
            f"Step 8 Python fence {fence_index} uses undocumented names: {sorted(undocumented)}"
        )
    assert set(_UPSTREAM_BINDINGS) <= loaded, (
        f"Step 8 no longer consumes documented inputs: {sorted(set(_UPSTREAM_BINDINGS) - loaded)}"
    )
    for name, binding in _UPSTREAM_BINDINGS.items():
        assert binding in text, f"Step 8 input {name!r} has no matching skill binding"


def test_runtime_threads_validation_aggregation_and_publication_results() -> None:
    text = _SKILL_PATH.read_text()
    step4 = text[text.index("### Step 4") : text.index("### Step 4.5")]
    step8 = text[text.index("### Step 8") :]

    assert "AUDIT_FINALIZATION = finalize_review_audit(" in step4
    assert "FINAL_REVIEW_FINDINGS = FILTERED_FINDINGS = survivors" in step4
    assert "PUBLICATION = prepare_experimental_review_publication(" in step8
    assert "survivors=FINAL_REVIEW_FINDINGS" in step8
    assert "receipt=" not in step8
    assert "github_review_binding_{pr_number}.json" in step8
    assert "Every publisher-written artifact" in step8
    assert "PUBLICATION_RESULT = publish_experimental_review_artifacts(" in step8
    assert "if not SNAPSHOT_IS_FRESH:" in step8
    assert 'verdict = AUDIT_FINALIZATION["verdict"]' in step8
    assert "finalize_review_audit(" in step8
    assert "FINAL_REVIEW_FINDINGS = []" in step8
    assert 'raise RuntimeError("publication generation changed after external effects")' in step8


def test_fixed_destinations_reject_direct_redirects() -> None:
    text = _SKILL_PATH.read_text()
    assert "jq -n ... > path" not in text
    assert "bash redirects (`> path`)" not in text
    for filename in (
        "diff_context_{pr_number}.json",
        "raw_findings_{pr_number}.json",
        "github_review_binding_{pr_number}.json",
        "local_findings_{pr_number}.json",
    ):
        assert f'> "{{review_output_dir}}{filename}"' not in text


def test_post_pr_review_owns_the_idempotent_receipt_write() -> None:
    """The prompt supplies one contained receipt path and never writes it itself."""
    text = _SKILL_PATH.read_text()
    assert text.count("post_pr_review") == 1

    call_idx = text.find("post_pr_review")
    section_start = text.rfind("\n### ", 0, call_idx)
    section_end = text.find("\n### ", call_idx)
    section = text[max(0, section_start) : section_end if section_end >= 0 else len(text)]

    assert _RECEIPT_PREFIX in section
    assert ".json" in section[section.find(_RECEIPT_PREFIX) :]
    assert "pr_number" in section.lower() or "PR_NUMBER" in section
    assert "{review_output_dir}" in section
    assert "receipt_path" in section
    assert "review_receipt_path" in text[call_idx:]

    receipt_line = next(line for line in section.splitlines() if _RECEIPT_PREFIX in line)
    assert not any(operator in receipt_line for operator in (">", "tee ", "jq -n")), (
        "review-pr must pass the receipt path to post_pr_review; shell or jq writes "
        "would bypass the tool's idempotent receipt semantics"
    )
