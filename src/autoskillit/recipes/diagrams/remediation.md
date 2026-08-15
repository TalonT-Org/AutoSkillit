<!-- autoskillit-recipe-hash: sha256:8ce55ee9e885c41f230aa1a2d5f7b0271972e4c32ee8dc06b8e2bb848feef93d -->
<!-- autoskillit-diagram-format: v7 -->
## remediation

### Flow

+-- [investigate] (optional)
|
rectify --- [review-approach] (optional)
|
+----+ FOR EACH PLAN PART:
|    |
|    dry-walkthrough --- implement --- test <-> [x fail -> fix]
|    |
|    +-- [audit] (optional)
|    |     x fail [-> make-plan]
|    |
|    merge
|    |
+----+
     |
     +-- [prepare-pr] (optional)
     |     +-- [arch-lens-{slug}] (optional, one per selected lens, parallel)
     |     compose-pr
