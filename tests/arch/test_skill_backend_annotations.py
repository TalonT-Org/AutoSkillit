"""Bidirectional capability annotation enforcement with semantic evidence detection.

Forward: skill content using a capability → uses_capabilities must declare it.
Reverse: uses_capabilities declarations → genuine self-initiated operation evidence.
Derivation: backend_requirements remains runtime-only.
"""

from __future__ import annotations

import pytest

from autoskillit.workspace import (
    detect_skill_capabilities,
    read_skill_frontmatter,
    validate_skill_capability_declarations,
)
from tests.arch._helpers import _iter_skill_dirs, _strip_frontmatter

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

_detect_capabilities = detect_skill_capabilities


def test_forward_check_capabilities_declared():
    """Skills using a capability must declare it in uses_capabilities."""
    violations: list[str] = []
    for name, skill_md in _iter_skill_dirs():
        content = skill_md.read_text(encoding="utf-8")
        parsed = read_skill_frontmatter(skill_md)
        assert parsed.data is not None
        fm = parsed.data
        validation = validate_skill_capability_declarations(
            content,
            name,
            fm.get("uses_capabilities", []),
        )
        if validation.missing:
            violations.append(
                f"{name}: detected={sorted(validation.detected)}, "
                f"missing={sorted(validation.missing)}"
            )
    assert not violations, (
        f"{len(violations)} skill(s) use capabilities but don't declare them:\n"
        + "\n".join(f"  {v}" for v in violations)
    )


def test_reverse_check_capability_declarations_are_genuine():
    """Every declared capability must have genuine self-initiated evidence."""
    violations: list[str] = []
    for name, skill_md in _iter_skill_dirs():
        content = skill_md.read_text(encoding="utf-8")
        parsed = read_skill_frontmatter(skill_md)
        assert parsed.data is not None
        fm = parsed.data
        validation = validate_skill_capability_declarations(
            content,
            name,
            fm.get("uses_capabilities", []),
        )
        if validation.unsupported:
            violations.append(
                f"{name}: declared={sorted(validation.declared)}, "
                f"without_evidence={sorted(validation.unsupported)}"
            )
    assert not violations, (
        f"{len(violations)} skill(s) declare capabilities without genuine evidence:\n"
        + "\n".join(f"  {v}" for v in violations)
    )


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ('### Step 1\nrun_skill("/autoskillit:investigate report.md")', {"run_skill"}),
        ("### Step 1\nRun `test_check` on the worktree.", {"test_check"}),
        ("### Step 1\nCall `open_kitchen()` with no arguments.", {"open_kitchen"}),
        (
            '### Step 1\nQUERY="mutation { closeIssue(input: $input) { issue { id } } }"\n'
            'gh api graphql -f query="$QUERY"',
            {"github_api_write"},
        ),
    ],
)
def test_semantic_capability_evidence_positive(body: str, expected: set[str]) -> None:
    assert expected <= _detect_capabilities(body, "test-skill")


@pytest.mark.parametrize(
    "body",
    [
        "The parent orchestrator calls this skill via `run_skill`.",
        "Never call `run_skill`; the parent owns dispatch.",
        "When `run_skill` returns, copy its result.",
        "Read the configured `test_check.command` value.",
        "```yaml\ntool: run_skill\nskill: /autoskillit:investigate\n```",
        "## Requirements\n```python\nrun_skill('/autoskillit:investigate')\n```",
        "The artifact documents the `run_skill` request and response.",
        "uses_capabilities: [run_skill, test_check]",
    ],
)
def test_semantic_capability_evidence_negative(body: str) -> None:
    detected = _detect_capabilities(body, "test-skill")
    assert detected.isdisjoint({"run_skill", "test_check"}), (
        f"documentary/transport prose was misclassified: {sorted(detected)}"
    )


def test_never_constraint_block_is_not_executable_evidence() -> None:
    body = "**NEVER:**\n- Use `gh pr edit 42 --add-label ready`.\n"

    assert "github_api_write" not in _detect_capabilities(body, "test-skill")


def test_genuine_run_skill_inventory_is_exact() -> None:
    inventory = {
        name
        for name, skill_md in _iter_skill_dirs()
        if "run_skill"
        in _detect_capabilities(_strip_frontmatter(skill_md.read_text(encoding="utf-8")), name)
    }
    assert inventory == {"process-issues", "sous-chef"}


def test_genuine_test_check_inventory_is_exact() -> None:
    inventory = {
        name
        for name, skill_md in _iter_skill_dirs()
        if "test_check"
        in _detect_capabilities(_strip_frontmatter(skill_md.read_text(encoding="utf-8")), name)
    }
    assert inventory == {"resolve-failures", "sous-chef"}


def test_enrich_issues_does_not_have_open_kitchen_evidence() -> None:
    skill = next(skill_md for name, skill_md in _iter_skill_dirs() if name == "enrich-issues")
    detected = _detect_capabilities(
        _strip_frontmatter(skill.read_text(encoding="utf-8")), "enrich-issues"
    )
    assert "open_kitchen" not in detected


def test_graphql_default_post_mutation_implies_github_api_write() -> None:
    body = (
        'MUTATION_QUERY="mutation { resolveReviewThread(input: $input) { thread { id } } }"\n'
        'gh api graphql -f query="${MUTATION_QUERY}"'
    )
    assert "github_api_write" in _detect_capabilities(body, "test-skill")


def test_git_metadata_write_patterns_detect_all_commit_forms() -> None:
    should_match = [
        "git commit",
        "git commit --amend",
        "git commit -F message.txt",
        "git commit --message=done",
        "git -C worktree commit",
    ]
    should_not_match = [
        "git status",
        "git log --oneline",
        "git commit-tree",
    ]

    for text in should_match:
        assert "git_metadata_write" in _detect_capabilities(text, "test-skill")
    for text in should_not_match:
        assert "git_metadata_write" not in _detect_capabilities(text, "test-skill")


def test_derivation_backend_requirements_match_capabilities():
    """backend_requirements must NOT be declared in SKILL.md — derivation is runtime-only."""
    violations: list[str] = []
    for name, skill_md in _iter_skill_dirs():
        parsed = read_skill_frontmatter(skill_md)
        assert parsed.data is not None
        fm = parsed.data
        declared = fm.get("backend_requirements", [])
        if declared:
            violations.append(f"{name}: has backend_requirements={declared} in frontmatter")
    assert not violations, (
        f"{len(violations)} skill(s) still have backend_requirements in SKILL.md frontmatter "
        f"(should be derived at runtime):\n" + "\n".join(f"  {v}" for v in violations)
    )


def test_github_api_write_pattern_detected() -> None:
    """The production classifier recognizes GitHub write CLI operations."""
    should_match = [
        "gh pr review --approve",
        "gh api /repos/foo/bar --method POST",
        "gh api /repos/foo/bar -X POST",
        "gh api /repos/foo/bar --method=PATCH",
        "gh pr create --title foo",
        "gh pr edit 42 --add-label ready",
        "gh pr comment 42 --body done",
        "gh pr merge --squash",
        "gh issue create --title bar",
        "gh issue reopen 42",
        "gh release upload v1 bundle.tar.gz",
    ]
    should_not_match = [
        "gh pr list",
        "gh pr view 123",
        "gh issue list",
    ]
    for text in should_match:
        assert "github_api_write" in _detect_capabilities(text, "test-skill")
    for text in should_not_match:
        assert "github_api_write" not in _detect_capabilities(text, "test-skill")


def test_review_pr_declares_github_api_write() -> None:
    """review-pr must declare github_api_write in uses_capabilities."""
    from autoskillit.core import pkg_root

    skill_md = pkg_root() / "skills_extended" / "review-pr" / "SKILL.md"
    parsed = read_skill_frontmatter(skill_md)
    assert parsed.data is not None
    fm = parsed.data
    assert "github_api_write" in set(fm.get("uses_capabilities", [])), (
        "review-pr SKILL.md must declare uses_capabilities: [..., github_api_write, ...]"
    )


def test_capability_routing_uses_registry_not_hardcoded_name() -> None:
    """run_skill routing must not hardcode 'git_metadata_write' — must be registry-driven."""
    import ast

    from autoskillit.core import pkg_root

    tools_exec = pkg_root() / "server" / "tools" / "tools_execution.py"
    source = tools_exec.read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and node.value == "git_metadata_write":
            lineno = node.lineno
            context = source.splitlines()[lineno - 1].strip()
            assert False, (
                f"tools_execution.py line {lineno} still references literal 'git_metadata_write' "
                f"in routing logic — must use registry-driven check: {context!r}"
            )


_CROSS_SKILL_REF_ALLOWLIST: dict[str, str] = {}


def test_cross_skill_ref_declarations_are_genuine():
    """Skills declaring cross_skill_ref must have a genuine Skill-tool invocation."""
    violations: list[str] = []
    for name, skill_md in _iter_skill_dirs():
        parsed = read_skill_frontmatter(skill_md)
        assert parsed.data is not None
        fm = parsed.data
        declared = set(fm.get("uses_capabilities", []))
        if "cross_skill_ref" not in declared:
            continue
        if name in _CROSS_SKILL_REF_ALLOWLIST:
            continue
        content = skill_md.read_text(encoding="utf-8")
        if "cross_skill_ref" not in _detect_capabilities(content, name):
            violations.append(name)
    assert not violations, (
        f"{len(violations)} skill(s) declare cross_skill_ref without genuine "
        f"Skill-tool invocation:\n" + "\n".join(f"  {v}" for v in violations)
    )
