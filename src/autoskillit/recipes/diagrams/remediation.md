<!-- autoskillit-recipe-hash: sha256:516fc419f800266de988df863dedcfcefc9591898b51f7a5e5e90d96218a0b76 -->
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
