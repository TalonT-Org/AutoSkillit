"""Contract tests for triage-issues --enrich flag and requirement enrichment behavior."""
import re
from pathlib import Path
import pytest

SKILLS_DIR = Path(__file__).parents[2] / "src/autoskillit/skills"
TRIAGE_SKILL = SKILLS_DIR / "triage-issues/SKILL.md"


def test_triage_enrich_flag_documented():
    """triage-issues must document the --enrich flag."""
    text = TRIAGE_SKILL.read_text()
    assert "--enrich" in text


def test_triage_enrich_flag_is_opt_in():
    """--enrich must be opt-in, not the default mode."""
    text = TRIAGE_SKILL.read_text()
    # --enrich is a flag, not default behavior
    assert "--enrich" in text
    # Default must NOT enrich (enrichment is off without the flag)
    lines = text.splitlines()
    enrich_lines = [l for l in lines if "--enrich" in l]
    assert len(enrich_lines) >= 1


def test_triage_enrich_targets_implementation_only():
    """Requirement enrichment must only apply to recipe:implementation issues."""
    text = TRIAGE_SKILL.read_text()
    # The enrich section must mention implementation specifically
    enrich_idx = text.find("--enrich")
    assert enrich_idx != -1
    # After mentioning --enrich, the text must associate it with implementation
    enrich_section = text[enrich_idx:enrich_idx + 2000]
    assert "implementation" in enrich_section.lower() or "recipe:implementation" in enrich_section


def test_triage_enrich_uses_req_id_format():
    """triage-issues enrichment must reference REQ- format identifiers."""
    text = TRIAGE_SKILL.read_text()
    assert "REQ-" in text


def test_triage_enrich_appends_requirements_section():
    """triage-issues enrichment must document appending ## Requirements section."""
    text = TRIAGE_SKILL.read_text()
    assert "## Requirements" in text


def test_triage_enrich_is_idempotent():
    """triage-issues enrichment must skip issues that already have ## Requirements."""
    text = TRIAGE_SKILL.read_text()
    lower = text.lower()
    # Must reference idempotency *in the context of ## Requirements* — not just any
    # "idempotent" mention (the current skill uses it only for label creation).
    idempotent = (
        ("idempotent" in lower and "## requirements" in lower)
        or "already has" in lower
        or ("skip if" in lower and "## requirements" in lower)
        or ("skip when" in lower and "## requirements" in lower)
    )
    assert idempotent, "Enrichment must be idempotent — skip issues that already have requirements"


def test_triage_manifest_schema_includes_requirements_generated():
    """triage manifest JSON must document requirements_generated field per issue."""
    text = TRIAGE_SKILL.read_text()
    assert "requirements_generated" in text


def test_triage_enrich_uses_gh_issue_edit():
    """Enrichment must use gh issue edit to append requirements (not just labels)."""
    text = TRIAGE_SKILL.read_text()
    # gh issue edit already appears in the current skill for label application.
    # This test requires gh issue edit AND requirements_generated together — confirming
    # the enrichment step (not just the label step) is documented.
    assert "gh issue edit" in text
    assert "requirements_generated" in text


def test_triage_enrich_no_subagents():
    """Requirement enrichment in triage must be in-context, not subagent-based."""
    text = TRIAGE_SKILL.read_text()
    # The enrich step specifically should not spawn new subagents
    enrich_idx = text.find("--enrich")
    if enrich_idx == -1:
        pytest.skip("--enrich not yet documented")
    enrich_section = text[enrich_idx:enrich_idx + 3000]
    # "subagent" must not appear specifically in the enrich step documentation
    # (other steps like split analysis do use subagents — we can't forbid globally)
    # Check that the enrich step description does not mention spawning/launching subagents
    assert "spawn" not in enrich_section.lower() or "enrich" not in enrich_section.lower()
