# headless/

Headless Claude session orchestration — command prep, subprocess invocation, result construction.

## Architecture Notes

The `__init__.py` is a facade with public API (`run_headless_core`, `DefaultHeadlessExecutor`) and re-exports from all submodules. `_execute_claude_headless` in `_headless_execute.py` is the shared subprocess execution path for both `run_skill` (leaf) and `dispatch_food_truck` (fleet) flows. It uses a deferred import for `flush_session_log` to avoid circular imports. Managed attempt/executor support lives under `_managed/`.
