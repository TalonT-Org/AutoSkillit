# recipe/

Recipe I/O, validation, semantic rules, schema, and bundled recipe tests.

## Architecture Notes

`conftest.py` provides shared fixtures for recipe tests. The `fixtures/` subdirectory contains YAML test data files including sample recipes and expected diagram output. The `test_rules_*.py` files each test a single semantic validation rule from `recipe/rules/` and its subdirectories (`campaign/`, `ci/`, `dataflow/`, `graph/`).
