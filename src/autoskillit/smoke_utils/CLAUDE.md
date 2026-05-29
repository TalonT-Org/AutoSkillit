# smoke_utils/

Utility callables for smoke-test pipeline `run_python` steps (decomposed from monolithic `smoke_utils.py`).

## Files

| File | Purpose |
|------|---------|
| `__init__.py` | Re-export facade — 21 public names via `__all__` |
| `_helpers.py` | Shared JSON loading helpers (`_load_json`, `try_load_json`) |
| `_merge_gate_diagnosis.py` | Merge gate test failure diagnosis file writer |
| `_review.py` | PR diff annotation, review loop guards, diff context enrichment |
| `_eval.py` | Eval manifest parsing, context building, scorecard compilation |
| `_telemetry.py` | PR token summary patching, `_PR_SECTION_RE` |
| `_git.py` | Git/merge-queue helpers, domain partitions, zero-change detection |
