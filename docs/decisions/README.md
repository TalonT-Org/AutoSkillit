# Architecture Decision Records

- [0001-prohibit-background-subagent-execution.md](0001-prohibit-background-subagent-execution.md) — prohibit `run_in_background: true` in all skills
- [0002-ban-inline-shell-scripts-from-cmd.md](0002-ban-inline-shell-scripts-from-cmd.md) — Prohibit inline shell scripts in recipe cmd fields; require externalization to .sh files or run_python callables
- [0003-skill-args.md](0003-skill-args.md) — Pass skill inputs as positional arguments, not environment variables
- [0004-recipe-redelivery.md](0004-recipe-redelivery.md) — Sanctioned `load_recipe` channel for recipe knowledge re-delivery after Codex context compaction
- [0005-output-budget-protocol.md](0005-output-budget-protocol.md) — Bound per-response model-context output with lossless artifacts, pre-spend guards, and derived transport ceilings
- [0006-output-containment.md](0006-output-containment.md) — Retire pre-execution command-shape classification in favor of per-backend output-boundary bounding on measured bytes
- [0007-context-admission.md](0007-context-admission.md) — Freeze the versioned cumulative context-admission boundary, authority contract, producer coverage, and privacy rules
- [0008-shell-capture-snapshot-authority.md](0008-shell-capture-snapshot-authority.md) — Make verified pipe-EOF snapshots, opaque V2 references, and checked delivery the sole Codex shell-capture authority
- [0009-verified-output-delivery-disposition.md](0009-verified-output-delivery-disposition.md) — A checksum-verified capture is delivered or the failure explicitly says why not; a bookkeeping failure never discards it
- [0010-systemd-scope-defense-in-depth.md](0010-systemd-scope-defense-in-depth.md) — systemd scope wrapping is a best-effort kernel backstop on top of the tether sweep, never the ceiling of record
