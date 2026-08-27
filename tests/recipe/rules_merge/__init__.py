"""Split-invariant and per-family focused tests for the rules_merge* modules.

These tests guard the #4857 decomposition: rules_merge.py is a thin facade
whose nine @semantic_rule rules now live in five sibling modules. The
split-invariant tests in this package assert ceiling compliance, facade
re-export identity, rule registration completeness, and test-filter cascade
coverage.
"""
