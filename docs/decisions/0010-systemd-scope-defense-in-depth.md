# ADR-0010: systemd Scope Wrapping Is Defense-in-Depth, Never the Ceiling of Record

**Status:** Accepted
**Date:** 2026-08-18
**Source:** Rectify — Orphaned Detached Child Processes (Spawner-Death Immunity)

## Context

`ProcessTetherConfig.systemd_scope_enabled` (default off) optionally wraps
funnel spawns with `systemd-run --user --scope --quiet -p
RuntimeMaxSec=<ceiling>`, giving the child a kernel-enforced ceiling that
survives spawner death. This is defense-in-depth on top of the tether sweep
(`sweep_orphaned_tethers`), not a replacement for it — three conditions make
the wrapping actually take effect, and one makes its own ceiling unreliable
even when it does:

- **WSL2** requires `[boot] systemd=true` in `/etc/wsl.conf` and WSL
  >= 0.67.6 (verify with `systemctl status`); WSL1 and systemd-less
  containers cannot use this path at all.
- **Headless contexts** require `loginctl enable-linger $USER` — without
  linger, the user manager is torn down at logout, taking every attached
  scope with it, including live sessions.
- **The probe** (`systemd-run` on `PATH` plus `systemctl --user
  is-system-running` in `{running, degraded}`) must pass, or the spawn
  proceeds unwrapped with a warning (`wrap_systemd_scope` in
  `execution/process/_process_tether.py`).
- **`RuntimeMaxSec` is not reliably enforced** on scope units (Launchpad
  [#2015126](https://bugs.launchpad.net/ubuntu/+source/systemd/+bug/2015126)),
  and its monotonic-clock timers do not advance while the host is suspended.
  The live attempt owner and the orphan tether use wall-clock deadlines, which
  continue to advance during suspension.

## Decision

`InteractiveLifetime` in `autoskillit.cli.session._session_process` owns the
live cook attempt's wall-clock hard cap. The tether sweep's `not_after` drives
orphan cleanup when that owner is gone; it is not the live attempt's ceiling
of record. `systemd_scope_enabled` adds a best-effort, fail-open kernel
backstop, and never substitutes for the live owner or the tether sweep. Its
absence (disabled, unsupported host, failed probe) must never be treated as a
correctness regression — only as one fewer layer of defense-in-depth.

## Consequences

- Code and docs referencing this field point here for the reliability
  caveats instead of repeating them inline (see
  `ProcessTetherConfig.systemd_scope_enabled` in
  `config/_dataclasses_fleet.py` and `wrap_systemd_scope`'s docstring in
  `execution/process/_process_tether.py`).
- A future change to WSL2/systemd-run detection, linger requirements, or the
  probe itself should update this ADR rather than re-deriving the rationale
  at each call site.
