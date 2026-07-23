# docs/

Documentation integrity, link validity, and naming convention tests.

## Files

| File | Purpose |
|------|---------|
| `__init__.py` | empty |
| `test_banned_phrases.py` | Reject AI-tone banned phrases in every doc (derived from REQ-DOC-070) |
| `test_claude_md_structure.py` | Validate AGENTS.md post-reorganization: @-import structure, Claude-specific content accuracy |
| `test_doc_counts.py` | Verify every numerical claim in every doc file matches source of truth |
| `test_doc_index.py` | Verify every doc is reachable from docs/README.md and every subdir has a README |
| `test_doc_links.py` | Verify every local markdown link resolves and no old flat-layout link survives |
| `test_filename_naming.py` | Encode the 7 naming rules from REQ-DOC-085 as predicates over docs/ filenames |
| `test_glossary_spelling.py` | Reject banned variants of glossary terms across every doc |
| `test_no_franchise_in_docs.py` | Guard against franchise references in docs |
| `test_orchestration_levels.py` | Orchestration levels doc validation |
| `test_no_synthetic_citation_markers.py` | Guard against synthetic deep-research citation markers in tracked files |
| `test_rationale_document_completeness.py` | Validate experiment-type rationale document completeness |
| `test_sub_claude_md_completeness.py` | Structural tests for per-subfolder AGENTS.md files under src/autoskillit/ |
| `test_tests_sub_claude_md_completeness.py` | Structural tests for per-subfolder AGENTS.md files under tests/ |
| `test_agents_md_content.py` | Validate AGENTS.md content completeness and boundary correctness |
| `test_guard_fail_mode_docs.py` | Verify guard fail-mode matrix documentation accuracy |
| `test_output_budget_protocol_decision.py` | Ratchet ADR-0005 limits, accepted gaps, operational signals, corrections, and forward obligations |
| `test_recipe_redelivery_decision.py` | ADR-0004 recipe pull pagination identity and reconstruction contracts |
| `test_check_sub_claude_md_script.py` | Unit and integration tests for the check_sub_claude_md.py pre-commit hook script |
| `test_context_admission_decision.py` | Ratchet ADR-0007 context-admission authority, evidence, traceability, and downstream ownership |
