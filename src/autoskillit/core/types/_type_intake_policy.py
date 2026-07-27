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
    "render_intake_digest",
    "CODEX_INTAKE_DISCIPLINE_DIGEST",
]

CODEX_INTAKE_DISCIPLINE_VERSION: Final[int] = 2

# Always-on injection: every byte is replicated into 11 bundled agent TOMLs at session
# setup plus one copy per session prompt across 5 delivery surfaces. Raising either
# ceiling is a decision, not a consequence — record measured before/after in the PR.
CODEX_INTAKE_DISCIPLINE_BYTE_BUDGET: Final[int] = 1200
CODEX_DISCIPLINE_SUFFIX_BYTE_BUDGET: Final[int] = 3000


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
