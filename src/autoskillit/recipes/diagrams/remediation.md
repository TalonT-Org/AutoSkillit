<!-- autoskillit-recipe-hash: sha256:ebf14a1001e82d735b435a31c97247e8313aa27d3a3d597cf4f539d070730eda -->
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
