<!-- autoskillit-recipe-hash: sha256:3ae2bffcd98062634cdd24de72281a5bc1335c143d073d13a929b3a32b026ce7 -->
<!-- autoskillit-diagram-format: v7 -->
## implementation-groups

### Flow

group
|
+----+ FOR EACH GROUP:
|    |
|    plan --- [review-approach] (optional)
|    |
|    +----+ FOR EACH PLAN PART:
|    |    |
|    |    verify --- implement --- test <-> [x fail -> fix]
|    |    |
|    |    merge
|    |    |
|    +----+
|    |
+----+
     |
     +-- [audit] (optional)
     |     x fail [-> plan]
     |
     +-- [prepare-pr] (optional)
     |     +-- [arch-lens-{slug}] (optional, one per selected lens, parallel)
     |     compose-pr
