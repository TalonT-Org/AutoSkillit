# fleet/

Fleet campaign CLI subcommands for multi-issue dispatch orchestration.

## Files

| File | Purpose |
|------|---------|
| `__init__.py` | Main module: `fleet_app` Cyclopts sub-app with `campaign`, `dispatch`, `list`, `status` commands |
| `_fleet_display.py` | Status display: `_render_status_display()`, `_watch_loop()`, `_STATUS_COLUMNS` |
| `_fleet_lifecycle.py` | Signal guard, stale dispatch reaping, `_pick_resume_campaign()` |
| `_fleet_session.py` | `_launch_fleet_session()` — builds Claude interactive session for fleet campaigns |
