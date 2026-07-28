# _projected_artifact/

Projected plugin artifact publication, validation, and launch-lease ownership.

## Files

| File | Purpose |
|------|---------|
| `__init__.py` | Pure facade for the projected-artifact lifecycle authority |
| `authority.py` | Cohesive publication, validation, lease handoff, and public constructors |
| `materialization.py` | Shared projection construction and validation below the authority boundary |

## Architecture Notes

Publication, exact-incarnation validation, and reader/writer lease handoff remain
co-located so destructive repair cannot bypass lifecycle lock ordering.
