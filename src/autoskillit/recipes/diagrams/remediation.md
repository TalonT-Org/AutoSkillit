<!-- autoskillit-recipe-hash: sha256:c808e80ed7f9218342f8ada30eefba99c2021035ea9525daa77b6fee35a102e2 -->
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
