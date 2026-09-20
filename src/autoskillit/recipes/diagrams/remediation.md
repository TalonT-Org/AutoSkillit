<!-- autoskillit-recipe-hash: sha256:a6a12620438126e3e49eb427a363a814be2b476108433b030552d92fbb16a812 -->
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
|    merge
|    |
+----+
     |
     +-- [prepare-pr] (optional)
     |     +-- [arch-lens-{slug}] (optional, one per selected lens, parallel)
     |     compose-pr
