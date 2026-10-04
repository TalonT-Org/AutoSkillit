DeckShell.registerView("cohort", ctx => {
  const fragment = document.createDocumentFragment();
  const pairs = DeckCore.summarizePairs(ctx.rows);
  const chartItems = pairs.map(pair => ({
    label: pair.harness + " · " + pair.provider,
    value: pair.runs,
    href: ctx.href({view: "cohort", params: {
      harness: [pair.harness], provider: [pair.provider]
    }})
  }));
  const chartCard = ctx.el("section", {class: "card"}, [
    ctx.el("h2", {}, "Runs by harness · provider"),
    ctx.barChart(chartItems, {label: "Runs by harness and provider"}),
    ctx.sortableTable({
      columns: [
        {key: "harness", label: "Harness", cell: pair => ctx.entityLink(pair.harness, {
          view: "cohort", params: {harness: [pair.harness]}
        })},
        {key: "provider", label: "Provider", cell: pair => ctx.entityLink(pair.provider, {
          view: "cohort", params: {provider: [pair.provider]}
        })},
        {key: "runs", label: "Runs", numeric: true},
        {key: "skills", label: "Skills", numeric: true},
        {key: "first_ms", label: "First run", cell: pair => pair.first_ms === null ?
          "—" : DeckCore.formatDate(pair.first_ms)},
        {key: "last_ms", label: "Last run", cell: pair => pair.last_ms === null ?
          "—" : DeckCore.formatDate(pair.last_ms)}
      ],
      rows: pairs,
      defaultSort: {key: "runs", dir: "desc"}
    })
  ]);
  fragment.appendChild(chartCard);

  const availabilityRows = [];
  for (const entry of ctx.model.availability) {
    const value = entry.state === "measured" ? 1234 :
      entry.state === "measured_zero" ? 0 : null;
    availabilityRows.push(ctx.el("dt", {},
      ctx.availabilityCell({state: entry.state, value})));
    availabilityRows.push(ctx.el("dd", {}, entry.description));
  }

  const samples = [
    {
      chip: {key: "x", label: "live", state: "live"}, selected: true,
      meaning: "This value can be selected on the current view."
    },
    {
      chip: {key: "x", label: "struck", state: "struck", reason: "not selectable here",
        issue: 4622}, selected: false,
      meaning: "This value is defined but cannot be selected on the current view."
    },
    {
      chip: {key: "x", label: "absent", state: "absent", reason: "not recorded",
        issue: 4622}, selected: false,
      meaning: "This value is not recorded in the index, so it is not zero."
    }
  ];
  const helpCard = ctx.el("section", {class: "card"}, [
    ctx.el("h2", {}, "How to read this deck"),
    ctx.el("dl", {}, availabilityRows),
    ...samples.map(sample => {
      const presentation = DeckCore.chipPresentation(sample.chip, sample.selected);
      return ctx.el("p", {}, [
        ctx.el("span", {class: presentation.className, "aria-hidden": "true"},
          presentation.text),
        ctx.el("span", {}, " " + sample.meaning)
      ]);
    })
  ]);
  fragment.appendChild(helpCard);

  if (ctx.rows.length === 0) {
    const emptyChildren = [ctx.el("p", {}, "No runs match this cohort.")];
    if (ctx.model.tables.sessions.length === 0) {
      emptyChildren.push(ctx.el("p", {}, [
        ctx.el("span", {}, "Run "),
        ctx.el("code", {}, "autoskillit sessions index --update"),
        ctx.el("span", {}, " to populate the report index.")
      ]));
    }
    fragment.appendChild(ctx.el("section", {class: "card"}, emptyChildren));
  }

  return fragment;
});
