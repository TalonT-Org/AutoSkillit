# server/

Server tool handler unit tests — kitchen, execution, CI, clone, workspace tools.

`_pipeline_test_helpers.py` provides shared pipeline-tracker helpers for server and
integration tests. `_type_coercion_fixtures.py` provides shared type-coercion fixtures
for annotation-aware imports.

## Architecture Notes

`conftest.py` provides shared fixtures including `tool_ctx` (full-stack L3 context) used across server tests. `_helpers.py` provides shared test builder utilities. The `test_tools_execution_*.py` files test run_skill in focused slices (command, input gates, response, results, routing).
