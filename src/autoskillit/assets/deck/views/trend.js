DeckShell.registerView("trend", ctx => {
  const DAY_MS = 86400000;
  const tokenFields = ["input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens"];
  const colors = ["s1", "s2", "s3", "s4"];
  const valueOf = measure => measure?.state === "measured" ? measure.value :
    measure?.state === "measured_zero" ? 0 : null;
  const groupText = row => [row.skill ?? "unknown skill", row.recipe ?? "unknown recipe",
    row.step ?? "unknown step", row.harness ?? "unknown harness",
    row.provider ?? "unknown provider", row.model ?? "unknown model"].join(" · ");
  const groupKey = row => JSON.stringify([row.skill, row.recipe, row.step,
    row.harness, row.provider, row.model]);
  const timeOf = row => row.time_ms ?? (row.day == null ? null : Date.parse(row.day + "T00:00:00Z"));

  function seriesGroups(rows) {
    const groups = new Map();
    rows.forEach(row => {
      const key = groupKey(row);
      if (!groups.has(key)) groups.set(key, {label: groupText(row), rows: []});
      groups.get(key).rows.push(row);
    });
    return [...groups.values()].map(group => ({...group, rows: [...group.rows].sort((a, b) =>
      (timeOf(a) ?? Infinity) - (timeOf(b) ?? Infinity))}));
  }

  function lineChart(rows, field, label, percent = false) {
    const groups = seriesGroups(rows);
    const timed = rows.map(timeOf).filter(value => value != null);
    const first = timed.reduce((minimum, value) => Math.min(minimum, value), Infinity);
    const last = timed.reduce((maximum, value) => Math.max(maximum, value), -Infinity);
    const values = rows.map(row => valueOf(
      field === "failure_share" ? row.failure_share : row.measures?.[field]
    )).filter(value => value !== null);
    const minValue = values.reduce((minimum, value) => Math.min(minimum, value), 0);
    const maxValue = Math.max(1, values.reduce((maximum, value) => Math.max(maximum, value), 0));
    const width = 820, height = Math.max(250, groups.length * 40 + 150);
    const left = 58, right = 18, top = 52 + groups.length * 13, bottom = 52;
    const plotWidth = width - left - right, plotHeight = height - top - bottom;
    const xFor = time => left + (last <= first ? plotWidth / 2 :
      (time - first) / (last - first) * plotWidth);
    const yFor = value => top + plotHeight - (value - minValue) / (maxValue - minValue) * plotHeight;
    const rendered = [
      ctx.svgElement("text", {x: left, y: 18, class: "deck-chart__title"}, label),
      ctx.svgElement("line", {x1: left, y1: top + plotHeight, x2: width - right,
        y2: top + plotHeight, class: "deck-chart__axis"}),
      ctx.svgElement("text", {x: 3, y: top + 4, class: "deck-chart__axis-label"},
        percent ? Math.round(maxValue * 100) + "%" : DeckCore.formatCount(maxValue)),
      ctx.svgElement("text", {x: 3, y: top + plotHeight, class: "deck-chart__axis-label"},
        percent ? Math.round(minValue * 100) + "%" : DeckCore.formatCount(minValue))
    ];
    if (timed.length) {
      rendered.push(ctx.svgElement("text", {x: left, y: height - 8,
        class: "deck-chart__axis-label"}, DeckCore.formatDate(first)));
      if (last !== first) rendered.push(ctx.svgElement("text", {x: width - right, y: height - 8,
        "text-anchor": "end", class: "deck-chart__axis-label"}, DeckCore.formatDate(last)));
    }
    groups.forEach((group, groupIndex) => {
      const color = colors[groupIndex % colors.length];
      let segment = [];
      let priorTime = null;
      const flush = () => {
        if (segment.length > 1) rendered.push(ctx.svgElement("polyline", {
          points: segment.map(point => point.x + "," + point.y).join(" "),
          class: "deck-chart__line deck-chart__line--" + color
        }));
        segment = [];
      };
      group.rows.forEach(row => {
        const time = timeOf(row);
        const measure = field === "failure_share" ? row.failure_share : row.measures?.[field];
        const value = valueOf(measure);
        if (time == null || value == null || (priorTime != null && time - priorTime > DAY_MS)) {
          flush();
          if (time != null && value == null) rendered.push(ctx.svgElement("rect", {
            x: xFor(time) - 4, y: top + plotHeight - 8, width: 8, height: 8,
            class: "deck-chart__missing", fill: "url(#deck-hatch)"}, [
            ctx.svgElement("title", {}, group.label + " · " + (row.day ?? "day") +
              ": no measured observation")
          ]));
          priorTime = time;
          if (value == null || time == null) return;
        }
        const x = xFor(time), y = yFor(value);
        segment.push({x, y});
        const sample = field === "failure_share" ? measure?.sample_size :
          measure?.observed_sessions;
        rendered.push(ctx.svgElement("circle", {cx: x, cy: y, r: 3.5,
          class: "deck-chart__point deck-chart__point--" + color}, [
          ctx.svgElement("title", {}, group.label + " · " + (row.day ?? "day") + ": " +
            (percent ? String(Math.round(value * 10000) / 100) + "%" :
              DeckCore.formatCount(value) + " tokens/session") + " · " +
            (sample == null ? "sample unavailable" : DeckCore.formatCount(sample) +
              (field === "failure_share" ? " known outcomes" : " observed sessions")))
        ]));
        priorTime = time;
      });
      flush();
      const legendY = 36 + groupIndex * 13;
      rendered.push(ctx.svgElement("line", {x1: left, y1: legendY - 3, x2: left + 16,
        y2: legendY - 3, class: "deck-chart__line deck-chart__line--" + color}));
      rendered.push(ctx.svgElement("text", {x: left + 22, y: legendY,
        class: "deck-chart__legend"}, group.label));
    });
    if (!timed.length || !groups.length) rendered.push(ctx.svgElement("text", {
      x: left + 8, y: top + 28, class: "deck-chart__empty"
    }, "No timed daily buckets match this measure."));
    return ctx.svgElement("svg", {class: "deck-chart", role: "img", "aria-label": label,
      viewBox: "0 0 " + width + " " + height}, rendered);
  }

  function measureText(measure, unit = "tokens/session") {
    const value = valueOf(measure);
    if (value === null) return [measure?.state === "no_observations" ? "no observations" :
      measure?.state ?? "unknown", measure?.reason].filter(Boolean).join(" · ");
    const sample = measure.observed_sessions ?? measure.sample_size;
    const eligible = measure.eligible_sessions ?? measure.eligible_count;
    const states = Object.entries(measure.state_counts ?? {}).map(([state, count]) =>
      state + " " + DeckCore.formatCount(count)).join(", ");
    return (value === 0 ? "0" : DeckCore.formatCount(value)) + " " +
      (measure.unit ?? unit) +
      (sample == null ? " · sample unavailable" : " · n=" + DeckCore.formatCount(sample)) +
      (eligible == null ? "" : " / " + DeckCore.formatCount(eligible) + " eligible") +
      (states ? " · " + states : "");
  }

  function dateRange(interval) {
    const start = interval?.covered_start_ms ?? interval?.start_ms;
    const end = interval?.covered_end_ms ?? interval?.end_ms;
    return start == null || end == null ? "no covered dates" :
      DeckCore.formatDate(start) + " → " + DeckCore.formatDate(end);
  }

  const comparisons = ctx.metrics?.comparisons ?? [];
  const comparisonRows = comparisons.flatMap(comparison => [
    ...tokenFields.map(field => ({...comparison, field, earlierMeasure: comparison.earlier?.measures?.[field],
      laterMeasure: comparison.later?.measures?.[field], delta: comparison.deltas?.[field]})),
    {...comparison, field: "failure_share", earlierMeasure: comparison.earlier?.failure_share,
      laterMeasure: comparison.later?.failure_share, delta: comparison.deltas?.failure_share}
  ]);
  const comparisonColumns = [
    {key: "skill", label: "Skill", cell: row => row.skill ?? "unknown"},
    {key: "step", label: "Step", cell: row => row.step ?? "unknown"},
    {key: "harness", label: "Harness"},
    {key: "provider", label: "Provider"},
    {key: "model", label: "Model", cell: row => row.model ?? "unknown"},
    {key: "field", label: "Measure"},
    {key: "earlier_range", label: "Earlier covered dates", cell: row => dateRange(row.earlier)},
    {key: "earlierMeasure", label: "Earlier", cell: row => measureText(row.earlierMeasure,
      row.field === "failure_share" ? "failure share" : "tokens/session")},
    {key: "later_range", label: "Later covered dates", cell: row => dateRange(row.later)},
    {key: "laterMeasure", label: "Later", cell: row => measureText(row.laterMeasure,
      row.field === "failure_share" ? "failure share" : "tokens/session")},
    {key: "delta", label: "Descriptive delta", cell: row => {
      const value = valueOf(row.delta);
      return value == null ? ["no observations", "unknown", "unavailable"].includes(row.delta?.state) ?
        row.delta.state + (row.delta.reason ? ": " + row.delta.reason : "") :
      row.delta?.reason ?? "insufficient observations" :
          (value === 0 ? "0" : String(Math.round(value * 100) / 100)) + " " +
          (row.delta.unit ?? "tokens/session") + " · n=" +
          (row.delta.earlier_samples == null ? "unavailable" :
            DeckCore.formatCount(row.delta.earlier_samples)) + "/" +
          (row.delta.later_samples == null ? "unavailable" :
            DeckCore.formatCount(row.delta.later_samples));
    }}
  ];
  const tableColumns = [
    {key: "day", label: "UTC day"},
    {key: "skill", label: "Skill", cell: row => row.skill ?? "unknown"},
    {key: "step", label: "Step", cell: row => row.step ?? "unknown"},
    {key: "harness", label: "Harness"},
    {key: "provider", label: "Provider"},
    {key: "model", label: "Resolved model", cell: row => row.model ?? "unknown"},
    ...tokenFields.map(field => ({key: field, label: field.replaceAll("_", " "),
      cell: row => measureText(row.measures?.[field])})),
    {key: "failure_share", label: "Failure share", cell: row => measureText(
      row.failure_share, "failure share")},
    {key: "eligible_sessions", label: "Eligible sessions", numeric: true},
    {key: "untimed_sessions", label: "Untimed", numeric: true},
    {key: "future_sessions", label: "Future excluded", numeric: true}
  ];
  const history = ctx.rows.find(row => row.retained_from_ms != null)?.retained_from_ms;

  return ctx.el("div", {class: "view-stack view-trend"}, [
    ctx.el("section", {class: "card"}, [
      ctx.el("h1", {}, "Daily token usage and failure share"),
      ctx.el("p", {class: "view-lede"}, "Each line keeps its skill, step, harness, provider, " +
        "and resolved model identity. Points show field-specific measured session samples; missing " +
        "or unsupported days break the line instead of becoming zero."),
      history == null ? null : ctx.el("p", {class: "view-note"}, "Retained history starts " +
        DeckCore.formatDate(history) + ". Untimed sessions remain outside daily buckets; future " +
        "timestamps are excluded."),
      ...tokenFields.map(field => lineChart(ctx.rows, field,
        field.replaceAll("_", " ") + " per observed session")),
      lineChart(ctx.rows, "failure_share", "Known-outcome session failure share", true),
      ctx.el("p", {class: "view-legend"}, "Hatched marks identify missing observations. " +
        "measured_zero is an explicit zero; unknown, unavailable, and not_applicable remain " +
        "textually distinct in the table. Failure share uses known outcomes only; the comparison " +
        "below is descriptive, without a causal or sustained-change claim."),
      ctx.sortableTable({columns: tableColumns, rows: [...ctx.rows],
        defaultSort: {key: "day", dir: "asc"}})
    ]),
    ctx.el("section", {class: "card"}, [
      ctx.el("h2", {}, "Earlier and later intervals"),
      ctx.el("p", {class: "view-note"}, "The selected interval is split at its exact temporal " +
        "midpoint; midpoint records belong to the later half. Each value carries its own observed " +
        "sample count and covered date range."),
      comparisonRows.length ? ctx.sortableTable({columns: comparisonColumns,
        rows: comparisonRows, defaultSort: {key: "field", dir: "asc"}}) :
        ctx.el("p", {class: "view-empty"}, "No earlier/later comparison has two observed sides."),
      ctx.el("div", {class: "view-links"}, [
        ctx.el("a", {href: ctx.href({view: "errors"})}, "Inspect failure symptoms"),
        ctx.el("a", {href: ctx.href({view: "gaps"})}, "Inspect evidence gaps")
      ])
    ])
  ]);
});
