DeckShell.registerView("context", ctx => {
  const turnRows = ctx.rows.filter(row => row.kind === "turn");
  const coverageRows = ctx.rows.filter(row => row.kind === "coverage");
  const previousModels = new Map();
  const modelChanges = new Map();
  turnRows.forEach(row => {
    const prior = previousModels.get(row.session_key);
    modelChanges.set(row.key, prior === undefined ? "first recorded model" :
      prior === row.model ? "same as previous turn" : "changed from " + (prior ?? "unknown"));
    previousModels.set(row.session_key, row.model);
  });
  const valueOf = measure => measure?.state === "measured" ? measure.value :
    measure?.state === "measured_zero" ? 0 : null;
  const stateName = measure => measure?.state ?? "unknown";
  const countCell = measure => ctx.availabilityCell(measure ?? {state: "unknown"});
  const stateCell = measure => DeckCore.isPrimitiveState(measure?.state) ? countCell(measure) :
    ctx.el("span", {class: "coverage-state coverage-state--" + (measure?.state ?? "unknown")},
      measure?.state === "no_observations" ? "no observations" :
        measure?.state === "mixed" ? "mixed coverage" : measure?.state ?? "unknown");
  const countText = measure => {
    const value = valueOf(measure);
    return value == null ? stateName(measure) : DeckCore.formatCount(value);
  };
  const turnLabel = row => "turn " + (row.ordinal ?? "?") + " · " +
    (row.model ?? "model unavailable");

  function tokenChart(rows, fields, label, title) {
    const width = 760, left = 230, right = 16, rowHeight = 22, gap = 5;
    const plotWidth = width - left - right;
    const totals = rows.map(row => fields.reduce((sum, field) => {
      const value = valueOf(row[field.key]);
      return sum + (value == null ? 0 : value);
    }, 0));
    const maximum = Math.max(1, totals.reduce((current, total) => Math.max(current, total), 0));
    const height = Math.max(44, rows.length * (rowHeight + gap) + 28);
    const children = [ctx.svgElement("text", {x: 0, y: 14, class: "deck-chart__title"}, title)];
    rows.forEach((row, index) => {
      const y = 24 + index * (rowHeight + gap);
      children.push(ctx.svgElement("text", {x: 0, y: y + 15, class: "deck-chart__label"},
        turnLabel(row)));
      children.push(ctx.svgElement("rect", {x: left, y, width: plotWidth, height: 15,
        class: "deck-chart__track"}));
      let x = left;
      fields.forEach(field => {
        const measure = row[field.key];
        const value = valueOf(measure);
        if (value === 0) {
          children.push(ctx.svgElement("circle", {cx: x, cy: y + 7.5, r: 3,
            class: "deck-chart__zero", "aria-label": field.label + " measured zero"}));
        } else if (value > 0) {
          const barWidth = value / maximum * plotWidth;
          children.push(ctx.svgElement("rect", {x, y, width: barWidth, height: 15,
            class: "deck-chart__bar deck-chart__bar--" + field.key}, [
            ctx.svgElement("title", {}, field.label + ": " + DeckCore.formatCount(value) + " tokens")
          ]));
          x += barWidth;
        }
      });
    });
    return ctx.svgElement("svg", {class: "deck-chart", role: "img", "aria-label": label,
      viewBox: "0 0 " + width + " " + height}, children);
  }

  function occupancyChart(rows) {
    const width = 760, height = 230, left = 52, right = 18, top = 24, bottom = 40;
    const points = rows.map((row, index) => ({row, index,
      value: valueOf(row.context_fraction_percent)}));
    const observed = points.filter(point => point.value !== null);
    const maximum = Math.max(1, observed.reduce((current, point) =>
      Math.max(current, point.value), 0));
    const plotWidth = width - left - right, plotHeight = height - top - bottom;
    const xFor = index => left + (rows.length < 2 ? plotWidth / 2 :
      index / (rows.length - 1) * plotWidth);
    const yFor = value => top + plotHeight - value / maximum * plotHeight;
    const children = [
      ctx.svgElement("text", {x: left, y: 14, class: "deck-chart__title"},
        "Cache-read proxy as percent of each turn model's context window"),
      ctx.svgElement("line", {x1: left, y1: top + plotHeight, x2: width - right,
        y2: top + plotHeight, class: "deck-chart__axis"}),
      ctx.svgElement("text", {x: 3, y: top + 4, class: "deck-chart__axis-label"},
        Math.round(maximum) + "%"),
      ctx.svgElement("text", {x: 3, y: top + plotHeight, class: "deck-chart__axis-label"}, "0%")
    ];
    let segment = [];
    let priorSession = null;
    const flush = () => {
      if (segment.length > 1) {
        children.push(ctx.svgElement("polyline", {points: segment.map(point =>
          point.x + "," + point.y).join(" "), class: "deck-chart__line"}));
      }
      segment = [];
    };
    points.forEach(point => {
      if (point.index > 0 && point.row.session_key !== priorSession) flush();
      priorSession = point.row.session_key;
      if (point.value === null) {
        flush();
        const x = xFor(point.index);
        children.push(ctx.svgElement("rect", {x: x - 4, y: top + plotHeight - 8,
          width: 8, height: 8, class: "deck-chart__missing", fill: "url(#deck-hatch)"}, [
          ctx.svgElement("title", {}, "Occupancy unavailable for " + turnLabel(point.row))
        ]));
        return;
      }
      const x = xFor(point.index), y = yFor(point.value);
      segment.push({x, y});
      children.push(ctx.svgElement("circle", {cx: x, cy: y, r: 3.5,
        class: point.value === 0 ? "deck-chart__point deck-chart__zero" : "deck-chart__point"}, [
        ctx.svgElement("title", {}, turnLabel(point.row) + ": " +
          (point.value === 0 ? "0% observed" : String(Math.round(point.value * 100) / 100) + "%"))
      ]));
    });
    flush();
    if (rows.length) {
      children.push(ctx.svgElement("text", {x: left, y: height - 8, class: "deck-chart__axis-label"},
        turnLabel(rows[0])));
      if (rows.length > 1) children.push(ctx.svgElement("text", {x: width - right, y: height - 8,
        "text-anchor": "end", class: "deck-chart__axis-label"}, turnLabel(rows[rows.length - 1])));
    }
    if (!observed.length) children.push(ctx.svgElement("text", {x: left + 8, y: top + 28,
      class: "deck-chart__empty"}, "No measured occupancy points in this selection."));
    return ctx.svgElement("svg", {class: "deck-chart", role: "img",
      "aria-label": "Per-turn cache-read proxy occupancy; missing values break the line",
      viewBox: "0 0 " + width + " " + height}, children);
  }

  function pairedSpanChart(rows) {
    const width = 760, left = 210, right = 135, rowHeight = 38, gap = 8;
    const plotWidth = width - left - right;
    const maxValue = Math.max(1, rows.reduce((current, row) => [
      row.prompt?.observed_subset ?? row.prompt,
      row.returned?.observed_subset ?? row.returned
    ]
      .reduce((maximum, measure) => {
        const value = valueOf(measure);
        return value === null ? maximum : Math.max(maximum, value);
      }, current), 0));
    const height = Math.max(46, rows.length * (rowHeight + gap) + 28);
    const children = [ctx.svgElement("text", {x: 0, y: 14, class: "deck-chart__title"},
      "Observed-subset parent prompt and subagent returned text tokens")];
    rows.forEach((row, index) => {
      const y = 24 + index * (rowHeight + gap);
      children.push(ctx.svgElement("text", {x: 0, y: y + 13, class: "deck-chart__label"},
        [row.skill, row.recipe, row.step, row.harness, row.provider, row.model]
          .filter(Boolean).join(" · ")));
      for (const [key, measure, offset] of [["prompt", row.prompt, 0],
        ["returned", row.returned, 18]]) {
        const observedSubset = measure?.observed_subset ?? measure;
        const value = valueOf(observedSubset);
        const barY = y + offset;
        if (value !== null && value > 0) {
          children.push(ctx.svgElement("rect", {x: left, y: barY, width: value / maxValue * plotWidth,
            height: 11, class: "deck-chart__bar deck-chart__bar--" + key}, [
            ctx.svgElement("title", {}, key + " observed subset: " +
              DeckCore.formatCount(value) + " tokens")
          ]));
        } else if (value === 0) {
          children.push(ctx.svgElement("circle", {cx: left, cy: barY + 5.5, r: 3,
            class: "deck-chart__zero"}));
        }
        children.push(ctx.svgElement("text", {x: width - right + 8, y: barY + 10,
          class: "deck-chart__axis-label"}, key + " observed subset · " + countText(observedSubset)));
      }
    });
    return ctx.svgElement("svg", {class: "deck-chart", role: "img",
      "aria-label": "Paired parent prompt and subagent returned text token counts",
      viewBox: "0 0 " + width + " " + height}, children);
  }

  const tableRows = [...ctx.rows];
  const columns = [
    {key: "session_key", label: "Owning session", cell: row => row.session_key == null ? "unattributed" :
      ctx.el("a", {href: ctx.href({view: "context", params: {session: [row.session_key]}})},
        row.session_key)},
    {key: "ordinal", label: "Turn", cell: row => row.kind === "coverage" ?
      "Ledger coverage" : row.ordinal ?? "—"},
    {key: "time_ms", label: "Recorded (UTC)", cell: row => row.time_ms == null ? "untimed" :
      new Date(row.time_ms).toISOString()},
    {key: "model", label: "Resolved model", cell: row => row.model ?? "unknown"},
    {key: "model_change", label: "Model history", cell: row => row.kind === "coverage" ? "—" :
      modelChanges.get(row.key) ?? "first recorded model"},
    {key: "cache_read_tokens", label: "Cache read", cell: row => countCell(row.cache_read_tokens)},
    {key: "cache_write_tokens", label: "Cache write", cell: row => countCell(row.cache_write_tokens)},
    {key: "output_tokens", label: "Output", cell: row => countCell(row.output_tokens)},
    {key: "context_window_tokens", label: "Model window", cell: row => typeof
      row.context_window_tokens === "number" ? DeckCore.formatCount(row.context_window_tokens) :
        row.context_window_tokens == null ? "unknown" : stateCell(row.context_window_tokens)},
    {key: "context_fraction", label: "Stored fraction (validation)", cell: row =>
      typeof row.context_fraction === "number" ?
        String(Math.round(row.context_fraction * 10000) / 100) + "%" : "not recorded"},
    {key: "fraction_disagreement", label: "Stored fraction check", cell: row =>
      row.fraction_disagreement === true ? "gap: differs from recomputed value" :
        row.fraction_disagreement === false ? "agrees" : "not compared"},
    {key: "context_fraction_percent", label: "Cache-read window", cell: row =>
      row.context_fraction_percent ? stateCell(row.context_fraction_percent) : "not applicable"}
  ];
  const inputFields = [{key: "input_tokens", label: "Input"}];
  const tokenFields = [
    {key: "cache_read_tokens", label: "Cache read"},
    {key: "cache_write_tokens", label: "Cache write"},
    {key: "output_tokens", label: "Output"}
  ];
  const tokenLegend = tokenFields.map(field => ctx.el("span", {
    class: "deck-series-legend__item"
  }, [ctx.el("span", {class: "deck-series-swatch deck-series-swatch--" + field.key,
    "aria-hidden": "true"}), ctx.el("span", {}, field.label)]));
  const inputChart = tokenChart(turnRows, inputFields, "Inclusive input tokens by turn",
    "Input tokens (inclusive; separate scale)");
  inputChart.setAttribute("style", "display:none");
  const inputToggle = ctx.el("button", {type: "button", class: "context-input-toggle",
    "aria-pressed": "false"}, "Show inclusive input series");
  let inputShown = false;
  inputToggle.addEventListener("click", () => {
    inputShown = !inputShown;
    inputChart.setAttribute("style", inputShown ? "display:block" : "display:none");
    inputToggle.setAttribute("aria-pressed", String(inputShown));
    inputToggle.textContent = inputShown ? "Hide inclusive input series" :
      "Show inclusive input series";
  });
  const parentRows = ctx.metrics?.parent_context ?? [];
  function coverageText(measure) {
    const states = Object.entries(measure?.state_counts ?? {}).map(([state, count]) =>
      state + " " + DeckCore.formatCount(count));
    const reasons = Object.entries(measure?.reason_counts ?? {}).map(([reason, count]) =>
      reason + " " + DeckCore.formatCount(count));
    const population = measure?.observation_count == null ? null :
      DeckCore.formatCount(measure.observation_count) + " observed / " +
      (measure.eligible_count == null ? "eligible count unavailable" :
        DeckCore.formatCount(measure.eligible_count) + " eligible");
    return [population, ...states, ...reasons]
      .filter(Boolean).join(" · ") || "coverage unavailable";
  }
  const provenanceText = provenance => (provenance ?? []).map(entry => typeof entry === "string" ?
    entry : [entry.source_id, entry.encoding, entry.tokenizer_version,
      [entry.child_role, entry.child_harness, entry.child_provider, entry.child_model]
        .filter(Boolean).join(" / ")].filter(Boolean).join(" · "))
    .filter(Boolean).join("; ") || "no source span";
  const comparisonCell = row => {
    const value = valueOf(row.comparison);
    const samples = row.comparison?.sample_size ?? row.matched_invocations;
    return value === null ? stateCell(row.comparison) : ctx.el("span", {class: "view-measure"}, [
      ctx.el("span", {}, (value === 0 ? "0" : String(Math.round(value * 100) / 100)) + "×"),
      ctx.el("small", {class: "view-sample"}, samples == null ? "sample unavailable" :
        DeckCore.formatCount(samples) + " matched invocations")
    ]);
  };
  const parentColumns = [
    {key: "skill", label: "Parent skill", cell: row => row.skill ?? "unknown"},
    {key: "recipe", label: "Recipe", cell: row => row.recipe ?? "unknown"},
    {key: "step", label: "Step", cell: row => row.step ?? "unknown"},
    {key: "harness", label: "Parent harness"},
    {key: "provider", label: "Parent provider"},
    {key: "model", label: "Parent invocation model"},
    {key: "prompt", label: "Prompt tokens", cell: row => stateCell(row.prompt)},
    {key: "prompt_observed_subset", label: "Prompt observed subset", cell: row =>
      stateCell(row.prompt?.observed_subset)},
    {key: "prompt_coverage", label: "Prompt coverage", cell: row => coverageText(row.prompt)},
    {key: "returned", label: "Returned text tokens", cell: row => stateCell(row.returned)},
    {key: "returned_observed_subset", label: "Return observed subset", cell: row =>
      stateCell(row.returned?.observed_subset)},
    {key: "returned_coverage", label: "Return coverage", cell: row => coverageText(row.returned)},
    {key: "eligible_invocations", label: "Eligible", numeric: true},
    {key: "matched_invocations", label: "Matched", numeric: true},
    {key: "untimed_excluded", label: "Untimed excluded", numeric: true},
    {key: "provenance", label: "Source / encoder provenance", cell: row =>
      provenanceText(row.provenance)},
    {key: "source_basis", label: "Count basis"},
    {key: "comparison", label: "Returned / prompt ratio", cell: comparisonCell}
  ];
  const links = ["gaps", "parity"].map(view => ctx.el("a", {href: ctx.href({view})},
    "Open " + view + " coverage"));

  return ctx.el("div", {class: "view-stack view-context"}, [
    ctx.el("section", {class: "card"}, [
      ctx.el("h1", {}, "Context occupancy and per-turn tokens"),
      ctx.el("p", {class: "view-lede"}, "Occupancy is cache-read tokens divided by the " +
        "resolved turn model's context window, shown as a percent without clamping. The token " +
        "stack uses cache read, cache write, and output. Input is an inclusive count, so it is " +
        "shown only as a separate optional series and never added to that stack."),
      turnRows.length ? tokenChart(turnRows, tokenFields, "Cache read, cache write, and output by turn",
        "Per-turn token breakdown") : ctx.el("p", {class: "view-empty"},
        "No observed turn rows match this selection."),
      ctx.el("div", {class: "deck-series-legend", "aria-label": "Token series legend"},
        tokenLegend),
      inputToggle, inputChart,
      ctx.el("p", {class: "view-legend"}, "Measured values are amounts; measured_zero is an " +
        "explicit zero marker. Unknown, unavailable, and not_applicable remain labeled in the " +
        "table and are not drawn as zero-height observations."),
      occupancyChart(turnRows),
      ctx.el("p", {class: "view-note"}, "Hatched marks identify missing occupancy. The line " +
        "breaks at every missing or unsupported turn; the adjacent table spells out each " +
        "availability state."),
      ctx.sortableTable({columns, rows: tableRows, defaultSort: {key: "time_ms", dir: "asc"}})
    ]),
    coverageRows.length ? ctx.el("section", {class: "card"}, [
      ctx.el("h2", {}, "Turn ledger coverage"),
      ctx.sortableTable({columns: [
        {key: "session_key", label: "Owning session"},
        {key: "turn_usage_state", label: "Ledger state", cell: row => row.turn_usage_state ===
          "observed" ? "observed" : ctx.availabilityCell({state: "unavailable"})},
        {key: "turn_usage_reason", label: "Reason", cell: row => row.turn_usage_reason ?? "—"},
        {key: "request_model", label: "Session request model", cell: row => row.request_model ?? "unknown"}
      ], rows: coverageRows, defaultSort: {key: "session_key", dir: "asc"}})
    ]) : null,
    ctx.el("section", {class: "card"}, [
      ctx.el("h2", {}, "Parent prompt and subagent returned text"),
      ctx.el("p", {class: "view-lede"}, "These are tokenizer counts of the recorded prompt " +
        "and returned text at the parent invocation model. They exclude argument metadata, " +
        "hidden/system/framing tokens, and unrelated parent work. Partial source coverage is " +
        "shown beside observed-subset comparisons."),
      parentRows.length ? pairedSpanChart(parentRows) : ctx.el("p", {class: "view-empty"},
        "No verified parent prompt/return spans match this selection."),
      parentRows.length ? ctx.sortableTable({columns: parentColumns, rows: parentRows,
        defaultSort: {key: "harness", dir: "asc"}}) : null,
      ctx.el("div", {class: "view-links"}, links)
    ])
  ]);
});
