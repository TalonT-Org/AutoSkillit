# rules/ci/

CI semantic rule modules (4 rule files).

The package initializer is docstring-only; importing rule modules registers them through
the `@semantic_rule` decorator.

## Architecture Notes

No cross-imports between rule modules. Each rule receives a `ValidationContext` argument.
