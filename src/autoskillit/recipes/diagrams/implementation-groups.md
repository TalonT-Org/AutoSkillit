<!-- autoskillit-recipe-hash: sha256:77bbb0511a7ab5970cae8a3e107ac59e386b99a1909376619ba80ad8d51c628e -->
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
