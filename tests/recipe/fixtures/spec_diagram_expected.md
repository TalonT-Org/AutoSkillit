<!-- autoskillit-recipe-hash: sha256:ab978f1993115203770ec86dbecc24c6ce97f31eff7a462ef30e558fe27b06ce -->
<!-- autoskillit-diagram-format: v7 -->
## spec-diagram-oracle
Minimal recipe for spec-oracle testing. Covers all layout features.

### Graph
┌────┤ FOR EACH PLAN PART:
│    │
│    [plan] (retry ×3) ─── implement (retry ×∞) ─── verify (retry ×3)
│     │
│     ✗ failure → escalate
│                           │
│                           ✗ failure → fix
│                           ⌛ context limit → retry_worktree
│                                                    │
│                                                    ✗ failure → escalate
│
└────┘
│         └── next_or_done: ${{ result.next }} == more_parts  → plan ↑
│                           (default)  → done
│
─────────────────────────────────────
done  "Complete."
escalate  "Failed."

### Inputs
| Name | Description | Default |
|------|-------------|---------|
| plan_path | Pre-existing plan file path (skip make-plan if provided) | off |
| task | The implementation task | — |
