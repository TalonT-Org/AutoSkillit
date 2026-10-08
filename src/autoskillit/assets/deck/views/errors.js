DeckShell.registerView("errors", ctx => {
  const stateCell = measure => DeckCore.coverageStateCell(ctx, measure, "coverage-state");
  const countCoverage = row => DeckCore.formatStateCounts(row.failures?.state_counts) ||
    "outcome coverage unavailable";
  const rateText = rate => {
    const value = rate?.state === "measured" ? rate.value :
      rate?.state === "measured_zero" ? 0 : null;
    const sample = rate?.sample_size;
    return value == null ? stateCell(rate) : ctx.el("span", {class: "view-measure"}, [
      ctx.el("span", {}, String(Math.round(value * 10000) / 100) + "%"),
      ctx.el("small", {class: "view-sample"}, sample == null ? "sample unavailable" :
        DeckCore.formatCount(sample) + " known outcomes")
    ]);
  };
  const inspectLink = row => {
    if (row.skill != null) return ctx.el("a", {href: ctx.href({view: "skill",
      entity: row.skill})}, row.skill);
    if (row.session_key != null) return ctx.el("a", {href: ctx.href({view: "context",
      params: {session: [row.session_key]}})}, "session evidence");
    return ctx.el("a", {href: ctx.href({view: "cohort"})}, "unattributed cohort");
  };
  const columns = [
    {key: "symptom", label: "Symptom", cell: row => row.symptom ?? "unknown symptom"},
    {key: "population", label: "Source population", cell: row => ({session_failure:
      "failed session", tool_failure: "failed tool_result event", unattributed_tool:
      "unattributed tool event", session_outcome: "unknown session outcome"})[row.population] ??
        row.population},
    {key: "skill", label: "Skill", cell: row => row.skill ?? "unknown skill"},
    {key: "recipe", label: "Recipe", cell: row => row.recipe ?? "unknown recipe"},
    {key: "step", label: "Step", cell: row => row.step ?? "unknown step"},
    {key: "harness", label: "Harness", cell: row => row.harness ?? "unattributed"},
    {key: "provider", label: "Provider", cell: row => row.provider ?? "unattributed"},
    {key: "failures", label: "Observed failures", cell: row => stateCell(row.failures)},
    {key: "failure_rate", label: "Known-outcome failure share", cell: row => rateText(row.failure_rate)},
    {key: "failure_coverage", label: "Outcome coverage", cell: row =>
      DeckCore.formatCount(row.observed_count ?? 0) + " observed / " +
      DeckCore.formatCount(row.eligible_count ?? 0) + " eligible · " +
      DeckCore.formatCount(row.unknown_count ?? 0) + " unknown · " + countCoverage(row)},
    {key: "tool_result_event_count", label: "Indexed tool_result events", numeric: true,
      cell: row => row.tool_result_event_count == null ? "unobserved" :
        DeckCore.formatCount(row.tool_result_event_count)},
    {key: "tool_event_coverage", label: "Tool event coverage", cell: row =>
      rateText(row.tool_event_coverage)},
    {key: "attribution_state", label: "Attribution", cell: row => row.attribution_state ??
      (row.population === "unattributed_tool" ? "unattributed" : "owner indexed")},
    {key: "inspect", label: "Inspect", cell: inspectLink}
  ];
  const tableRows = [...ctx.rows];
  const populationRows = ctx.metrics?.populations ?? [];
  const outcomeCoverage = summary => DeckCore.countLabel(summary?.observed_count) +
    " observed / " + DeckCore.countLabel(summary?.eligible_count) + " eligible · " +
    DeckCore.countLabel(summary?.unknown_count) + " unknown · " +
    DeckCore.formatStateCounts(summary?.state_counts);
  const coverageColumns = [
    {key: "skill", label: "Skill", cell: row => row.skill ?? "unknown skill"},
    {key: "recipe", label: "Recipe", cell: row => row.recipe ?? "unknown recipe"},
    {key: "step", label: "Step", cell: row => row.step ?? "unknown step"},
    {key: "harness", label: "Harness", cell: row => row.harness ?? "unattributed"},
    {key: "provider", label: "Provider", cell: row => row.provider ?? "unattributed"},
    {key: "eligible_sessions", label: "Eligible sessions", numeric: true},
    {key: "session_failures", label: "Session failures", cell: row =>
      stateCell(row.session_outcomes?.failures)},
    {key: "session_failure_share", label: "Known-outcome session failure share", cell: row =>
      rateText(row.session_outcomes?.failure_rate)},
    {key: "session_outcome_coverage", label: "Session outcome coverage", cell: row =>
      outcomeCoverage(row.session_outcomes)},
    {key: "tool_result_event_count", label: "Indexed tool_result events", numeric: true,
      cell: row => row.tool_result_event_count == null ? "unobserved" :
        DeckCore.formatCount(row.tool_result_event_count)},
    {key: "tool_event_coverage", label: "Indexed event presence", cell: row =>
      rateText(row.tool_event_coverage)},
    {key: "tool_failures", label: "Observed tool failures", cell: row =>
      stateCell(row.tool_outcomes?.failures)},
    {key: "tool_failure_share", label: "Known-outcome tool failure share", cell: row =>
      rateText(row.tool_outcomes?.failure_rate)},
    {key: "tool_outcome_coverage", label: "Tool outcome coverage", cell: row =>
      outcomeCoverage(row.tool_outcomes)},
    {key: "attribution_state", label: "Attribution", cell: row => row.attribution_state}
  ];

  return ctx.el("section", {class: "card view-errors"}, [
    ctx.el("h1", {}, "Failure symptoms by skill and step"),
    ctx.el("p", {class: "view-lede"}, "Session failures and explicit failed tool_result events " +
      "remain separate populations, even when they belong to one attempt. Unknown outcomes stay " +
      "in the eligible coverage counts and are not counted as failures."),
    ctx.el("p", {class: "view-note"}, "A tool failure is counted only when indexed success is " +
      "explicitly false. Tool event coverage means indexed tool_result event presence among " +
      "eligible sessions; the observed events do not establish a complete tool history."),
    tableRows.length ? ctx.sortableTable({columns, rows: tableRows,
      defaultSort: {key: "symptom", dir: "asc"}}) :
      ctx.el("p", {class: "view-empty"}, "No observed failure symptoms match this cohort."),
    populationRows.length ? ctx.el("section", {class: "view-error-coverage"}, [
      ctx.el("h2", {}, "Outcome and tool-event coverage"),
      ctx.el("p", {class: "view-note"}, "Outcome state counts use all eligible sessions. Tool " +
        "event presence describes only indexed tool_result rows and does not prove complete " +
        "tool history. Unattributed events keep their own population row."),
      ctx.sortableTable({columns: coverageColumns, rows: populationRows,
        defaultSort: {key: "eligible_sessions", dir: "desc"}})
    ]) : null,
    ctx.el("p", {class: "view-legend"}, "Unknown symptom, skill, step, or event attribution " +
      "is retained as an explicit row instead of being assigned to a nearby attempt. " +
      "A measured_zero failure share is an observed 0%; unknown, unavailable, and " +
      "not_applicable remain separate states.")
  ]);
});
