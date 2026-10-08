DeckShell.registerView("parity", ctx => {
  const groupFields = ["session_key", "harness", "provider", "skill", "recipe", "step",
    "model", "level", "population"];
  const groupKey = row => JSON.stringify(groupFields.map(field => row[field] ?? null));
  const stateCell = row => {
    const state = row?.state ?? "no_observations";
    const body = DeckCore.isPrimitiveState(state) ? ctx.availabilityCell(row.measure ?? {state}) :
      ctx.el("span", {class: "coverage-state coverage-state--" + state},
        state === "mixed" ? "mixed coverage" : state === "no_observations" ?
          "no observations" : state);
    const counts = row ? Object.entries(row.state_counts ?? {}).map(([name, count]) =>
      name + " " + DeckCore.formatCount(count)).join(" · ") : "no row in this selection";
    const coverage = ctx.el("small", {class: "parity-cell__coverage"}, row ?
      DeckCore.countLabel(row.observation_count) + " observations · " +
      DeckCore.countLabel(row.eligible_count) + " eligible source records · " + counts : counts);
    const cell = ctx.el("span", {class: "parity-cell"}, [body, coverage]);
    if (!row || !["measured", "measured_zero", "not_applicable"].includes(state)) {
      return ctx.el("a", {class: "parity-cell-link",
        href: ctx.href({view: "gaps", params: DeckCore.inspectParams(row)})}, cell);
    }
    return cell;
  };
  const groups = new Map();
  const fields = [];
  ctx.rows.forEach(row => {
    const key = groupKey(row);
    if (!groups.has(key)) {
      groups.set(key, {...Object.fromEntries(groupFields.map(field => [field, row[field] ?? null])),
        fields: {}});
    }
    groups.get(key).fields[row.field] = row;
    if (!fields.includes(row.field)) fields.push(row.field);
  });
  const matrixRows = [...groups.values()];
  const columns = [
    {key: "harness", label: "Harness", cell: row => row.harness ?? "unattributed"},
    {key: "provider", label: "Provider", cell: row => row.provider ?? "unattributed"},
    {key: "skill", label: "Skill", cell: row => row.skill ?? "unknown"},
    {key: "recipe", label: "Recipe", cell: row => row.recipe ?? "unknown"},
    {key: "step", label: "Step", cell: row => row.step ?? "unknown"},
    {key: "model", label: "Resolved model", cell: row => row.model ?? "unknown"},
    {key: "level", label: "Level", cell: row => row.level ?? "unrecorded"},
    {key: "population", label: "Population"},
    {key: "session_key", label: "Session", cell: row => row.session_key ?? "all eligible"},
    ...fields.map(field => ({key: field, label: field.replace(/_/g, " "),
      cell: row => stateCell(row.fields[field])}))
  ];

  return ctx.el("section", {class: "card view-parity"}, [
    ctx.el("h1", {}, "Harness and provider coverage parity"),
    ctx.el("p", {class: "view-lede"}, "Each row shows the same target fields for one selected " +
      "harness/provider population. Observation and eligibility counts are separate: " +
      "eligibility counts owner sessions for attributed evidence and events for " +
      "unattributed tools. Text accompanies every state; color is supplementary."),
    ctx.el("p", {class: "view-legend"}, "Measured and measured_zero are observed states. " +
      "Unknown, unavailable, and not_applicable remain distinct. Mixed coverage and no " +
      "observations are summaries of the primitive states shown in each cell."),
    matrixRows.length ? ctx.sortableTable({columns, rows: matrixRows,
      defaultSort: {key: "harness", dir: "asc"}}) :
      ctx.el("p", {class: "view-empty"}, "No parity rows match this selected cohort."),
    ctx.el("p", {class: "view-note"}, "Links on partial or missing cells open the gap register " +
      "with this cohort and exact session selection retained.")
  ]);
});
