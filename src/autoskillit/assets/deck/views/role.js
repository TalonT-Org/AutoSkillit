DeckShell.registerView("role", ctx => {
  const roleName = ctx.route.entity;
  const identities = ctx.model.tables?.roles ?? [];
  const definitions = ctx.definitions ?? {};
  const rows = ctx.metrics ?? [];
  const roleNames = new Set([
    ...identities.map(row => row.role),
    ...Object.keys(definitions),
    ...(roleName == null ? [] : [roleName])
  ]);
  const roleRows = roleName == null ? [...roleNames].sort().map(role => ({
    role,
    definition: definitions[role],
    hasMetrics: rows.some(row => row.role === role)
  })) : rows.filter(row => row.role === roleName);

  function measureCell(measure) {
    return ctx.availabilityCell(measure ?? {state: "unavailable"});
  }

  function ratioCell(ratio, percent = false) {
    const value = DeckCore.formatRatio(ratio, percent);
    return ctx.el("div", {class: "view-measure"}, [
      value == null ? measureCell(ratio) : ctx.el("span", {}, value),
      ctx.el("small", {class: "view-sample"}, DeckCore.ratioSample(ratio)),
      DeckCore.reviewSignal(ctx, ratio)
    ]);
  }

  function toolMix(ratios = {}) {
    const tools = Object.entries(ratios.tool_mix ?? {});
    return tools.length ? ctx.el("div", {class: "view-pills"}, tools.map(([tool, ratio]) => {
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

  const spawningLevels = (ctx.selection?.level?.keys ?? []).map(key =>
    (ctx.chips?.level ?? []).find(chip => chip.key === key)?.label ?? key);
  const edges = ctx.relationships ?? [];
  const spawningTable = edges.length ? ctx.sortableTable({
    columns: [
      {key: "skill", label: "Spawning skill", cell: edge => ctx.entityLink(edge.skill, {
        view: "skill", entity: edge.skill
      })},
      {key: "harness", label: "Harness"},
      {key: "provider", label: "Child provider"}
    ],
    rows: edges,
    defaultSort: {key: "skill", dir: "asc"}
  }) : ctx.el("p", {class: "view-empty"}, "No verified spawning-skill links are recorded.");

  const harnessRows = roleRows.flatMap(row => (row.harnesses ?? []).map(cell => ({
    role: row.role,
    provider: row.provider,
    harness: cell.harness,
    models: cell.models ?? [],
    measures: cell.measures,
    ratios: cell.ratios
  })));
  const usageTable = harnessRows.length ? ctx.sortableTable({
    columns: [
      {key: "harness", label: "Harness"},
      {key: "provider", label: "Provider"},
      {key: "models", label: "Observed models", cell: row =>
        (row.models ?? []).join(", ") || "No model recorded"},
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
      {key: "tool_mix", label: "Observed tool mix", cell: row => toolMix(row.ratios)}
    ],
    rows: harnessRows,
    defaultSort: {key: "harness", dir: "asc"}
  }) : ctx.el("p", {class: "view-empty"}, "No child-invocation metrics match these facets.");

  const definition = roleName == null ? null : definitions[roleName];
  const available = definition?.state === "available";
  const definitionCard = roleName == null ? null : ctx.el("section", {class: "card"}, [
    ctx.el("h2", {}, "Role definition · " + roleName),
    available ? ctx.el("p", {}, definition.description ?? "Role definition loaded.") :
      ctx.el("p", {class: "view-review__reason"}, "Definition unavailable for " + roleName + "."),
    available ? ctx.el("p", {}, "Claude Code declared tools: " +
      ((definition.tools ?? []).join(", ") || "none recorded")) : null,
    available ? ctx.el("p", {}, "Claude Code declared model: " +
      (definition.model ?? "not declared")) : null,
    available ? ctx.el("p", {}, "Codex read-only tools: " +
      ((definition.reader_tools ?? []).join(", ") || "none declared")) : null,
    available ? ctx.el("p", {}, "Codex model: " +
      (definition.codex_model ?? "not declared")) : null,
    available ? ctx.el("pre", {}, definition.body ?? "") : null
  ]);
  const indexTable = roleName == null ? ctx.sortableTable({
    columns: [
      {key: "role", label: "Role", cell: row => ctx.entityLink(row.role, {
        view: "role", entity: row.role
      })},
      {key: "state", label: "Definition", cell: row => row.definition?.state ?? "unavailable"},
      {key: "hasMetrics", label: "Selected metrics", cell: row =>
        row.hasMetrics ? "available" : "none in this cohort"}
    ],
    rows: roleRows,
    defaultSort: {key: "role", dir: "asc"}
  }) : null;

  return ctx.el("div", {class: "view-stack"}, [
    ctx.el("section", {class: "card"}, [
      ctx.el("h2", {}, roleName ?? "Known specialized roles"),
      ctx.el("p", {class: "view-actor"}, roleName == null ?
        "Native specialized child actor · L0" : "Actor level: L0 · Spawning skill level: " +
        (spawningLevels.join(" + ") || "all observed levels")),
      ctx.el("p", {class: "view-lede"},
        "Observed child-invocation usage stays separate from its spawning skill's session spend."),
      indexTable
    ]),
    definitionCard,
    ctx.el("section", {class: "card"}, [
      ctx.el("h2", {}, "Observed role usage"),
      ctx.el("p", {class: "view-note"},
        "Observed models and tool mix come from child evidence. Declared tools and models above come from the role definition."),
      usageTable
    ]),
    ctx.el("section", {class: "card"}, [ctx.el("h2", {}, "Spawning skills"), spawningTable])
  ]);
});
