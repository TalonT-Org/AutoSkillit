# _projected_artifact/

Projected plugin artifact publication, validation, and launch-lease ownership.

## Architecture Notes

Publication, exact-incarnation validation, and reader/writer lease handoff remain
co-located so destructive repair cannot bypass lifecycle lock ordering.
