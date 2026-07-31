# execution/

Subprocess integration, headless session, process lifecycle, and session result tests.

## Architecture Notes

`conftest.py` provides shared fixtures for the execution test suite. The headless tests are split across multiple files by concern (dispatch, synthesis, path validation, env injection, ordering) following the P1-F01 audit fix.

`_merge_queue_helpers.py` provides the `_make_watcher` and `_queue_state` factories.
Backend support keeps deterministic exact-identity launch binding in
`_plugin_binding.py` and intentionally pytest-free conformance assertions in
`_conformance_assertions.py`. Fresh Codex override coverage requires the installed-CLI
parse gate, and deterministic conformance fixtures retain the `--update-fixtures` review
gate.
