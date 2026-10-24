<!-- autoskillit-recipe-hash: sha256:ce746f1fb6e24780bbd5415063b5ed93da47d974acf92cac77e84fe7e86659dc -->
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
