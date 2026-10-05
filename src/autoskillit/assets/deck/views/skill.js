DeckShell.registerView("skill", ctx => {
  const rows = ctx.metrics ?? [];
  const routeSkill = ctx.route.entity;
  const relationships = ctx.relationships ?? [];
  const selected = routeSkill == null ? [] : rows.filter(row => row.skill === routeSkill);
  const skillNames = [...new Set(selected.map(row => row.skill))];
  if (routeSkill != null && !skillNames.includes(routeSkill)) skillNames.unshift(routeSkill);

  function measureCell(measure) {
    return ctx.availabilityCell(measure ?? {state: "unavailable"});
  }

  function ratioCell(ratio, percent = false) {
    const value = DeckCore.formatRatio(ratio, percent);
    return ctx.el("div", {class: "view-measure"}, [
      value == null ? measureCell(ratio) : ctx.el("span", {}, value),
      ctx.el("small", {class: "view-sample"}, DeckCore.ratioSample(ratio))
    ]);
  }

  function definitionCard(role) {
    const definition = ctx.definitions?.[role];
    return ctx.el("article", {class: "view-definition"}, [
      ctx.el("h3", {}, ctx.entityLink(role, {view: "role", entity: role})),
      definition?.state === "available" ?
        ctx.el("p", {}, definition.description ?? "Role definition loaded.") :
        ctx.el("p", {class: "view-review__reason"}, "Definition unavailable for " + role + "."),
      definition?.state === "available" ? ctx.el("p", {}, "Declared tools: " +
        ((definition.tools ?? []).join(", ") || "none recorded")) : null,
      definition?.state === "available" ? ctx.el("pre", {}, definition.body ?? "") : null
    ]);
  }

  const roleNames = new Set(relationships.map(edge => edge.role));
  for (const row of selected) {
    for (const child of row.child_roles ?? []) roleNames.add(child.role);
    for (const role of row.ratios?.input_output?.definition_roles ?? []) roleNames.add(role);
    for (const role of row.ratios?.cache_share?.definition_roles ?? []) roleNames.add(role);
  }
  const definitions = [...roleNames].sort().map(definitionCard);

  const skillTable = rows.length ? ctx.sortableTable({
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
        cell: row => measureCell(row.measures?.cache_write_tokens)},
      {key: "input_output", label: "Input / output", cell: row =>
        ratioCell(row.ratios?.input_output)},
      {key: "cache_share", label: "Cache-read share", cell: row =>
        ratioCell(row.ratios?.cache_share, true)},
      {key: "exact_retransmission", label: "Exact prompt retransmission", cell: row =>
        measureCell(row.exact_retransmission)}
    ],
    rows,
    defaultSort: {key: "skill", dir: "asc"}
  }) : ctx.el("p", {class: "view-empty"}, "No skill-run metrics match these facets.");

  if (routeSkill == null) {
    return ctx.el("section", {class: "card"}, [
      ctx.el("h2", {}, "Skills in this cohort"),
      ctx.el("p", {class: "view-lede"},
        "Select a skill to inspect its recipe-step and child-role attribution."),
      skillTable
    ]);
  }

  const steps = selected.flatMap(row => (row.recipe_steps ?? []).map(step => ({
    skill: row.skill,
    recipe: step.recipe,
    step: step.step,
    measures: step.measures,
    ratios: step.ratios,
    exact_retransmission: step.exact_retransmission
  })));
  const stepTable = steps.length ? ctx.sortableTable({
    columns: [
      {key: "recipe", label: "Recipe"},
      {key: "step", label: "Step"},
      {key: "input_tokens", label: "Input tokens", cell: row =>
        measureCell(row.measures?.input_tokens)},
      {key: "output_tokens", label: "Output tokens", cell: row =>
        measureCell(row.measures?.output_tokens)},
      {key: "cache_read_tokens", label: "Cache-read tokens", cell: row =>
        measureCell(row.measures?.cache_read_tokens)},
      {key: "exact_retransmission", label: "Exact prompt retransmission", cell: row =>
        measureCell(row.exact_retransmission)}
    ],
    rows: steps,
    defaultSort: {key: "recipe", dir: "asc"}
  }) : ctx.el("p", {class: "view-empty"}, "No recipe-step attribution is available for this skill.");

  const childRows = selected.flatMap(row => (row.child_roles ?? []).map(child => ({
    skill: row.skill,
    ...child
  })));
  const childTable = childRows.length ? ctx.sortableTable({
    columns: [
      {key: "role", label: "Child role", cell: row => ctx.entityLink(row.role, {
        view: "role", entity: row.role
      })},
      {key: "harness", label: "Harness"},
      {key: "provider", label: "Provider"},
      {key: "input_tokens", label: "Child input tokens", cell: row =>
        measureCell(row.measures?.input_tokens)},
      {key: "output_tokens", label: "Child output tokens", cell: row =>
        measureCell(row.measures?.output_tokens)},
      {key: "input_output", label: "Input / output", cell: row =>
        ratioCell(row.ratios?.input_output)},
      {key: "cache_share", label: "Cache-read share", cell: row =>
        ratioCell(row.ratios?.cache_share, true)}
    ],
    rows: childRows,
    defaultSort: {key: "role", dir: "asc"}
  }) : ctx.el("p", {class: "view-empty"}, "No verified child-role attribution is linked to this skill.");

  const heading = routeSkill ?? "Skills in this cohort";
  const related = relationships.length ? ctx.sortableTable({
    columns: [
      {key: "role", label: "Spawned role", cell: edge => ctx.entityLink(edge.role, {
        view: "role", entity: edge.role
      })},
      {key: "harness", label: "Harness"},
      {key: "provider", label: "Child provider"}
    ],
    rows: relationships,
    defaultSort: {key: "role", dir: "asc"}
  }) : ctx.el("p", {class: "view-empty"}, "No verified child relationships are recorded.");

  return ctx.el("div", {class: "view-stack"}, [
    ctx.el("section", {class: "card"}, [
      ctx.el("h2", {}, heading),
      routeSkill == null ? ctx.el("p", {class: "view-lede"},
        "Select a skill to inspect its recipe-step and child-role attribution.") :
        ctx.el("p", {class: "view-lede"},
          "Input exposure and cache-read share are retransmission proxies. Exact repeated-prompt tokens are not reported when absent; cache-read is already included in input."),
      skillTable
    ]),
    ctx.el("section", {class: "card"}, [
      ctx.el("h2", {}, "Contributor definitions"),
      ...(definitions.length ? definitions : [
        ctx.el("p", {class: "view-empty"}, "No child-role definitions are linked to this skill.")
      ])
    ]),
    ctx.el("section", {class: "card"}, [
      ctx.el("h2", {}, "Recipe-step attribution"),
      ctx.el("p", {class: "view-note"},
        "Step measures remain separate from child-role usage and from the skill-run total."),
      stepTable
    ]),
    ctx.el("section", {class: "card"}, [
      ctx.el("h2", {}, "Child-role attribution"), childTable, related
    ])
  ]);
});
