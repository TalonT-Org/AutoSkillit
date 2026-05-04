# docs/

Documentation integrity, link validity, and naming convention tests.

## Files

| File | Purpose |
|------|---------|
| `__init__.py` | empty |
| `test_banned_phrases.py` | Reject AI-tone banned phrases in every doc (derived from REQ-DOC-070) |
| `test_claude_md_structure.py` | Validate CLAUDE.md post-reorganization content accuracy |
| `test_doc_counts.py` | Verify every numerical claim in every doc file matches source of truth |
| `test_doc_index.py` | Verify every doc is reachable from docs/README.md and every subdir has a README |
| `test_doc_links.py` | Verify every local markdown link resolves and no old flat-layout link survives |
| `test_filename_naming.py` | Encode the 7 naming rules from REQ-DOC-085 as predicates over docs/ filenames |
| `test_glossary_spelling.py` | Reject banned variants of glossary terms across every doc |
| `test_no_franchise_in_docs.py` | Guard against franchise references in docs |
| `test_orchestration_levels.py` | Orchestration levels doc validation |
| `test_sub_claude_md_completeness.py` | Structural tests for per-subfolder CLAUDE.md files under src/autoskillit/ |
| `test_tests_sub_claude_md_completeness.py` | Structural tests for per-subfolder CLAUDE.md files under tests/ |
