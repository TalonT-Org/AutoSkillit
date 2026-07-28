# hooks/_capture/

Small stdlib-only primitives shared by the shell-capture producer and cleanup
owners. Modules must remain importable when the hooks directory alone is on
`sys.path`.

## Files

| File | Purpose |
|------|---------|
| `_authority.py` | Project/root descriptor authority and lifecycle context factory |
| `_sweep.py` | Bounded cleanup sweep and verified recovery-operation orchestration |
| `_types.py` | Lifecycle outcome, observation, and internal signal types |
