# arch/

AST enforcement, sub-package layer contracts, and architectural invariant tests.

## Architecture Notes

`conftest.py` provides shared fixtures for AST-based tests. `_helpers.py` contains the shared AST visitor infrastructure used across multiple test files. `_rules.py` defines reusable arch rule tuples. `_deselection.py` provides diff-aware parametrized deselection helpers used by tests that use `pytest.mark.parametrize` over large rule sets. `_deferred_debt.py` provides the shared TrackedDeferral shape and the assert_not_stale/assert_entries_still_apply/assert_rationale_present checks for architectural allowlists that defer a fix behind a tracking issue rather than exempt it permanently.
