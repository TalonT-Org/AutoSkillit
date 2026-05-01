import re
from pathlib import Path

REPO_ROOT = Path(__file__).parents[2]
ORCH_DOC = REPO_ROOT / "docs" / "orchestration-levels.md"
GLOSSARY = REPO_ROOT / "docs" / "glossary.md"


def test_orchestration_levels_doc_exists():
    assert ORCH_DOC.exists(), "docs/orchestration-levels.md must be created"


def test_orchestration_levels_has_required_sections():
    text = ORCH_DOC.read_text()
    for heading in ["## Level Definitions", "## Mapping Table", "## Key Rules",
                    "## Disambiguation"]:
        assert heading in text, f"Missing section: {heading}"


def test_orchestration_levels_has_ascii_trees():
    text = ORCH_DOC.read_text()
    # ASCII trees use box-drawing or plain characters; check L-nodes appear
    for level in ["L0", "L1", "L2", "L3"]:
        assert level in text


def test_glossary_has_orchestration_level_entries():
    text = GLOSSARY.read_text()
    for term in ["### L0", "### L1", "### L2", "### L3",
                 "### food truck", "### Ghost Kitchen"]:
        assert term in text, f"Glossary missing entry: {term}"


def test_glossary_orchestrator_entry_uses_l2():
    text = GLOSSARY.read_text()
    # Find the orchestrator section
    match = re.search(r"### orchestrator\n(.+?)(?=\n###|\Z)", text, re.DOTALL)
    assert match, "Glossary missing ### orchestrator entry"
    section = match.group(1)
    assert "L2" in section, "'orchestrator' glossary entry must reference L2"
    assert "Tier 1" not in section, "'orchestrator' entry must not use 'Tier 1' language"


def test_glossary_worker_entry_uses_l1():
    text = GLOSSARY.read_text()
    match = re.search(r"### worker\n(.+?)(?=\n###|\Z)", text, re.DOTALL)
    assert match, "Glossary missing ### worker entry"
    section = match.group(1)
    assert "L1" in section, "'worker' glossary entry must reference L1"
    assert "Tier 2" not in section, "'worker' entry must not use 'Tier 2' language"


def test_orchestration_doc_cross_references_levels():
    orch_exec = REPO_ROOT / "docs" / "execution" / "orchestration.md"
    text = orch_exec.read_text()
    assert "orchestration-levels.md" in text, \
        "docs/execution/orchestration.md must cross-reference orchestration-levels.md"


def test_claude_md_has_il_disambiguation():
    claude_md = REPO_ROOT / "CLAUDE.md"
    text = claude_md.read_text()
    # The disambiguation must mention import layers and IL-N notation
    assert "IL-" in text and "import" in text.lower(), \
        "CLAUDE.md Section 6 must contain IL-N disambiguation sentence"
