# doctor/

Diagnostic health checks for the autoskillit installation (52 checks).

## Architecture Notes

Hub-and-spoke: `__init__.py` is the single orchestration point. Each `_doctor_*` module is an independent check group returning `list[DoctorResult]`. Fleet checks are conditionally run only when the fleet feature is enabled.

`run_doctor()` is the read-only diagnostic hub. Opt-in filesystem repair is a
distinct spoke, `run_doctor_repairs()`, selected only by the CLI's `--repair`
flag; both paths use the shared result collector and formatter exactly once.
