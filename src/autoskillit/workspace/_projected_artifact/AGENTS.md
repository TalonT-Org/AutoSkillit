# _projected_artifact/

Projected plugin artifact publication, validation, and launch-lease ownership.

## Architecture Notes

Publication, exact-incarnation validation, and reader/writer lease handoff remain
co-located so destructive repair cannot bypass lifecycle lock ordering.

`materialization.py` is the stable identity-preserving facade. The canonical owners
are `_documents.py` (projection contexts, `SkillContractRecord`, and
`project_agent_skill_document`), `_publication.py` (manifest schema, agent-skill tree
materialization, sanitized-plugin root staging), and `_validation.py`
(`validate_sanitized_plugin_artifact`). Shards import each other directly and must
never import the `materialization.py` facade; `authority.py` likewise reaches the
canonical owners rather than the facade.

`_validation.py` reconstructs the expected manifest independently of the producer in
`_publication.py`. Do not factor the two onto a shared builder — the duplication is
what lets the validator catch producer bugs.

Each shard is capped at 750 lines (`tests/arch/test_session_skill_materialization_size_ceilings.py`);
split further rather than growing past it.
