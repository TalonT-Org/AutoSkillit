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

  function ratioCell(ratio, percent = false) {
    const text = DeckCore.formatRatio(ratio, percent);
    const value = text == null ? ctx.availabilityCell(ratio ?? {state: "unavailable"}) :
      ctx.el("span", {class: "view-measure"}, text);
    return ctx.el("div", {class: "view-measure"}, [value,
      ctx.el("small", {class: "view-sample"}, DeckCore.ratioSample(ratio)),
      DeckCore.reviewSignal(ctx, ratio)]);
  }

  function toolMix(ratios = {}) {
    const entries = Object.entries(ratios.tool_mix ?? {});
    return entries.length ? ctx.el("div", {class: "view-pills"}, entries.map(([tool, ratio]) => {
      const value = DeckCore.formatRatio(ratio, true);
      return ctx.el("span", {}, [
        ctx.el("span", {}, tool),
        ctx.el("span", {}, " · "),
        value == null ? ctx.availabilityCell(ratio) : ctx.el("span", {}, value),
        ctx.el("small", {class: "view-sample"}, DeckCore.ratioSample(ratio)),
        DeckCore.reviewSignal(ctx, ratio)
      ]);
    })) :
      ctx.el("span", {class: "view-empty"}, "No observed tool calls");
  }

  const definitions = ctx.definitions ?? {};
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
  const definitionCards = [...contributorRoles].sort().map(role => {
    const definition = definitions[role];
    const available = definition?.state === "available";
    return ctx.el("article", {class: "view-definition"}, [
      ctx.el("h3", {}, ctx.entityLink(role, {view: "role", entity: role})),
      available ? ctx.el("p", {}, definition.description ?? "Role definition loaded.") :
        ctx.el("p", {class: "view-review__reason"}, "Definition unavailable for " + role + "."),
      available ? ctx.el("p", {}, "Declared tools: " +
        ((definition.tools ?? []).join(", ") || "none recorded")) : null,
      available ? ctx.el("pre", {}, definition.body ?? "") : null
    ]);
  });
  const skillTable = skillRows.length ? ctx.sortableTable({
    columns: [
      {key: "skill", label: "Skill", cell: row => ctx.entityLink(row.skill, {
        view: "skill", entity: row.skill
      })},
      {key: "harness", label: "Harness"},
      {key: "provider", label: "Provider"},
      {key: "input_output", label: "Input / output", cell: row =>
        ratioCell(row.ratios?.input_output)},
      {key: "cache_share", label: "Cache-read share", cell: row =>
        ratioCell(row.ratios?.cache_share, true)},
      {key: "tool_mix", label: "Observed tool mix", cell: row => toolMix(row.ratios)}
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
        ratioCell(row.ratios?.input_output)},
      {key: "cache_share", label: "Cache-read share", cell: row =>
        ratioCell(row.ratios?.cache_share, true)},
      {key: "tool_mix", label: "Observed tool mix", cell: row => toolMix(row.ratios)}
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
