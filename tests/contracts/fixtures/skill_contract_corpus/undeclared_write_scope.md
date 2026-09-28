---
name: undeclared-write-scope
description: Fixture written before write_paths became a required declaration.
---
# undeclared-write-scope

This fixture predates the mandatory write-scope declaration. Migration inserts
`write_paths: inherit`, which preserves its pre-contract abstaining behavior.
