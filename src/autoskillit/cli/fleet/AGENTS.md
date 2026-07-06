# fleet/

Fleet campaign CLI subcommands for multi-issue dispatch orchestration.

## Files

| File | Purpose |
|------|---------|
| `__init__.py` | Main module: `fleet_app` Cyclopts sub-app with `campaign`, `dispatch`, `list`, `run`, `status` commands |
| `_fleet_display.py` | Status display: `_render_status_display()`, `_watch_loop()`, `_STATUS_COLUMNS`, `render_fleet_error()` |
| `_fleet_lifecycle.py` | Thin wrapper delegating reap to `fleet._dispatch_reaper`; `_pick_resume_campaign()` |
| `_fleet_preview.py` | Pre-launch dispatch preview: `_build_dispatch_recipe_table()`, `_print_dispatch_preview()` |
| `_fleet_run.py` | `fleet_run` command body + `_fleet_run_error` JSON envelope helper (extracted from `__init__.py`) |
| `_fleet_session.py` | `_launch_fleet_session()` — builds Claude interactive session for fleet campaigns |
