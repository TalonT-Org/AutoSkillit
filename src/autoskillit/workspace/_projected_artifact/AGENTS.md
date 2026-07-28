# _projected_artifact/

Projected plugin artifact publication, validation, and launch-lease ownership.

## Files

| File | Purpose |
|------|---------|
| `__init__.py` | Pure facade for the projected-artifact lifecycle authority |
| `authority.py` | Cohesive publication, validation, lease handoff, and public constructors |

## Architecture Notes

Publication, exact-incarnation validation, and reader/writer lease handoff remain
co-located so destructive repair cannot bypass lifecycle lock ordering.
