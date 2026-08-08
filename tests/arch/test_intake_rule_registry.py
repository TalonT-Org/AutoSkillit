"""Structural guards for the Codex intake-rule registry (#4351).

Every injected instruction rule must be evidence-bound, exception-qualified, and
must not name a path class the AutoSkillit harness denies.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from autoskillit.core import (
    CODEX_INTAKE_DISCIPLINE_DIGEST,
    CODEX_INTAKE_DISCIPLINE_VERSION,
    CODEX_INTAKE_RULES,
    RETIRED_INTAKE_RULE_IDS,
    render_intake_digest,
)
from autoskillit.execution.backends._claude_prompt import CODEX_CO_INJECTED_POLICIES
from autoskillit.hooks._command_classification import command_has_blocked_protected_path_read
from autoskillit.hooks.guards.recipe_read_guard import _CMD_PATH_PATTERNS

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

_ABSOLUTE_RE = re.compile(
    r"(?<![\w-])(never|always|do not|don't|must|under no circumstances|no exceptions)(?![\w-])",
    re.IGNORECASE,
)
_MIN_ANCHOR_LEN = 12

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_SRC_ROOT = _PROJECT_ROOT / "src" / "autoskillit"

# Probes are shell commands, not paths — the predicate takes a command string.
_PROBE_TEMPLATE = "sed -n '1,250p' {path}"

_PATH_CLASS_PROBE_PATHS: dict[str, tuple[str, ...]] = {
    "session-skill-md": ("/dev/shm/autoskillit-sessions/abc123/skills/resolve-review/SKILL.md",),
    "plan-file": (".autoskillit/temp/rectify/rectify_x_2026-07-26.md",),
    "data-file": (".autoskillit/temp/review-pr/annotated_diff_4345.txt",),
    # agents-md is GENERATED from the live tree, not hand-listed — see below.
}

# The generated sweep discovers the live root-and-source guide set.
# Only the listed source guide is blocked, for the reason recorded below.
KNOWN_BLOCKED_AGENTS_MD: dict[str, str] = {
    "src/autoskillit/agents/AGENTS.md": (
        "Matches recipe_read_guard.py:31 (src/autoskillit/agents/.*\\.md), which exists to "
        "stop agent definitions being re-read from disk after compaction. Agent definitions "
        "reach a session through generated TOMLs, not through this file."
    ),
}

# One positive control per _CMD_PATH_PATTERNS entry: proves the predicate is live.
_BLOCKED_CONTROLS = (
    "src/autoskillit/recipes/implementation.yaml",
    "src/autoskillit/skills_extended/rectify/SKILL.md",
    "src/autoskillit/agents/wp-elaborator.md",
)


def _agents_md_probe_paths() -> list[str]:
    found = [_PROJECT_ROOT / "AGENTS.md", *_SRC_ROOT.rglob("AGENTS.md")]
    return sorted(
        p.relative_to(_PROJECT_ROOT).as_posix()
        for p in found
        if p.is_file() and "__pycache__" not in str(p)
    )


def test_rule_ids_are_unique_kebab_case() -> None:
    ids = [rule.id for rule in CODEX_INTAKE_RULES]
    assert len(ids) == len(set(ids)), f"Duplicate rule ids: {ids}"
    bad = [i for i in ids if not re.fullmatch(r"^[a-z][a-z0-9]*(-[a-z0-9]+)*$", i)]
    assert not bad, f"Rule ids must be kebab-case: {bad}"


def test_no_absolute_imperative_without_a_declared_exception() -> None:
    for rule in CODEX_INTAKE_RULES:
        if _ABSOLUTE_RE.search(rule.text):
            assert len(rule.exception.strip()) >= 20, (
                f"Rule {rule.id!r} states an absolute but has no declared exception"
            )


def test_every_evidence_anchor_is_a_fragment_of_its_own_rule() -> None:
    for rule in CODEX_INTAKE_RULES:
        assert len(rule.evidence_anchor) >= _MIN_ANCHOR_LEN, (
            f"Rule {rule.id!r} evidence_anchor is shorter than {_MIN_ANCHOR_LEN} chars"
        )
        assert rule.evidence_anchor in rule.text, (
            f"Rule {rule.id!r} evidence_anchor is not a substring of its own text"
        )


@pytest.mark.medium
def test_backend_capability_basis_matches_live_value() -> None:
    from autoskillit.execution.backends import BACKEND_REGISTRY

    for rule in CODEX_INTAKE_RULES:
        if rule.basis != "backend-capability":
            continue
        backend_name, _, field_name = rule.evidence.partition(".")
        assert backend_name and field_name, (
            f"Rule {rule.id!r} evidence must be '<backend>.<capability_field>': {rule.evidence!r}"
        )
        capabilities = BACKEND_REGISTRY[backend_name]().capabilities
        value = getattr(capabilities, field_name)
        assert value, f"Rule {rule.id!r} evidence field {field_name!r} resolved falsy"
        assert str(value) in rule.text, (
            f"Rule {rule.id!r} text does not contain the live capability value {value!r}"
        )


def test_doc_basis_resolves_and_anchor_appears_in_the_cited_doc() -> None:
    for rule in CODEX_INTAKE_RULES:
        if rule.basis not in {"adr", "local-policy"}:
            continue
        if re.fullmatch(r"^#\d+$", rule.evidence):
            continue
        doc_path = _PROJECT_ROOT / rule.evidence
        assert doc_path.is_file(), f"Rule {rule.id!r} evidence doc does not exist: {rule.evidence}"
        doc_text = doc_path.read_text(encoding="utf-8")
        assert rule.evidence_anchor.lower() in doc_text.lower(), (
            f"Rule {rule.id!r} evidence_anchor {rule.evidence_anchor!r} not found in "
            f"{rule.evidence}"
        )


def test_issue_basis_cites_a_plausible_issue_number() -> None:
    for rule in CODEX_INTAKE_RULES:
        if rule.basis != "local-policy" or not re.fullmatch(r"^#\d+$", rule.evidence):
            continue
        assert int(rule.evidence.lstrip("#")) >= 1, (
            f"Rule {rule.id!r} evidence issue number must be >= 1: {rule.evidence}"
        )


def test_upstream_aligned_basis_carries_a_citation() -> None:
    for rule in CODEX_INTAKE_RULES:
        if rule.basis != "upstream-aligned":
            continue
        assert re.fullmatch(r"openai/codex#\d+", rule.evidence) or re.match(
            r"codex-rs/\S+", rule.evidence
        ), (
            f"Rule {rule.id!r} upstream-aligned evidence must cite an issue or file: "
            f"{rule.evidence}"
        )


def test_no_rule_names_a_path_class_the_harness_denies() -> None:
    """A False result means the path class is not in the harness's denied set —

    not that the harness has affirmatively authorised the read. That narrower claim
    is the one that matters: it is exactly what would have blocked a recipe-yaml
    carve-out.
    """
    seen_agents_md = False
    for rule in CODEX_INTAKE_RULES:
        for path_class in rule.path_classes:
            if path_class == "agents-md":
                probe_paths = _agents_md_probe_paths()
                seen_agents_md = True
            else:
                probe_paths = list(_PATH_CLASS_PROBE_PATHS[path_class])
            for probe_path in probe_paths:
                cmd = _PROBE_TEMPLATE.format(path=probe_path)
                is_blocked = command_has_blocked_protected_path_read(cmd, _CMD_PATH_PATTERNS)
                if probe_path in KNOWN_BLOCKED_AGENTS_MD:
                    assert is_blocked, (
                        f"{probe_path} is a declared exception but is no longer blocked — "
                        "remove it from KNOWN_BLOCKED_AGENTS_MD"
                    )
                else:
                    assert not is_blocked, (
                        f"Rule {rule.id!r} path_class {path_class!r} probe {probe_path} is "
                        "blocked by the harness and has no declared exception"
                    )

    if seen_agents_md:
        for blocked_path in KNOWN_BLOCKED_AGENTS_MD:
            cmd = _PROBE_TEMPLATE.format(path=blocked_path)
            assert command_has_blocked_protected_path_read(cmd, _CMD_PATH_PATTERNS), (
                f"{blocked_path} must still be blocked by the harness"
            )

    for control_path in _BLOCKED_CONTROLS:
        cmd = _PROBE_TEMPLATE.format(path=control_path)
        assert command_has_blocked_protected_path_read(cmd, _CMD_PATH_PATTERNS), (
            f"Positive control {control_path} must be blocked — the predicate may be dead"
        )


def test_rendered_digest_equals_registry_render() -> None:
    assert CODEX_INTAKE_DISCIPLINE_DIGEST == render_intake_digest(
        CODEX_INTAKE_RULES, CODEX_INTAKE_DISCIPLINE_VERSION
    )


def test_subagent_parent_wait_rule_is_concise_and_exception_qualified() -> None:
    (rule,) = (rule for rule in CODEX_INTAKE_RULES if rule.id == "subagent-parent-wait")

    assert len(rule.text.encode("utf-8")) <= 140
    assert "wait for every active sub-agent" in rule.text
    assert "only do work the user explicitly requests" in rule.text
    assert rule.exception == (
        "The user may explicitly request work while sub-agents remain active."
    )
    assert rule.text in CODEX_INTAKE_DISCIPLINE_DIGEST


def test_digest_header_carries_the_version() -> None:
    assert CODEX_INTAKE_DISCIPLINE_DIGEST.startswith(
        f"Context Intake Discipline v{CODEX_INTAKE_DISCIPLINE_VERSION}:"
    )


def test_every_rule_subject_is_declared_for_the_intake_digest() -> None:
    rule_subjects = {rule.subject for rule in CODEX_INTAKE_RULES}
    (matrix_entry,) = (
        entry
        for entry in CODEX_CO_INJECTED_POLICIES
        if entry.constant_name == "CODEX_INTAKE_DISCIPLINE_DIGEST"
    )
    assert rule_subjects == set(matrix_entry.subjects)


def test_no_retired_intake_rule_id_is_live() -> None:
    live_ids = {rule.id for rule in CODEX_INTAKE_RULES}
    overlap = live_ids & set(RETIRED_INTAKE_RULE_IDS)
    assert overlap == set(), (
        f"Retired intake-rule ids must not appear in CODEX_INTAKE_RULES: {overlap}"
    )


def test_retired_intake_rule_ids_are_lowercase_and_kebab_case() -> None:
    kebab_re = re.compile(r"^[a-z][a-z0-9-]*$")
    for retired_id in RETIRED_INTAKE_RULE_IDS:
        assert retired_id, "RETIRED_INTAKE_RULE_IDS entries must be non-empty"
        assert kebab_re.fullmatch(retired_id), (
            f"Retired intake-rule id {retired_id!r} must match {kebab_re.pattern!r}"
        )


def test_data_file_bounded_read_exception_does_not_reference_retired_rules() -> None:
    (rule,) = (rule for rule in CODEX_INTAKE_RULES if rule.id == "data-file-bounded-read")
    assert "completeness rule" not in rule.exception, (
        f"data-file-bounded-read.exception still references retired completeness rule: "
        f"{rule.exception!r}"
    )
    assert "completeness rule above" not in rule.exception


def test_intake_rule_wire_order_is_stable() -> None:
    assert [rule.id for rule in CODEX_INTAKE_RULES] == [
        "data-file-bounded-read",
        "outer-result-token-ceiling",
        "agents-md-package-table",
        "subagent-fresh-context",
        "subagent-parent-wait",
    ]


def test_retired_intake_rule_ids_gateway_is_a_frozenset() -> None:
    """`RETIRED_INTAKE_RULE_IDS` must be reachable via the public `autoskillit.core` gateway.

    Companion to `test_no_retired_intake_rule_id_is_live`; proves the IL-0
    hard constraint (production code imports only from `autoskillit.core`,
    never from `autoskillit.core.types` directly) is honoured for the new
    retirement registry.
    """
    from autoskillit.core import RETIRED_INTAKE_RULE_IDS as GatewayRetiredIntakeRuleIds

    assert isinstance(GatewayRetiredIntakeRuleIds, frozenset)
    assert GatewayRetiredIntakeRuleIds is RETIRED_INTAKE_RULE_IDS
