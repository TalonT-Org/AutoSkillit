# doctor/

Diagnostic health checks for the autoskillit installation (48 checks).

## Architecture Notes

Hub-and-spoke: `__init__.py` is the single orchestration point. Each `_doctor_*` module is an independent check group returning `list[DoctorResult]`. Fleet checks are conditionally run only when the fleet feature is enabled.
