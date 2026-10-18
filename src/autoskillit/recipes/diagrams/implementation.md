<!-- autoskillit-recipe-hash: sha256:6f4126255fa60dbfe82b0a047b5467378249668da3c0986bb3071fe82df27dfc -->
<!-- autoskillit-diagram-format: v7 -->
## implementation

### Flow

plan --- bind_plan_set <-> [bounded coverage replan -> plan]
|
[review-approach] (optional)
|
+----+ FOR EACH PLAN PART:
|    |
|    verify --- renew_plan_set --- implement --- test <-> [x fail -> fix]
|    |
|    merge
|    |
+----+
     |
     +-- [audit] (optional)
     |     x fail [-> plan]
     |
     +-- [prepare-pr] (optional)
     |     +-- [arch-lens-{slug}] (optional, one per selected lens, parallel)
     |     compose-pr
