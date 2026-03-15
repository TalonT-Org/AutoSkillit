"""Cross-skill contract: content fidelity for issue body assembly.

Rules enforced:
- Any skill whose SKILL.md contains a "## From #" body-assembly section
  (indicating it assembles combined content from multiple source issues) must
  also document fetch_github_issue — the per-issue REST fetch that guarantees
  full, untruncated body content.
- No such skill may use angle-bracket placeholder syntax for body-copy
  instructions (<full body ...>, <content of issue N>, etc.).

These rules make the class of bug described in issue #372 structurally
impossible for any future skill with the same body-assembly pattern.
"""

from __future__ import annotations

import re

from autoskillit.workspace.skills import bundled_skills_dir


def _all_skill_mds() -> list[tuple[str, str]]:
    bd = bundled_skills_dir()
    return [
        (d.name, (d / "SKILL.md").read_text())
        for d in sorted(bd.iterdir())
        if d.is_dir() and (d / "SKILL.md").is_file()
    ]


def _body_assembling_skills() -> list[tuple[str, str]]:
    """Skills that assemble combined bodies from multiple source issues."""
    return [(name, text) for name, text in _all_skill_mds() if "## From #" in text]


def test_body_assembling_skills_use_per_issue_fetch() -> None:
    """Skills that assemble ## From #N sections must call fetch_github_issue per-issue.

    The bulk gh issue list endpoint can truncate body content. Any skill that
    assembles a combined document verbatim from source issue bodies must use the
    REST per-issue endpoint (via fetch_github_issue) to guarantee full content.
    A skill that has ## From # sections in its body template but no
    fetch_github_issue call is structurally incomplete.
    """
    failures: list[str] = []
    for skill_name, text in _body_assembling_skills():
        if "fetch_github_issue" not in text:
            failures.append(
                f"  {skill_name}: has '## From #' body assembly sections "
                f"but does not call fetch_github_issue per-issue"
            )
    assert not failures, (
        "Body-assembling skills must use fetch_github_issue for full content:\n"
        + "\n".join(failures)
    )


def test_body_assembling_skills_forbid_angle_bracket_copy_instructions() -> None:
    """Skills assembling verbatim issue bodies must not use angle-bracket placeholder syntax.

    <full body of issue N, verbatim> is parsed as a fill-in-the-blank template
    slot by the LLM. Any angle-bracket token referencing 'body' or 'content' of
    an issue in a body-assembly context indicates a broken copy instruction that
    will produce summaries or hyperlinks instead of the actual text.
    """
    pattern = re.compile(r"<[^>]*(body|content)\s+of\s+(issue|#)", re.IGNORECASE)
    failures: list[str] = []
    for skill_name, text in _body_assembling_skills():
        if pattern.search(text):
            failures.append(
                f"  {skill_name}: contains angle-bracket body-copy placeholder "
                f"syntax — use explicit imperative paste language instead"
            )
    assert not failures, (
        "Body-assembling skills must not use angle-bracket placeholder syntax "
        "for copy instructions:\n" + "\n".join(failures)
    )


def test_body_assembling_skills_have_never_summarize() -> None:
    """Skills assembling verbatim issue bodies must explicitly forbid summarization.

    Without an explicit NEVER constraint, an LLM in generative mode (just
    finished title synthesis) defaults to concise output, producing one-sentence
    summaries or hyperlinks for body sections it should copy verbatim.
    """
    summarize_terms = re.compile(r"summarize|paraphrase|abbreviate", re.IGNORECASE)
    failures: list[str] = []
    for skill_name, text in _body_assembling_skills():
        if not summarize_terms.search(text):
            failures.append(
                f"  {skill_name}: has body assembly sections but no explicit "
                f"NEVER constraint against summarizing/paraphrasing source content"
            )
    assert not failures, (
        "Body-assembling skills must explicitly forbid summarization in their NEVER block:\n"
        + "\n".join(failures)
    )
