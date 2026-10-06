DeckShell.registerView("spend", ctx => {
  const skillRows = ctx.skillMetrics ?? ctx.metrics ?? [];
  const roleRows = ctx.roleMetrics ?? [];
  const amount = measure => measure &&
    (measure.state === "measured" || measure.state === "measured_zero") ? measure.value : null;
  const skillChart = skillRows.map(row => ({
    label: row.skill + " · " + row.harness + " · " + row.provider,
    value: amount(row.measures?.input_tokens),
    href: ctx.href({view: "skill", entity: row.skill})
  }));
  const roleCells = DeckCore.roleHarnessRows(roleRows);
  const roleChart = roleCells.map(row => ({
    label: row.role + " · " + row.harness + " · " + row.provider,
    value: amount(row.measures?.input_tokens),
    href: ctx.href({view: "role", entity: row.role})
  }));
  const measureCell = measure => ctx.availabilityCell(measure ?? {state: "unavailable"});
  const skillTable = skillRows.length ? ctx.sortableTable({
    columns: [
      {key: "skill", label: "Skill", cell: row => ctx.entityLink(row.skill, {
        view: "skill", entity: row.skill
      })},
      {key: "harness", label: "Harness"},
      {key: "provider", label: "Provider"},
      {key: "input_tokens", label: "Input tokens", numeric: true,
        cell: row => measureCell(row.measures?.input_tokens)},
      {key: "output_tokens", label: "Output tokens", numeric: true,
        cell: row => measureCell(row.measures?.output_tokens)},
      {key: "cache_read_tokens", label: "Cache-read tokens", numeric: true,
        cell: row => measureCell(row.measures?.cache_read_tokens)},
      {key: "cache_write_tokens", label: "Cache-write tokens", numeric: true,
        cell: row => measureCell(row.measures?.cache_write_tokens)}
    ],
    rows: skillRows,
    defaultSort: {key: "skill", dir: "asc"}
  }) : ctx.el("p", {class: "view-empty"}, "No skill-run spend is available in this cohort.");
  const roleTable = roleCells.length ? ctx.sortableTable({
    columns: [
      {key: "role", label: "Role", cell: row => ctx.entityLink(row.role, {
        view: "role", entity: row.role
      })},
      {key: "harness", label: "Harness"},
      {key: "provider", label: "Provider"},
      {key: "input_tokens", label: "Input tokens", numeric: true,
        cell: row => measureCell(row.measures?.input_tokens)},
      {key: "output_tokens", label: "Output tokens", numeric: true,
        cell: row => measureCell(row.measures?.output_tokens)},
      {key: "cache_read_tokens", label: "Cache-read tokens", numeric: true,
        cell: row => measureCell(row.measures?.cache_read_tokens)},
      {key: "cache_write_tokens", label: "Cache-write tokens", numeric: true,
        cell: row => measureCell(row.measures?.cache_write_tokens)}
    ],
    rows: roleCells,
    defaultSort: {key: "role", dir: "asc"}
  }) : ctx.el("p", {class: "view-empty"}, "No verified child-role spend is available in this cohort.");

  return ctx.el("div", {class: "view-stack"}, [
    ctx.el("p", {class: "view-lede"},
      "Input, output, cache-read, and cache-write tokens remain separate inclusive measures. " +
      "Cache-read is already included in input. Child-role usage is shown as its own population."),
    ctx.el("div", {class: "view-grid"}, [
      ctx.el("section", {class: "card"}, [
        ctx.el("h2", {}, "Skill-run spend"),
        ctx.barChart(skillChart, {label: "Input tokens by skill run identity"}),
        skillTable
      ]),
      ctx.el("section", {class: "card"}, [
        ctx.el("h2", {}, "Child-role spend"),
        ctx.barChart(roleChart, {label: "Input tokens by child role identity"}),
        roleTable
      ])
    ])
  ]);
});
