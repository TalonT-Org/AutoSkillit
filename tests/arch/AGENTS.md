# arch/

AST enforcement, sub-package layer contracts, and architectural invariant tests.

## Architecture Notes

`conftest.py` provides shared fixtures for AST-based tests. `_helpers.py` contains the shared AST visitor infrastructure used across multiple test files. `_rules.py` defines reusable arch rule tuples. `_deselection.py` provides diff-aware parametrized deselection helpers used by tests that use `pytest.mark.parametrize` over large rule sets. `_deferred_debt.py` provides the shared `TrackedDeferral` shape and its checks for architectural allowlists that defer a fix behind a tracking issue rather than exempt it permanently. Every deferral must name a live `regression_test`; renaming or deleting that test, including changing a parametrized case ID, requires updating every matching deferral in the same commit.

The enumeration rule derives vanished errors from `VANISHED_ERRORS`: `_handler_matches_vanished_error` selects the first handler Python would use, `_handler_recovers` requires that handler to complete normally, and `_try_recovers_vanish` applies both checks to every runtime member. `_handler_covers_oserror` remains for rules that only need broad OSError coverage. Its taint analysis supports one same-module return-value hop, including wrapper summaries; it intentionally does not propagate taint through function arguments, so callers must still recover at direct reads. This limit is deliberate and must remain documented when the guard changes.
