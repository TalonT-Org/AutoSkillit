"""Bidirectional backend annotation enforcement with capability-aware detection.

Forward: skill content using a capability → uses_capabilities must declare it.
Reverse: backend_requirements present → at least one capability justifies it.
Co-requirement: backend_requirements non-empty → uses_capabilities must also exist.
Derivation: backend_requirements == union of required_backends from uses_capabilities.
"""

from __future__ import annotations

import re

import pytest

from autoskillit.core.types._type_constants_registries import SKILL_CAPABILITY_REGISTRY
from autoskillit.workspace.skills import _read_skill_frontmatter
from tests.arch._helpers import _iter_skill_dirs, _strip_doc_fenced_blocks, _strip_frontmatter

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

_CAPABILITY_PATTERNS: dict[str, list[re.Pattern[str]]] = {
    "agent_subagent": [re.compile(r"Agent\(\s*subagent_type\s*=")],
    "agent_model": [re.compile(r"Agent\(\s*model\s*=")],
    "open_kitchen": [re.compile(r"\bopen_kitchen\b"), re.compile(r"\bclose_kitchen\b")],
    "run_skill": [re.compile(r"\brun_skill\b")],
    "test_check": [re.compile(r"\btest_check\b")],
    "claude_dir": [re.compile(r"\.claude/")],
    "git_metadata_write": [
        re.compile(r"create_impl_worktree\.sh|git worktree add\b[ \t]+\S|git checkout -b"),
        re.compile(r"git\s+(?:-C\s+\S+\s+)?commit\s+-m"),
        re.compile(r'\bgit\s+(?:-C\s+\S+\s+)?rebase\s+(?:--\w|[$"\{])'),
    ],
    "github_api_write": [
        re.compile(
            r"gh api[^\n]*(?:--method\s+(?:POST|PATCH|PUT|DELETE))"
            r"|gh pr (?:review|create|merge)\b"
            r"|gh issue (?:create|edit|close)\b"
            r"|gh release create\b"
        ),
    ],
}


def _detect_capabilities(body: str, skill_name: str) -> set[str]:
    filtered = _strip_doc_fenced_blocks(body)
    # Collapse shell line continuations so multi-line gh api calls are detected
    filtered = re.sub(r"\\\n\s*", " ", filtered)
    detected: set[str] = set()
    for cap_name, patterns in _CAPABILITY_PATTERNS.items():
        for pat in patterns:
            if pat.search(filtered):
                detected.add(cap_name)
                break
    for line in filtered.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "autoskillit:" in stripped:
            if f"autoskillit:{skill_name}" not in stripped:
                detected.add("cross_skill_ref")
                break
    return detected


_INLINE_DETECTED_CAPS = {"cross_skill_ref"}

_pattern_keys = set(_CAPABILITY_PATTERNS) | _INLINE_DETECTED_CAPS
_registry_keys = set(SKILL_CAPABILITY_REGISTRY)
assert _pattern_keys == _registry_keys, (
    f"_CAPABILITY_PATTERNS + inline caps must match registry. "
    f"Missing: {_registry_keys - _pattern_keys}, "
    f"Extra: {_pattern_keys - _registry_keys}"
)


def test_forward_check_capabilities_declared():
    """Skills using a capability must declare it in uses_capabilities."""
    violations: list[str] = []
    for name, skill_md in _iter_skill_dirs():
        content = skill_md.read_text(encoding="utf-8")
        body = _strip_frontmatter(content)
        detected = _detect_capabilities(body, name)
        if not detected:
            continue
        fm = _read_skill_frontmatter(skill_md)
        declared = set(fm.get("uses_capabilities", []))
        missing = detected - declared
        if missing:
            violations.append(f"{name}: detected={sorted(detected)}, missing={sorted(missing)}")
    assert not violations, (
        f"{len(violations)} skill(s) use capabilities but don't declare them:\n"
        + "\n".join(f"  {v}" for v in violations)
    )


def test_reverse_check_annotation_justified():
    """Skills with backend_requirements must have at least one capability justifying it."""
    violations: list[str] = []
    for name, skill_md in _iter_skill_dirs():
        fm = _read_skill_frontmatter(skill_md)
        backend_reqs = set(fm.get("backend_requirements", []))
        if not backend_reqs:
            continue
        uses_caps = list(fm.get("uses_capabilities", []))
        if not uses_caps:
            violations.append(f"{name}: has backend_requirements but no uses_capabilities")
            continue
        derived: set[str] = set()
        for cap_name in uses_caps:
            cap_def = SKILL_CAPABILITY_REGISTRY.get(cap_name)
            if cap_def:
                derived |= cap_def.required_backends
        for req in backend_reqs:
            if req not in derived:
                violations.append(
                    f"{name}: backend_requirements includes '{req}' "
                    f"but no declared capability requires it"
                )
    assert not violations, (
        f"{len(violations)} annotation(s) not justified by capabilities:\n"
        + "\n".join(f"  {v}" for v in violations)
    )


def test_co_requirement_backend_requires_capabilities():
    """Non-empty backend_requirements → uses_capabilities must also be declared."""
    violations: list[str] = []
    for name, skill_md in _iter_skill_dirs():
        fm = _read_skill_frontmatter(skill_md)
        if fm.get("backend_requirements") and not fm.get("uses_capabilities"):
            violations.append(name)
    assert not violations, (
        f"{len(violations)} skill(s) have backend_requirements without uses_capabilities:\n"
        + "\n".join(f"  {v}" for v in violations)
    )


def test_derivation_backend_requirements_match_capabilities():
    """backend_requirements must NOT be declared in SKILL.md — derivation is runtime-only."""
    violations: list[str] = []
    for name, skill_md in _iter_skill_dirs():
        fm = _read_skill_frontmatter(skill_md)
        declared = fm.get("backend_requirements", [])
        if declared:
            violations.append(f"{name}: has backend_requirements={declared} in frontmatter")
    assert not violations, (
        f"{len(violations)} skill(s) still have backend_requirements in SKILL.md frontmatter "
        f"(should be derived at runtime):\n" + "\n".join(f"  {v}" for v in violations)
    )


def test_github_api_write_pattern_detected() -> None:
    """_CAPABILITY_PATTERNS["github_api_write"] matches GitHub write CLI patterns."""
    patterns = _CAPABILITY_PATTERNS["github_api_write"]
    should_match = [
        "gh pr review --approve",
        "gh api /repos/foo/bar --method POST",
        "gh pr create --title foo",
        "gh pr merge --squash",
        "gh issue create --title bar",
    ]
    should_not_match = [
        "gh pr list",
        "gh pr view 123",
        "gh issue list",
    ]
    for text in should_match:
        assert any(p.search(text) for p in patterns), (
            f"github_api_write pattern should match: {text!r}"
        )
    for text in should_not_match:
        assert not any(p.search(text) for p in patterns), (
            f"github_api_write pattern should NOT match: {text!r}"
        )


def test_review_pr_declares_github_api_write() -> None:
    """review-pr must declare github_api_write in uses_capabilities."""
    from autoskillit.core import pkg_root

    skill_md = pkg_root() / "skills_extended" / "review-pr" / "SKILL.md"
    fm = _read_skill_frontmatter(skill_md)
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
