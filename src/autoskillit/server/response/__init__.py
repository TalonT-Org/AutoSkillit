"""Response conformance, run_skill completion, and response-budget enforcement.

Owns post-conversion conformance checks for registered string tools
(``_response_conformance``), the admission and exact-delivery boundary for
``run_skill`` receipts (``_run_skill_completion``), and lossless,
shape-preserving response budget enforcement for MCP handler responses
(the ``_response_budget`` sub-package).
"""
