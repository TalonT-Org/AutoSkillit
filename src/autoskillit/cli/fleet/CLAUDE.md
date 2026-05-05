# fleet/

Fleet campaign CLI subcommands for multi-issue dispatch orchestration.

## Files

| File | Purpose |
|------|---------|
| `__init__.py` | Main module: `fleet_app` Cyclopts sub-app with `campaign`, `dispatch`, `list`, `status` commands |
| `_fleet_display.py` | Status display: `_render_status_display()`, `_watch_loop()`, `_STATUS_COLUMNS`, `render_fleet_error()` |
| `_fleet_lifecycle.py` | Signal guard, stale dispatch reaping, `_pick_resume_campaign()` |
| `_fleet_preview.py` | Pre-launch dispatch preview: `_build_dispatch_recipe_table()`, `_print_dispatch_preview()` |
| `_fleet_session.py` | `_launch_fleet_session()` — builds Claude interactive session for fleet campaigns |
