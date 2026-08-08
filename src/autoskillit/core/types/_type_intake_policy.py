"""Codex context-intake rule registry — evidence-bound Codex instruction-reading policy.

Zero autoskillit imports.
"""

from __future__ import annotations

from typing import Final, Literal, NamedTuple

__all__ = [
    "IntakeRuleDef",
    "CODEX_INTAKE_RULES",
    "CODEX_INTAKE_DISCIPLINE_VERSION",
    "CODEX_INTAKE_DISCIPLINE_BYTE_BUDGET",
    "CODEX_DISCIPLINE_SUFFIX_BYTE_BUDGET",
    "CODEX_SCOPE_DISCIPLINE_BYTE_BUDGET",
    "CODEX_SCOPE_DISCIPLINE_DIGEST",
    "render_intake_digest",
    "CODEX_INTAKE_DISCIPLINE_DIGEST",
]

CODEX_INTAKE_DISCIPLINE_VERSION: Final[int] = 3

# Always-on injection: every byte is replicated into 13 bundled agent TOMLs at session
# setup plus one copy per session prompt across 5 delivery surfaces. Raising either
# ceiling is a decision, not a consequence — record measured before/after in the PR.
CODEX_INTAKE_DISCIPLINE_BYTE_BUDGET: Final[int] = 1200
# Governs the UNIVERSAL composed suffix only (output-discipline + intake-discipline +
# recipe-delivery-contract — codex_discipline_suffix()'s default form), delivered to
# every Codex session including bundled agent TOMLs. The scope digest is a separate
# change-authoring policy
# (CODEX_SCOPE_DISCIPLINE_DIGEST, own budget below) delivered only to skill sessions
# whose contract declares `scope_discipline: true`, to interactive TUI sessions
# (deliberate — task unknown at launch), and to resumes that opt in explicitly via
# codex_discipline_suffix(include_scope=True).
CODEX_DISCIPLINE_SUFFIX_BYTE_BUDGET: Final[int] = 3150
CODEX_SCOPE_DISCIPLINE_BYTE_BUDGET: Final[int] = 1700


class IntakeRuleDef(NamedTuple):
    """One context-intake rule injected into Codex sessions.

    ``basis``/``evidence``/``evidence_anchor`` are what make the rule mergeable: a
    guard resolves the evidence and fails when it does not exist or no longer says
    what the rule claims. ``exception`` is mandatory whenever the text states an
    absolute — an unqualified imperative is the failure mode behind #4351, #4265 and
    #4373. ``path_classes`` names the file classes the rule directs the agent to read,
    so a guard can prove none of them is in the harness's denied set.
    """

    id: str
    subject: str
    text: str
    basis: Literal["backend-capability", "adr", "upstream-aligned", "local-policy"]
    evidence: str
    evidence_anchor: str
    exception: str
    path_classes: tuple[str, ...]


CODEX_INTAKE_RULES: Final[tuple[IntakeRuleDef, ...]] = (
    IntakeRuleDef(
        id="instruction-file-completeness",
        subject="instruction-file-intake",
        text=(
            "Instruction files you are about to act on — the SKILL.md you selected and "
            "any plan file your task names — must be read completely before you act on "
            "them; if a read is truncated or paginated, continue until EOF."
        ),
        basis="upstream-aligned",
        evidence="openai/codex#27044",
        evidence_anchor="must be read completely",
        exception=(
            "Recipe YAML and bundled source-tree SKILL.md paths are never read from disk "
            "at all — recipes arrive via load_recipe / get_recipe_section and skills via "
            "the Skill tool."
        ),
        path_classes=("session-skill-md", "plan-file"),
    ),
    IntakeRuleDef(
        id="data-file-bounded-read",
        subject="data-file-intake",
        text=(
            "For data, log, and source files, locate the region first with `rg -n` and "
            "read only that region; keep each `sed -n` range to at most 250 lines."
        ),
        basis="local-policy",
        evidence="#4280",
        evidence_anchor="at most 250 lines",
        exception=(
            "Instruction files covered by the completeness rule above are exempt from this bound."
        ),
        path_classes=("data-file",),
    ),
    IntakeRuleDef(
        id="outer-result-token-ceiling",
        subject="tool-result-budget",
        text="Never pass max_output_tokens above 10000.",
        basis="backend-capability",
        evidence="codex.unnegotiated_tool_result_token_limit",
        evidence_anchor="max_output_tokens above 10000",
        exception=(
            "The single attested recipe-delivery call named in the recipe delivery "
            "calling contract is the only exemption."
        ),
        path_classes=(),
    ),
    IntakeRuleDef(
        id="agents-md-package-table",
        subject="package-table-orientation",
        text=(
            "Package tables in AGENTS.md files are an index, not required reading; "
            "consult a per-package AGENTS.md only for packages you are modifying — "
            "except `src/autoskillit/agents/`, whose definitions reach you as configured "
            "sub-agents and are not read from disk."
        ),
        basis="local-policy",
        evidence="AGENTS.md",
        evidence_anchor="an index, not required reading",
        exception="Read a package's own AGENTS.md when you are modifying that package.",
        path_classes=("agents-md",),
    ),
    IntakeRuleDef(
        id="subagent-fresh-context",
        subject="subagent-spawning",
        text=(
            'Spawn sub-agents with fresh context: pass fork_turns "none" explicitly '
            '(omitting fork_turns silently defaults to "all", forking the full parent '
            "conversation). Give each sub-agent an explicit narrow brief; sub-agents "
            "return a summary of their own task work, not raw file contents, and never "
            "read or interpret your instruction files on your behalf."
        ),
        basis="adr",
        evidence="docs/decisions/0005-output-budget-protocol.md",
        evidence_anchor='fork_turns "none"',
        exception="Sub-agents may still perform task work when the selected skill allows it.",
        path_classes=(),
    ),
    IntakeRuleDef(
        id="subagent-parent-wait",
        subject="subagent-spawning",
        text=(
            "After delegating, wait for every active sub-agent; do not duplicate their work. "
            "While they run, only do work the user explicitly requests."
        ),
        basis="local-policy",
        evidence="#4447",
        evidence_anchor="wait for every active sub-agent",
        exception="The user may explicitly request work while sub-agents remain active.",
        path_classes=(),
    ),
)


def render_intake_digest(
    rules: tuple[IntakeRuleDef, ...] = CODEX_INTAKE_RULES,
    version: int = CODEX_INTAKE_DISCIPLINE_VERSION,
) -> str:
    """Render the injected wire text from the rule registry."""
    return "\n".join(
        (f"Context Intake Discipline v{version}:", *(f"- {rule.text}" for rule in rules))
    )


CODEX_INTAKE_DISCIPLINE_DIGEST: Final[str] = render_intake_digest()

CODEX_SCOPE_DISCIPLINE_DIGEST: Final[str] = (
    "SCOPE DISCIPLINE (this backend tends to over-engineer; these rules are mandatory):\n"
    "S1. Maintainability is the goal. Every line you add must be read, understood, and kept\n"
    "    working by someone else. Volume the requirement genuinely forces is fine; volume\n"
    "    from speculation is not.\n"
    'S2. Build the smallest design that satisfies the stated requirements. "Immunity",\n'
    '    "architectural", or "contract" framing is not a mandate for maximal machinery —\n'
    "    deliver the minimal mechanism that closes the enumerated gaps. When the problem\n"
    "    truly needs a complex solution, build it.\n"
    "S3. No speculative machinery: do not introduce a new registry, enum, ID-wrapper/newtype\n"
    "    class, state machine, protocol, event vocabulary, or abstraction layer unless the\n"
    "    task text names it explicitly OR two existing call sites need it today. One concrete\n"
    "    implementation beats an extensible framework.\n"
    "S4. Reuse before invention: extend existing modules, types, and helpers. Do not rewrite,\n"
    '    "harden", or defensively refactor adjacent code the requirement does not touch.\n'
    "    Trace only the symbols you will modify or whose contracts you rely on.\n"
    "S5. Tests cover the behavior this change introduces — one focused test per new behavior\n"
    "    or reachable edge. No permutation matrices for hypothetical states. Prefer\n"
    "    parametrization over near-duplicate test bodies.\n"
    "S6. Reason as long as you need before writing — depth of thought is free; depth of code\n"
    "    is not. The finished change introduces the fewest new concepts that satisfy the\n"
    "    requirement, as deep modules: simple interfaces hiding substantial implementation,\n"
    "    never layers of shallow pass-throughs."
)

_RULE_IDS = [rule.id for rule in CODEX_INTAKE_RULES]
if len(_RULE_IDS) != len(set(_RULE_IDS)):
    raise AssertionError(f"CODEX_INTAKE_RULES ids must be unique: {_RULE_IDS}")
del _RULE_IDS

_ANCHOR_NOT_IN_TEXT = [
    rule.id for rule in CODEX_INTAKE_RULES if rule.evidence_anchor not in rule.text
]
if _ANCHOR_NOT_IN_TEXT:
    raise AssertionError(
        "evidence_anchor must be a literal substring of the rule's own text: "
        f"{_ANCHOR_NOT_IN_TEXT}"
    )
del _ANCHOR_NOT_IN_TEXT

if "'''" in CODEX_INTAKE_DISCIPLINE_DIGEST:
    raise AssertionError("CODEX_INTAKE_DISCIPLINE_DIGEST must not contain triple single-quotes")

_DIGEST_BYTES = len(CODEX_INTAKE_DISCIPLINE_DIGEST.encode("utf-8"))
if _DIGEST_BYTES > CODEX_INTAKE_DISCIPLINE_BYTE_BUDGET:
    raise AssertionError(
        f"CODEX_INTAKE_DISCIPLINE_DIGEST is {_DIGEST_BYTES} bytes, exceeding the "
        f"{CODEX_INTAKE_DISCIPLINE_BYTE_BUDGET}-byte budget"
    )
del _DIGEST_BYTES

_SCOPE_DIGEST_BYTES = len(CODEX_SCOPE_DISCIPLINE_DIGEST.encode("utf-8"))
if _SCOPE_DIGEST_BYTES > CODEX_SCOPE_DISCIPLINE_BYTE_BUDGET:
    raise AssertionError(
        f"CODEX_SCOPE_DISCIPLINE_DIGEST is {_SCOPE_DIGEST_BYTES} bytes, exceeding the "
        f"{CODEX_SCOPE_DISCIPLINE_BYTE_BUDGET}-byte budget"
    )
del _SCOPE_DIGEST_BYTES

if "'''" in CODEX_SCOPE_DISCIPLINE_DIGEST:
    raise AssertionError("CODEX_SCOPE_DISCIPLINE_DIGEST must not contain triple single-quotes")
