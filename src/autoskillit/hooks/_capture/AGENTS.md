# hooks/_capture/

Small stdlib-only primitives shared by the shell-capture producer and cleanup
owners. Modules must remain importable when the hooks directory alone is on
`sys.path`.

## Files

| File | Purpose |
|------|---------|
| `__init__.py` | Package marker |
| `authority.py` | Project/root descriptor authority and lifecycle context factory |
| `types.py` | Lifecycle outcome, observation, and internal signal types |
