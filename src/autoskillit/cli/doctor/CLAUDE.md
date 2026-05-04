# doctor/

Diagnostic health checks for the autoskillit installation (28 checks).

## Files

| File | Purpose |
|------|---------|
| `__init__.py` | Orchestration facade: imports all check functions and assembles `run_doctor()` |
| `_doctor_types.py` | `DoctorResult` dataclass and `_NON_PROBLEM` severity set |
| `_doctor_config.py` | Project config, gitignore, and secret scanning checks |
| `_doctor_env.py` | Ambient env var leak detection (`SESSION_TYPE`, `CAMPAIGN_ID`) |
| `_doctor_features.py` | Feature flag dependency consistency and registry import checks |
| `_doctor_fleet.py` | Fleet infrastructure, campaign state, and sous-chef checks |
| `_doctor_hooks.py` | Hook registration, executability, and registry drift checks |
| `_doctor_install.py` | Install path, entry points, version drift, update dismissal checks |
| `_doctor_mcp.py` | MCP server registration, dual registration, plugin cache checks |
| `_doctor_runtime.py` | Quota cache schema version and claude process state checks |

## Architecture Notes

Hub-and-spoke: `__init__.py` is the single orchestration point. Each `_doctor_*` module is an independent check group returning `list[DoctorResult]`. Fleet checks are conditionally run only when the fleet feature is enabled.
