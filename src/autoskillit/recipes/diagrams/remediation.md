<!-- autoskillit-recipe-hash: sha256:6addac09bab0d41b1b317dc21af3cb8441e8e5ba0865a337852259d3b5aff48a -->
<!-- autoskillit-diagram-format: v7 -->
## remediation

### Flow

+-- [investigate] (optional)
|
rectify --- bind_plan_set <-> [bounded coverage replan -> make-plan]
|
[review-approach] (optional)
|
+----+ FOR EACH PLAN PART:
|    |
|    dry-walkthrough --- renew_plan_set --- implement --- test <-> [x fail -> fix]
|             |
|             +-- [context limit -> renew_before_retry -> retry_walkthrough]
|    |
|    +-- [audit] (optional)
|    |     x fail [-> make-plan]
|    |
|    merge <-> [x test gate -> fix -> test]
|    |
+----+
     |
     +-- [prepare-pr] (optional)
     |     +-- [arch-lens-{slug}] (optional, one per selected lens, parallel)
     |     compose-pr
