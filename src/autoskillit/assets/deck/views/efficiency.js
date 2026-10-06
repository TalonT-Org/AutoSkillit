DeckShell.registerView("efficiency", ctx => {
  const skillRows = ctx.skillMetrics ?? [];
  const roleRows = (ctx.roleMetrics ?? []).flatMap(row => (row.harnesses ?? []).map(cell => ({
    role: row.role,
    provider: row.provider,
    harness: cell.harness,
    models: cell.models ?? [],
    measures: cell.measures,
    ratios: cell.ratios
  })));

  function ratioSignals(ratios = {}) {
    const values = [];
    if (ratios.input_output) values.push({label: "Input exposure per output", ratio: ratios.input_output});
    if (ratios.cache_share) values.push({label: "Cache-read share", ratio: ratios.cache_share,
      percent: true});
    for (const [tool, ratio] of Object.entries(ratios.tool_mix ?? {})) {
      values.push({label: "Tool mix · " + tool, ratio, percent: true});
    }
    return values;
  }

  const contributorRoles = new Set((ctx.relationships ?? []).map(edge => edge.role));
  for (const row of skillRows) {
    for (const signal of ratioSignals(row.ratios)) {
      for (const role of signal.ratio.definition_roles ?? []) contributorRoles.add(role);
    }
  }
  for (const row of roleRows) {
    for (const signal of ratioSignals(row.ratios)) {
      for (const role of signal.ratio.definition_roles ?? []) contributorRoles.add(role);
    }
  }
  const definitionCards = [...contributorRoles].sort().map(role =>
    DeckCore.definitionCard(ctx, role));
  const skillTable = skillRows.length ? ctx.sortableTable({
    columns: [
      {key: "skill", label: "Skill", cell: row => ctx.entityLink(row.skill, {
        view: "skill", entity: row.skill
      })},
      {key: "harness", label: "Harness"},
      {key: "provider", label: "Provider"},
      {key: "input_output", label: "Input / output", cell: row =>
        DeckCore.ratioCell(ctx, row.ratios?.input_output)},
      {key: "cache_share", label: "Cache-read share", cell: row =>
        DeckCore.ratioCell(ctx, row.ratios?.cache_share, true)},
      {key: "tool_mix", label: "Observed tool mix", cell: row => DeckCore.toolMix(ctx, row.ratios)}
    ],
    rows: skillRows,
    defaultSort: {key: "skill", dir: "asc"}
  }) : ctx.el("p", {class: "view-empty"}, "No skill-run ratios are available in this cohort.");
  const roleTable = roleRows.length ? ctx.sortableTable({
    columns: [
      {key: "role", label: "Role", cell: row => ctx.entityLink(row.role, {
        view: "role", entity: row.role
      })},
      {key: "harness", label: "Harness"},
      {key: "provider", label: "Provider"},
      {key: "input_output", label: "Input / output", cell: row =>
        DeckCore.ratioCell(ctx, row.ratios?.input_output)},
      {key: "cache_share", label: "Cache-read share", cell: row =>
        DeckCore.ratioCell(ctx, row.ratios?.cache_share, true)},
      {key: "tool_mix", label: "Observed tool mix", cell: row => DeckCore.toolMix(ctx, row.ratios)}
    ],
    rows: roleRows,
    defaultSort: {key: "role", dir: "asc"}
  }) : ctx.el("p", {class: "view-empty"}, "No child-invocation ratios are available in this cohort.");

  return ctx.el("div", {class: "view-stack"}, [
    ctx.el("p", {class: "view-lede"},
      "Prepared ratios compare eligible input, output, cache-read, and observed tool totals. " +
      "They are evidence signals, not quality diagnoses or automatic model recommendations."),
    ctx.el("section", {class: "card"}, [
      ctx.el("h2", {}, "Contributor definitions"),
      ...(definitionCards.length ? definitionCards : [
        ctx.el("p", {class: "view-empty"}, "No contributor definitions are linked in this cohort.")
      ])
    ]),
    ctx.el("section", {class: "card"}, [ctx.el("h2", {}, "Skill-run efficiency"), skillTable]),
    ctx.el("section", {class: "card"}, [ctx.el("h2", {}, "Child-invocation efficiency"), roleTable])
  ]);
});
