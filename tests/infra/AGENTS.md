# infra/

CI/CD configuration, security, guard coverage, and release sanity tests.

`tests/infra/conftest.py` owns the `FormatterCoverageDef` and
`_FORMATTER_COVERAGE_REGISTRY` meta-test mapping.

## Architecture Notes

`_pretty_output_helpers.py` and `_token_summary_helpers.py` provide shared helper factories used across the split pretty_output and token_summary test files respectively.
