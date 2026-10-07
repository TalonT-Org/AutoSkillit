DeckShell.registerView("gaps", ctx => {
  const primitiveStates = ["measured", "measured_zero", "unknown", "unavailable",
    "not_applicable"];
  const viewForField = field => {
    const name = String(field ?? "");
    if (name.includes("session_outcome") || name.includes("tool_error") ||
      name.includes("attribution")) return "errors";
    if (name.includes("turn") || name.includes("context") || name.includes("window") ||
      name.includes("prompt") || name.includes("return")) return "context";
    return "trend";
  };
  const stateCell = row => primitiveStates.includes(row.state) ?
    ctx.availabilityCell(row.measure ?? {state: row.state}) :
    ctx.el("span", {class: "coverage-state coverage-state--" + row.state},
      row.state === "mixed" ? "mixed coverage" :
        row.state === "no_observations" ? "no observations" : row.state ?? "unknown");
  const stateCounts = row => Object.entries(row.state_counts ?? {}).map(([state, count]) =>
    state + " " + DeckCore.formatCount(count)).join(" · ") || "state counts unavailable";
  const countLabel = value => value == null ? "count unavailable" : DeckCore.formatCount(value);
  const inspectParams = row => Object.fromEntries([
    ["harness", row.harness], ["provider", row.provider], ["skill", row.skill],
    ["recipe", row.recipe], ["step", row.step], ["model", row.model],
    ["session", row.session_key]
  ].filter(([, value]) => value != null).map(([key, value]) => [key, [value]]));
  const tableRows = [...ctx.rows];
  const columns = [
    {key: "question", label: "Unanswered question", cell: row => row.question ??
      "What evidence is missing for " + row.field + "?"},
    {key: "prerequisite", label: "Prerequisite", cell: row => row.prerequisite ?? row.reason ?? "—"},
    {key: "field", label: "Affected field"},
    {key: "harness", label: "Harness", cell: row => row.harness ?? "unattributed"},
    {key: "provider", label: "Provider", cell: row => row.provider ?? "unattributed"},
    {key: "skill", label: "Skill", cell: row => row.skill ?? "unknown"},
    {key: "recipe", label: "Recipe", cell: row => row.recipe ?? "unknown"},
    {key: "step", label: "Step", cell: row => row.step ?? "unknown"},
    {key: "model", label: "Resolved model", cell: row => row.model ?? "unknown"},
    {key: "population", label: "Population"},
    {key: "state", label: "Coverage state", cell: stateCell},
    {key: "coverage_counts", label: "Observed / eligible", cell: row =>
      countLabel(row.observation_count) + " observed / " + countLabel(row.eligible_count) +
        " eligible · " + stateCounts(row)},
    {key: "source", label: "Evidence source", cell: row => row.source ?? "not recorded"},
    {key: "reason", label: "Reason", cell: row => row.reason ?? "—"},
    {key: "affected_view", label: "Inspect", cell: row => ctx.el("a", {
      href: ctx.href({view: viewForField(row.field), params: inspectParams(row)})
    }, "Open evidence")}
  ];

  return ctx.el("section", {class: "card view-gaps"}, [
    ctx.el("h1", {}, "Live evidence gaps"),
    ctx.el("p", {class: "view-lede"}, "Each entry states the unanswered question, its missing " +
      "prerequisite, the affected field and population, and the indexed source. Counts describe " +
      "observed records among eligible records; they do not turn absent evidence into zero."),
    ctx.el("p", {class: "view-note"}, "Tool coverage means indexed tool_result event presence " +
      "among eligible sessions. Observed events do not prove complete tool history, and absent " +
      "rows mean unobserved evidence."),
    tableRows.length ? ctx.sortableTable({columns, rows: tableRows,
      defaultSort: {key: "observation_count", dir: "desc"}}) :
      ctx.el("p", {class: "view-empty"}, "No uncovered fields match this selected cohort."),
    ctx.el("p", {class: "view-legend"}, "Measured, measured_zero, unknown, unavailable, and " +
      "not_applicable are primitive states. Mixed coverage and no observations are summaries " +
      "with their state counts shown; they are not measurements.")
  ]);
});
