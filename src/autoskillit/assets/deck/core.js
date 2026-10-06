globalThis.DeckCore = (() => {
  "use strict";
  const CHIP_STATES = Object.freeze({LIVE: "live", STRUCK: "struck", ABSENT: "absent"});
  const DAY_MS = 86400000;
  const enc = encodeURIComponent;
  const formatCount = n => new Intl.NumberFormat("en-US").format(n);
  const formatDate = ms => new Date(ms).toISOString().slice(0, 10);

  function formatRatio(ratio, percent = false) {
    if (!ratio || (ratio.state !== "measured" && ratio.state !== "measured_zero")) return null;
    if (ratio.state === "measured_zero") return percent ? "0%" : "0";
    if (typeof ratio.value !== "number" || !Number.isFinite(ratio.value)) return null;
    const value = Math.round(ratio.value * (percent ? 100 : 1) * 100) / 100;
    return String(value) + (percent ? "%" : "");
  }

  function ratioSample(ratio) {
    if (ratio?.sample_size == null) return "sample size unavailable";
    const unit = ratio.sample_unit === "skill-run" ? "skill run" :
      ratio.sample_unit === "child-invocation" ? "child invocation" : ratio.sample_unit;
    return formatCount(ratio.sample_size) + " " + unit + (ratio.sample_size === 1 ? "" : "s");
  }

  function reviewEligibility(ratio, definitions) {
    const roles = [...new Set(ratio?.definition_roles ?? [])];
    if (!ratio || (ratio.state !== "measured" && ratio.state !== "measured_zero")) {
      return {eligible: false,
        reason: ratio?.review_reason || "No measured review signal is available.", roles};
    }
    if (ratio.review_eligible !== true || !(ratio.sample_size > 0)) {
      return {eligible: false,
        reason: ratio.review_reason || "This signal is not eligible for review.", roles};
    }
    if (!roles.length) {
      return {eligible: false,
        reason: "No contributor definitions are linked to this signal.", roles};
    }
    const unavailable = roles.filter(role => definitions?.[role]?.state !== "available");
    if (unavailable.length) {
      return {eligible: false,
        reason: "Contributor definitions unavailable: " + unavailable.join(", ") + ".", roles};
    }
    return {eligible: true, reason: null, roles};
  }

  function reviewSignal(ctx, ratio) {
    const eligibility = reviewEligibility(ratio, ctx.definitions);
    const links = eligibility.roles.map(role => ctx.entityLink(role, {
      view: "role", entity: role
    }));
    const definitions = links.length ? ctx.el("div", {
      class: "view-review__definitions",
      "aria-label": "Contributor definitions"
    }, links) : null;
    const marker = ctx.el("span", {
      class: "view-review__marker",
      "aria-live": "polite",
      hidden: true
    }, "Flagged for review");
    const button = ctx.el("button", {
      type: "button",
      class: "view-review__flag",
      "data-review-flag": "true",
      "aria-label": "Flag this signal for review",
      "aria-pressed": "false",
      disabled: !eligibility.eligible
    }, "Flag for review");
    let flagged = false;
    if (eligibility.eligible) {
      button.addEventListener("click", () => {
        flagged = !flagged;
        button.setAttribute("aria-pressed", String(flagged));
        marker.hidden = !flagged;
      });
    }
    return ctx.el("div", {class: "view-review-signal", "data-review-signal": "true"}, [
      definitions,
      button,
      marker,
      eligibility.reason ? ctx.el("p", {class: "view-review__reason"}, eligibility.reason) : null
    ]);
  }
  function measureCell(ctx, measure) {
    return ctx.availabilityCell(measure ?? {state: "unavailable"});
  }

  function ratioCell(ctx, ratio, percent = false) {
    const value = formatRatio(ratio, percent);
    return ctx.el("div", {class: "view-measure"}, [
      value == null ? measureCell(ctx, ratio) : ctx.el("span", {}, value),
      ctx.el("small", {class: "view-sample"}, ratioSample(ratio)),
      reviewSignal(ctx, ratio)
    ]);
  }

  function definitionCard(ctx, role) {
    const definition = ctx.definitions?.[role];
    const available = definition?.state === "available";
    return ctx.el("article", {class: "view-definition"}, [
      ctx.el("h3", {}, ctx.entityLink(role, {view: "role", entity: role})),
      available ? ctx.el("p", {}, definition.description ?? "Role definition loaded.") :
        ctx.el("p", {class: "view-review__reason"}, "Definition unavailable for " + role + "."),
      available ? ctx.el("p", {}, "Declared tools: " +
        ((definition.tools ?? []).join(", ") || "none recorded")) : null,
      available ? ctx.el("pre", {}, definition.body ?? "") : null
    ]);
  }

  function toolMix(ctx, ratios = {}) {
    const tools = Object.entries(ratios.tool_mix ?? {});
    return tools.length ? ctx.el("div", {class: "view-pills"}, tools.map(([tool, ratio]) => {
      const value = formatRatio(ratio, true);
      return ctx.el("span", {}, [
        ctx.el("span", {}, tool),
        ctx.el("span", {}, " · "),
        value == null ? ctx.availabilityCell(ratio) : ctx.el("span", {}, value),
        ctx.el("small", {class: "view-sample"}, ratioSample(ratio)),
        reviewSignal(ctx, ratio)
      ]);
    })) : ctx.el("span", {class: "view-empty"}, "No observed tool calls");
  }

  function roleHarnessRows(rows) {
    return rows.flatMap(row => (row.harnesses ?? []).map(cell => ({
      role: row.role,
      provider: row.provider,
      harness: cell.harness,
      models: cell.models ?? [],
      measures: cell.measures,
      ratios: cell.ratios
    })));
  }

  const decodeTable = ({columns, rows}) => rows.map(r =>
    Object.fromEntries(columns.map((c, i) => [c, r[i]])));

  function encodeRoute(route, cohortKeys) {
    let path = "#/" + enc(route.view);
    if (route.entity != null) path += "/" + enc(route.entity);
    const params = route.params || {};
    const keys = [...new Set([...cohortKeys, "sort", ...Object.keys(params).sort()])];
    const pairs = [];
    for (const key of keys) {
      const values = params[key];
      if (values == null || values.length === 0) continue;
      pairs.push(enc(key) + "=" + values.map(enc).join(","));
    }
    return path + (pairs.length ? "?" + pairs.join("&") : "");
  }

  function decodeRoute(hash, landing) {
    const text = hash.replace(/^#/, "").replace(/^\//, "");
    const split = text.indexOf("?");
    const path = (split < 0 ? text : text.slice(0, split)).split("/");
    const query = split < 0 ? "" : text.slice(split + 1);
    let view = landing, entity = null;
    try { view = decodeURIComponent(path[0]) || landing; } catch (_) {}
    if (path.length > 1) {
      try { entity = decodeURIComponent(path[1]); } catch (_) {}
    }
    const params = {};
    for (const pair of query.split("&")) {
      const at = pair.indexOf("=");
      if (at <= 0 || at === pair.length - 1) continue;
      try {
        const key = decodeURIComponent(pair.slice(0, at));
        const values = pair.slice(at + 1).split(",").map(decodeURIComponent);
        if (key && values.every(v => v !== "")) {
          Object.defineProperty(params, key, {value: values, enumerable: true,
            writable: true, configurable: true});
        }
      } catch (_) {}
    }
    return {view, entity, params};
  }

  function hrefFor(route, target, cohortKeys) {
    const params = Object.fromEntries(cohortKeys.filter(k =>
      Object.hasOwn(route.params, k)).map(k => [k, [...route.params[k]]]));
    for (const [key, value] of Object.entries(target.params || {})) {
      if (value === null) delete params[key];
      else Object.defineProperty(params, key, {value, enumerable: true,
        writable: true, configurable: true});
    }
    return encodeRoute({view: target.view, entity: target.entity ?? null, params}, cohortKeys);
  }

  function effectiveSelection(chips, selected) {
    const live = chips.filter(c => c.state === "live").map(c => c.key);
    if (selected == null || selected.length === 0) {
      return {keys: live, dropped: [], widened: false};
    }
    const keys = live.filter(k => selected.includes(k));
    const dropped = selected.filter(k => !live.includes(k));
    return {keys, dropped, widened: false};
  }

  function toggleSelection(chips, selected, key) {
    const live = chips.filter(c => c.state === "live").map(c => c.key);
    const current = effectiveSelection(chips, selected).keys;
    const next = live.filter(k => k === key ? !current.includes(k) : current.includes(k));
    if (!next.length) return current;
    return next.length === live.length ? null : next;
  }

  function windowSelection(chips, selected) {
    const want = selected?.[0];
    const chip = want == null ? chips.find(c => c.state === "live" && c.key === "all") :
      chips.find(c => c.state === "live" && c.key === want);
    return {chip: chip ?? null, dropped: want != null && !chip ? [want] : []};
  }

  function sameValues(left, right) {
    return left.length === right.length && left.every(value => right.includes(value));
  }

  function selectPrepared(prepared, viewId, chips, route) {
    prepared = prepared ?? {};
    chips = chips ?? {};
    const params = route.params ?? {};
    const window = windowSelection(chips.window ?? [], params.window ?? null);
    const level = effectiveSelection(chips.level ?? [], params.level ?? null);
    const harness = effectiveSelection(chips.harness ?? [], params.harness ?? null);
    const provider = effectiveSelection(chips.provider ?? [], params.provider ?? null);
    const harnesses = new Set((chips.harness ?? [])
      .filter(chip => harness.keys.includes(chip.key)).map(chip => chip.match));
    const providers = new Set((chips.provider ?? [])
      .filter(chip => provider.keys.includes(chip.key)).map(chip => chip.match));

    function selectedRows(blocks) {
      if (!window.chip) return [];
      const inWindow = blocks.filter(block => block.window === window.chip.key);
      const domain = [...new Set(inWindow.flatMap(block => block.levels))];
      const selectedLevels = new Set(level.keys
        .map(key => (chips.level ?? []).find(chip => chip.key === key)?.match)
      );
      const requested = domain.filter(value => selectedLevels.has(value));
      if (!requested.length) return [];
      const block = inWindow.find(item => sameValues(item.levels, requested));
      return block?.rows ?? [];
    }

    let skillRows = viewId === "role" ? [] : selectedRows(prepared.skills ?? []).filter(row =>
      harnesses.has(row.harness) && providers.has(row.provider));
    let roleRows = viewId === "skill" ? [] : selectedRows(prepared.roles ?? []).filter(row =>
      providers.has(row.provider)).map(row => ({...row,
      harnesses: (row.harnesses ?? []).filter(cell => harnesses.has(cell.harness))
    })).filter(row => row.harnesses.length > 0);
    if (viewId === "skill" && route.entity != null) {
      skillRows = skillRows.filter(row => row.skill === route.entity);
    }
    if (viewId === "role" && route.entity != null) {
      roleRows = roleRows.filter(row => row.role === route.entity);
    }
    const relationships = (prepared.relationships ?? []).filter(edge => {
      if (viewId === "skill" && route.entity != null) return edge.skill === route.entity;
      if (viewId === "role" && route.entity != null) return edge.role === route.entity;
      return true;
    });

    return {
      skillRows,
      roleRows,
      relationships,
      definitions: prepared.definitions ?? {},
      viewHistory: prepared.view_histories?.[viewId] ?? null,
      selection: {window, level, harness, provider}
    };
  }

  function filterRows(model, rows, chips, route) {
    let filtered = rows;
    for (const facet of model.facets.filter(f => f.kind === "values")) {
      const keys = effectiveSelection(chips[facet.id], route.params[facet.id] ?? null).keys;
      const allowed = new Set(chips[facet.id].filter(c => keys.includes(c.key)).map(c => c.match));
      filtered = filtered.filter(r => allowed.has(r[facet.column] ?? null));
    }
    let untimed = 0;
    const window = windowSelection(chips.window, route.params.window ?? null).chip;
    if (!window) {
      filtered = [];
    } else if (window.days != null) {
      filtered = filtered.filter(row => {
        if (row.time_ms == null) { untimed += 1; return false; }
        return row.time_ms >= model.generated_at_ms - window.days * DAY_MS;
      });
    }
    return {rows: filtered, untimed};
  }

  const reasonText = chip => chip.reason + (chip.issue != null ? " (#" + chip.issue + ")" : "");
  function droppedNote(chips, key) {
    const chip = chips.find(c => c.key === key);
    return chip ? chip.label + " is not selectable on this view — " + reasonText(chip) :
      key + " does not appear in this index";
  }

  function populationSentence(model, chips, route, result) {
    const n = result.rows.length;
    const notes = [], parts = [];
    for (const facet of model.facets.filter(f => f.kind === "values")) {
      const values = chips[facet.id];
      const selection = effectiveSelection(values, route.params[facet.id] ?? null);
      const live = values.filter(c => c.state === "live");
      const labels = values.filter(c => selection.keys.includes(c.key)).map(c => c.label);
      const selected = !live.length ? "none recorded" : !selection.keys.length ? "none selected" :
        labels.join(" + ") + (selection.keys.length === live.length ? " (all)" : "");
      parts.push(facet.label + " " + selected);
      notes.push(...selection.dropped.map(k => droppedNote(values, k)));
    }
    const selection = windowSelection(chips.window, route.params.window ?? null);
    const window = selection.chip;
    if (!window) {
      parts.push("window unavailable");
    } else if (window.days != null) {
      parts.push("window last " + window.label + " (" +
        formatDate(model.generated_at_ms - window.days * DAY_MS) + " → " +
        formatDate(model.generated_at_ms) + ")");
    } else {
      parts.push("window all history" + (model.history.first_ms == null ? "" :
        " (" + formatDate(model.history.first_ms) + " → " + formatDate(model.generated_at_ms) + ")"));
    }
    notes.push(...selection.dropped.map(k => droppedNote(chips.window, k)));
    if (result.untimed) notes.push(formatCount(result.untimed) + (result.untimed === 1 ?
      " run without a timestamp falls outside every window" :
      " runs without a timestamp fall outside every window"));
    return {headline: formatCount(n) + (n === 1 ? " run" : " runs"), detail: parts.join(" · "), notes};
  }

  function summarizePairs(rows) {
    const groups = new Map();
    for (const row of rows) {
      const key = JSON.stringify([row.harness, row.provider]);
      if (!groups.has(key)) groups.set(key, {harness: row.harness, provider: row.provider,
        runs: 0, skills: new Set(), first_ms: null, last_ms: null});
      const group = groups.get(key);
      group.runs += 1;
      if (row.skill != null) group.skills.add(row.skill);
      if (row.time_ms != null) {
        group.first_ms = group.first_ms == null ? row.time_ms : Math.min(group.first_ms, row.time_ms);
        group.last_ms = group.last_ms == null ? row.time_ms : Math.max(group.last_ms, row.time_ms);
      }
    }
    return [...groups.values()].map(g => ({...g, skills: g.skills.size})).sort((a, b) =>
      b.runs - a.runs || compare(a.harness, b.harness) || compare(a.provider, b.provider));
  }

  const compare = (a, b) => a < b ? -1 : a > b ? 1 : 0;
  function sortRows(rows, key, dir) {
    return rows.map((row, i) => ({row, i})).sort((a, b) => {
      const av = a.row[key], bv = b.row[key];
      if (av == null || bv == null) {
        return av == null && bv == null ? a.i - b.i : av == null ? 1 : -1;
      }
      const order = typeof av === "number" && typeof bv === "number" ? compare(av, bv) :
        compare(String(av), String(bv));
      return order * (dir === "desc" ? -1 : 1) || a.i - b.i;
    }).map(x => x.row);
  }

  function parseSort(value, keys, fallback) {
    const [key, dir] = (value || "").split(":");
    return keys.includes(key) && (dir === "asc" || dir === "desc") &&
      value === key + ":" + dir ? {key, dir} : fallback;
  }

  function chipPresentation(chip, selected) {
    const live = chip.state === "live";
    return {text: chip.label + (chip.state === "absent" ? " ✕" : ""),
      className: "chip" + (live ? "" : " chip--" + chip.state), disabled: !live,
      pressed: live && selected, reason: live ? null : reasonText(chip)};
  }

  function availabilityPresentation(measure, vocabulary) {
    const entry = vocabulary.find(v => v.state === measure.state);
    if (!entry) throw new Error("unknown availability state: " + measure.state);
    return {text: measure.state === "measured" ? formatCount(measure.value) :
      measure.state === "measured_zero" ? "0" : entry.label,
      className: "av av--" + measure.state, title: entry.description};
  }

  function barLayout(items, {width, labelWidth, valueWidth, rowHeight, gap}) {
    const plot = width - labelWidth - valueWidth;
    const max = items.reduce((max, item) => item.value == null ? max : Math.max(max, item.value), 0);
    return items.map((item, i) => ({label: item.label, value: item.value, href: item.href,
      x: labelWidth, y: i * (rowHeight + gap), w: item.value == null ? null :
        max > 0 ? item.value / max * plot : 0, h: rowHeight}));
  }

  return Object.freeze({decodeTable, encodeRoute, decodeRoute, hrefFor, effectiveSelection,
    toggleSelection, windowSelection, selectPrepared, filterRows, populationSentence,
    summarizePairs, sortRows, parseSort, chipPresentation, availabilityPresentation,
    barLayout, formatCount, formatRatio, ratioSample, reviewEligibility, reviewSignal,
    measureCell, ratioCell, definitionCard, toolMix, roleHarnessRows, formatDate,
    CHIP_STATES, DAY_MS});
})();
