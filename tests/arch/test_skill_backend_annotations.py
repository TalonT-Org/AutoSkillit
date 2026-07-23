"""Bidirectional capability annotation enforcement with semantic evidence detection.

Forward: skill content using a capability → uses_capabilities must declare it.
Reverse: uses_capabilities declarations → genuine self-initiated operation evidence.
Derivation: backend_requirements remains runtime-only.
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
    "open_kitchen": [],
    "run_skill": [],
    "test_check": [],
    "claude_dir": [re.compile(r"\.claude/")],
    "commit_files": [re.compile(r"\bcommit_files\s*\(")],
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

_SELF_INITIATED_TOOL_CAPABILITIES: dict[str, tuple[str, ...]] = {
    "open_kitchen": ("open_kitchen", "close_kitchen"),
    "run_skill": ("run_skill",),
    "test_check": ("test_check",),
}

_NON_OPERATION_CONTEXT = re.compile(
    r"\b(?:"
    r"called by|calls this via|invoked by|launched by|"
    r"do not|don't|never|must not|cannot|can't|without|skip|"
    r"returns?|returned|result|output|response|warning|denied|blocked|"
    r"configuration|config key|frontmatter|documentation|artifact|"
    r"gated behind|generated recipe"
    r")\b",
    re.IGNORECASE,
)


def _has_self_initiated_tool_operation(filtered: str, tool_name: str) -> bool:
    """Detect an outbound tool operation, excluding transport and documentary prose."""
    tool = re.escape(tool_name)
    direct_call = re.compile(rf"\b{tool}\s*\(")
    imperative = re.compile(
        rf"\b(?:call|run|invoke|use|execute|retry|re-run|test it)\b"
        rf"[^\n]{{0,100}}\b{tool}\b",
        re.IGNORECASE,
    )
    configuration = re.compile(
        rf"(?:\buses_capabilities\s*:|\btool\s*:|\b{tool}\.(?:command|commands)\b)",
        re.IGNORECASE,
    )

    for raw_line in filtered.splitlines():
        line = raw_line.strip().strip("`")
        if not line or line.startswith("#"):
            continue
        if configuration.search(line) or _NON_OPERATION_CONTEXT.search(line):
            continue
        if direct_call.search(line) or imperative.search(line):
            return True
    return False


def _has_graphql_mutation_operation(filtered: str) -> bool:
    """Default-POST ``gh api graphql`` is a write when its query is a mutation."""
    return bool(
        re.search(r"\bgh\s+api\s+graphql\b", filtered)
        and re.search(r"\bmutation\b", filtered, re.IGNORECASE)
    )


_EXCLUDED_SECTION_HEADINGS = (
    "Related Skills",
    "See also",
    "See Also",
)

_EXCLUDED_PROSE_PHRASES = (
    "consider running",
    "you may want to",
    "you could run",
    "produced by",
    "consumed by",
    "called by",
    "written by",
)


_NAMING_EXCLUSION_WORDS: frozenset[str] = frozenset(
    {"prefix", "convention", "when", "format", "syntax", "naming"}
)

_IMPERATIVE_VERBS = (
    "use",
    "run",
    "invoke",
    "load",
    "spawn",
    "call",
    "dispatch",
    "execute",
    "launch",
    "trigger",
)


def _has_imperative_cross_skill_invocation(stripped: str) -> bool:
    """Return True if stripped line begins with an imperative verb + `/autoskillit:`.

    Matches instruction-style lines like:
      - "Use `/autoskillit:mermaid` skill ..."
      - "Run `/autoskillit:dry-walkthrough` on the file ..."
      - "Invoke the /autoskillit:rectify skill to repair ..."
      - "Spawn all subagents via `/autoskillit:foo`"
      - "- Use `/autoskillit:retry-worktree` ..."

    Excludes Agent() subagent dispatch (subagent_type:) and prose-only mentions
    of siblings that do not require skill-tool invocation.
    """
    if "subagent_type:" in stripped:
        return False
    core = stripped.lstrip("-* ").strip()
    lower = core.lower()
    if "skill tool" in lower:
        return False  # handled by the explicit "skill tool" detector
    has_skill_word = (
        " skill " in lower
        or lower.endswith(" skill")
        or " skill." in lower
        or " skill," in lower
        or " skill;" in lower
        or " skill:" in lower
        or " skill/" in lower
        or " skill (" in lower
        or "/skill" in lower
        or "skill `" in lower
    )
    has_run_skill_invocation = "run_skill" in lower and "/autoskillit:" in lower
    for verb in _IMPERATIVE_VERBS:
        if (
            lower.startswith(verb + " ")
            or lower.startswith(verb + " the ")
            or lower.startswith(verb + " all ")
        ):
            if "/autoskillit:" in lower and (has_skill_word or has_run_skill_invocation):
                after_verb = lower.split(verb + " ", 1)[1] if verb + " " in lower else ""
                if after_verb.startswith("`/autoskillit:"):
                    rest = (
                        after_verb.split("`/autoskillit:", 1)[1]
                        if "`/autoskillit:" in after_verb
                        else ""
                    )
                    if not rest or rest.startswith("open-") or rest.startswith("close-"):
                        pass  # fall through
                    else:
                        first_word = (
                            rest.lstrip("` ").split()[0].rstrip(",.;:`'\"") if rest.split() else ""
                        )
                        if first_word in _NAMING_EXCLUSION_WORDS:
                            return False
                return True
            if "/autoskillit:" in lower and verb in {
                "use",
                "run",
                "invoke",
                "spawn",
                "call",
                "execute",
            }:
                after_prefix = lower.split(verb + " ", 1)[1] if verb + " " in lower else ""
                if after_prefix.startswith("`/autoskillit:") or after_prefix.startswith(
                    "the `/autoskillit:"
                ):
                    rest = (
                        after_prefix.split("`/autoskillit:", 1)[1]
                        if "`/autoskillit:" in after_prefix
                        else ""
                    )
                    if not rest:
                        return True
                    if rest.startswith("open-") or rest.startswith("close-"):
                        return False
                    first_word = (
                        rest.lstrip("` ").split()[0].rstrip(",.;:`'\"") if rest.split() else ""
                    )
                    if first_word in _NAMING_EXCLUSION_WORDS:
                        return False
                    return True
    return False


def _has_slash_command_invocation(stripped: str) -> bool:
    """Detect pure `/autoskillit:<name>` slash-command invocation patterns.

    Matches directive lines like:
      - "/autoskillit:vis-lens-{slug1} {source_dir} {ctx_path}"
      - "/autoskillit:issue-splitter --issue {N} --repo {owner}"
      - "/autoskillit:build-execution-map --assess-review-approach"

    Excludes example/wrong/related contexts and parenthetical references.
    """
    if not stripped.startswith("/autoskillit:"):
        return False
    if stripped.startswith("/autoskillit:{"):
        return False
    lower = stripped.lower()
    if " e.g." in lower or lower.startswith("e.g.") or "(e.g." in lower:
        return False
    if "wrong" in lower and (
        "**wrong" in lower or "wrong:**" in lower or "wrong example" in lower
    ):
        return False
    if "right" in lower and ("**right" in lower or "right:**" in lower or "correct:" in lower):
        return False
    if stripped.startswith("`") and (
        stripped.endswith("`)")
        or stripped.endswith("`)")
        or stripped.endswith("`,")
        or stripped.endswith("`).")
        or stripped.endswith("`;")
    ):
        return False
    if stripped.startswith("`") and stripped.endswith("`"):
        return False
    return True


def _is_genuine_cross_skill_ref_line(line: str, skill_name: str) -> bool:
    """Return True if a line contains a genuine Skill-tool invocation of a sibling skill.

    Excludes self-references, advisory prose, pipeline lineage, See-Also footers,
    and Agent() subagent dispatch (which is covered by agent_subagent).
    """
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return False
    if "autoskillit:" not in stripped:
        return False
    if f"autoskillit:{skill_name}" in stripped:
        return False
    if "Agent(subagent_type=" in stripped:
        return False
    lower = stripped.lower()
    for phrase in _EXCLUDED_PROSE_PHRASES:
        if phrase in lower:
            return False
    if "load" in lower and "skill tool" in lower:
        return True
    if "invoke" in lower and "skill tool" in lower:
        return True
    if 'run_skill("/autoskillit:' in stripped or "run_skill('/autoskillit:" in stripped:
        return True
    if 'Skill("/autoskillit:' in stripped or "Skill('/autoskillit:" in stripped:
        return True
    if _has_imperative_cross_skill_invocation(stripped):
        return True
    if _has_slash_command_invocation(stripped):
        return True
    if "run_skill" in lower and "/autoskillit:" in lower:
        return True
    return False


def _detect_cross_skill_ref(filtered: str, skill_name: str) -> bool:
    """Detect genuine cross_skill_ref by scanning lines with heading context.

    Returns True if at least one line under a non-excluded heading contains a
    genuine Skill-tool invocation pattern AND the line is not under a heading
    that is a See-Also / Related-Skills footer.
    """
    current_section_is_excluded = False
    in_skill_table_header = False
    lines = filtered.splitlines()
    for idx, raw_line in enumerate(lines):
        stripped = raw_line.strip()
        if stripped.startswith("#"):
            heading_text = stripped.lstrip("#").strip()
            current_section_is_excluded = any(
                h.lower() == heading_text.lower() for h in _EXCLUDED_SECTION_HEADINGS
            )
            in_skill_table_header = False
            continue
        if stripped.startswith("|"):
            if "skill" in stripped.lower():
                in_skill_table_header = True
            continue
        if current_section_is_excluded:
            continue
        if stripped.startswith("|") and not in_skill_table_header:
            continue
        lower = stripped.lower()
        if (
            "skill tool" in lower
            and ("load" in lower or "call" in lower or "use" in lower or "invoke" in lower)
            and "/autoskillit:" not in stripped
        ):
            for jdx in range(idx + 1, min(idx + 4, len(lines))):
                next_stripped = lines[jdx].strip()
                if not next_stripped:
                    continue
                if next_stripped.startswith("#"):
                    break
                if (
                    "/autoskillit:" in next_stripped
                    and f"autoskillit:{skill_name}" not in next_stripped
                ):
                    return True
                if "skill tool" in next_stripped.lower():
                    break
                break
        if _is_genuine_cross_skill_ref_line(raw_line, skill_name):
            return True
    return False


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
    for cap_name, tool_names in _SELF_INITIATED_TOOL_CAPABILITIES.items():
        if any(_has_self_initiated_tool_operation(filtered, tool) for tool in tool_names):
            detected.add(cap_name)
    if _has_graphql_mutation_operation(filtered):
        detected.add("github_api_write")
    if _detect_cross_skill_ref(filtered, skill_name):
        detected.add("cross_skill_ref")
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


def test_reverse_check_capability_declarations_are_genuine():
    """Every declared capability must have genuine self-initiated evidence."""
    violations: list[str] = []
    for name, skill_md in _iter_skill_dirs():
        content = skill_md.read_text(encoding="utf-8")
        body = _strip_frontmatter(content)
        detected = _detect_capabilities(body, name)
        fm = _read_skill_frontmatter(skill_md)
        declared = set(fm.get("uses_capabilities", []))
        unsupported = declared - detected
        if unsupported:
            violations.append(
                f"{name}: declared={sorted(declared)}, without_evidence={sorted(unsupported)}"
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


_CROSS_SKILL_REF_ALLOWLIST: dict[str, str] = {}


def test_cross_skill_ref_declarations_are_genuine():
    """Skills declaring cross_skill_ref must have a genuine Skill-tool invocation."""
    violations: list[str] = []
    for name, skill_md in _iter_skill_dirs():
        fm = _read_skill_frontmatter(skill_md)
        declared = set(fm.get("uses_capabilities", []))
        if "cross_skill_ref" not in declared:
            continue
        if name in _CROSS_SKILL_REF_ALLOWLIST:
            continue
        content = skill_md.read_text(encoding="utf-8")
        body = _strip_frontmatter(content)
        if not _detect_cross_skill_ref(_strip_doc_fenced_blocks(body), name):
            violations.append(name)
    assert not violations, (
        f"{len(violations)} skill(s) declare cross_skill_ref without genuine "
        f"Skill-tool invocation:\n" + "\n".join(f"  {v}" for v in violations)
    )
