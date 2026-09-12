# infra/

CI/CD configuration, security, guard coverage, and release sanity tests.

`tests/infra/conftest.py` owns the `FormatterCoverageDef` and
`_FORMATTER_COVERAGE_REGISTRY` meta-test mapping.

## Architecture Notes

`_pretty_output_helpers.py` and `_token_summary_helpers.py` provide shared helper factories used across the split pretty_output and token_summary test files respectively. `_complexity_helpers.py` provides the same for scripts/check_complexity.py's test files (tests/infra/test_check_complexity.py, test_check_complexity_git_e2e.py, test_check_complexity_ruff_parity.py, and tests/arch/test_complexity_limits.py).
