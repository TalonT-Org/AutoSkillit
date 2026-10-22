<!-- autoskillit-recipe-hash: sha256:5148d0d7a73457977aec5d305aab3563b94a0a1716d01d76e02e46e5ecd519e3 -->
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
