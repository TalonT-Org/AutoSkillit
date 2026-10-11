globalThis.DeckShell = (() => {
  "use strict";

  const renderers = new Map();
  const SVG_NS = "http://www.w3.org/2000/svg";

  function appendChild(parent, child) {
    if (child === null || child === undefined) return;
    if (typeof child === "string" || typeof child === "number" || typeof child === "boolean") {
      parent.appendChild(document.createTextNode(String(child)));
      return;
    }
    parent.appendChild(child);
  }

  function el(tag, attrs = {}, children = null) {
    const node = document.createElement(tag);
    for (const [name, value] of Object.entries(attrs)) {
      if (value === null || value === undefined || value === false) continue;
      node.setAttribute(name, value === true ? "" : String(value));
    }
    if (Array.isArray(children)) {
      for (const child of children) appendChild(node, child);
    } else if (children !== null && children !== undefined) {
      if (typeof children === "object") node.appendChild(children);
      else node.textContent = String(children);
    }
    return node;
  }

  function registerView(id, render) {
    if (renderers.has(id)) throw new Error("duplicate deck view: " + id);
    renderers.set(id, render);
  }

  function registeredViews() {
    return Array.from(renderers.keys());
  }

  function svgElement(tag, attrs = {}, children = null) {
    const node = document.createElementNS(SVG_NS, tag);
    for (const [name, value] of Object.entries(attrs)) {
      if (value === null || value === undefined || value === false) continue;
      node.setAttribute(name, value === true ? "" : String(value));
    }
    if (Array.isArray(children)) {
      for (const child of children) appendChild(node, child);
    } else if (children !== null && children !== undefined) {
      if (typeof children === "object") node.appendChild(children);
      else node.textContent = String(children);
    }
    return node;
  }

  function boot(payload) {
    const model = {...payload, tables: Object.fromEntries(
      Object.entries(payload.tables).map(([key, table]) => [key, DeckCore.decodeTable(table)]))};
    const cohortKeys = payload.facets.map(facet => facet.id);
    let route;

    function entityLink(text, target) {
      return el("a", {href: DeckCore.hrefFor(route, target, cohortKeys)}, text);
    }

    function sortableTable({columns, rows, defaultSort}) {
      const keys = columns.map(column => column.key);
      const sort = DeckCore.parseSort(route.params.sort?.[0], keys, defaultSort);
      const head = el("thead", {}, el("tr", {}, columns.map(column => {
        const direction = column.key === sort.key ? sort.dir : "none";
        const th = el("th", {
          "aria-sort": direction === "asc" ? "ascending" :
            direction === "desc" ? "descending" : "none",
          class: column.numeric ? "numeric" : null
        });
        const button = el("button", {type: "button"}, column.label);
        button.addEventListener("click", () => {
          const params = {...route.params};
          params.sort = [column.key + ":" +
            (column.key === sort.key && sort.dir === "asc" ? "desc" : "asc")];
          location.hash = DeckCore.encodeRoute({...route, params}, cohortKeys);
        });
        th.appendChild(button);
        return th;
      })));
      const body = el("tbody", {}, DeckCore.sortRows(rows, sort.key, sort.dir).map(row =>
        el("tr", {}, columns.map(column => {
          const value = column.cell ? column.cell(row) : row[column.key];
          return el("td", {class: column.numeric ? "numeric" : null},
            value === null || value === undefined ? "—" : value);
        }))));
      return el("table", {}, [head, body]);
    }

    function availabilityCell(measure) {
      const presentation = DeckCore.availabilityPresentation(measure, model.availability);
      return el("span", {class: presentation.className, title: presentation.title},
        presentation.text);
    }

    function barChart(items, {label, width = 640}) {
      const labelWidth = 200, valueWidth = 80, rowHeight = 18, gap = 6;
      const layout = DeckCore.barLayout(items, {width, labelWidth, valueWidth, rowHeight, gap});
      const height = layout.length ? layout[layout.length - 1].y + rowHeight : rowHeight;
      const svg = document.createElementNS(SVG_NS, "svg");
      svg.setAttribute("role", "list");
      svg.setAttribute("aria-label", label);
      svg.setAttribute("viewBox", "0 0 " + width + " " + height);

      layout.forEach(bar => {
        const group = document.createElementNS(SVG_NS, "g");
        group.setAttribute("role", "listitem");

        const labelText = document.createElementNS(SVG_NS, "text");
        labelText.setAttribute("class", "bar-label");
        labelText.setAttribute("x", "0");
        labelText.setAttribute("y", String(bar.y + 13));
        labelText.textContent = bar.label;
        if (bar.href) {
          const link = document.createElementNS(SVG_NS, "a");
          link.setAttribute("href", bar.href);
          link.appendChild(labelText);
          group.appendChild(link);
        } else {
          group.appendChild(labelText);
        }

        const rect = document.createElementNS(SVG_NS, "rect");
        rect.setAttribute("class", bar.w === null ? "bar bar--absent" : "bar");
        rect.setAttribute("aria-hidden", "true");
        rect.setAttribute("x", String(bar.x));
        rect.setAttribute("y", String(bar.y));
        rect.setAttribute("width", String(bar.w === null ? width - labelWidth - valueWidth : bar.w));
        rect.setAttribute("height", String(bar.h));
        group.appendChild(rect);

        const valueText = document.createElementNS(SVG_NS, "text");
        valueText.setAttribute("class", "bar-value");
        valueText.setAttribute("x", String(width - valueWidth + 4));
        valueText.setAttribute("y", String(bar.y + 13));
        valueText.textContent = bar.value == null ? "not reported" : DeckCore.formatCount(bar.value);
        group.appendChild(valueText);
        svg.appendChild(group);
      });
      return svg;
    }

    function renderNav() {
      const nav = document.getElementById("deck-nav");
      nav.replaceChildren();
      const groups = new Map();
      for (const view of model.views) {
        if (!groups.has(view.group)) groups.set(view.group, []);
        groups.get(view.group).push(view);
      }
      for (const [group, views] of groups) {
        nav.appendChild(el("div", {class: "nav-group"}, group));
        for (const view of views) {
          const current = view.id === route.view;
          let item;
          if (view.status === "built") {
            item = el("a", {
              class: "navq",
              "data-view": view.id,
              href: DeckCore.hrefFor(route, {view: view.id}, cohortKeys)
            }, [el("span", {}, view.question), el("small", {}, view.decision)]);
          } else {
            item = el("span", {
              class: "navq navq--planned",
              "data-view": view.id,
              "aria-disabled": "true"
            }, [el("span", {}, view.question),
              el("small", {}, "arrives in #" + view.issue)]);
          }
          if (current) item.setAttribute("aria-current", "true");
          nav.appendChild(item);
        }
      }
    }

    function renderCohort(chips, result, sentence, facets = model.facets) {
      const cohort = document.getElementById("deck-cohort");
      cohort.replaceChildren();
      cohort.appendChild(el("div", {class: "cohort-sentence"}, [
        el("b", {}, sentence.headline), el("span", {}, sentence.detail)
      ]));
      cohort.appendChild(el("ul", {class: "cohort-notes"}, sentence.notes.map(note =>
        el("li", {}, note))));

      for (const facet of facets) {
        const facetChips = facet.kind === "window" ? chips.window : chips[facet.id];
        const window = facet.kind === "window" ?
          DeckCore.windowSelection(chips.window, route.params.window ?? null).chip : null;
        const selectedKeys = facet.kind === "window" ? (window ? [window.key] : []) :
          DeckCore.effectiveSelection(chips[facet.id], route.params[facet.id] ?? null).keys;
        const group = el("div", {class: "cohort-group"}, [el("span", {}, facet.label)]);
        const unavailable = [];
        facetChips.forEach((chip, index) => {
          const presentation = DeckCore.chipPresentation(chip, selectedKeys.includes(chip.key));
          if (!presentation.disabled) {
            const button = el("button", {
              type: "button",
              class: presentation.className,
              "aria-pressed": String(presentation.pressed),
              "data-facet": facet.id,
              "data-key": chip.key
            }, presentation.text);
            button.addEventListener("click", () => {
              const params = {...route.params};
              if (facet.kind === "window") {
                params.window = chip.key === "all" ? null : [chip.key];
              } else {
                params[facet.id] = DeckCore.toggleSelection(chips[facet.id],
                  route.params[facet.id] ?? null, chip.key);
              }
              location.hash = DeckCore.encodeRoute({...route, params}, cohortKeys);
            });
            group.appendChild(button);
          } else {
            const reasonId = "deck-reason-" + facet.id + "-" + index;
            group.appendChild(el("button", {
              type: "button",
              class: presentation.className,
              disabled: true,
              "aria-describedby": reasonId
            }, presentation.text));
            unavailable.push(el("li", {id: reasonId}, chip.label + " — " + presentation.reason));
          }
        });
        cohort.appendChild(group);
        if (unavailable.length) {
          const reasons = el("ul", {class: "cohort-reasons"}, unavailable);
          cohort.appendChild(reasons);
        }
      }

      const resetParams = Object.fromEntries([...cohortKeys, "skill", "recipe", "step", "model"]
        .map(key => [key, null]));
      cohort.appendChild(el("a", {
        class: "cohort-reset",
        href: DeckCore.hrefFor(route, {
          view: route.view, entity: route.entity, params: resetParams
        }, cohortKeys)
      }, "Reset cohort"));
    }

    function renderAnalysisCard(analysis, viewId) {
      const display = value => value == null || value === "" ? "not recorded" :
        typeof value === "object" ? JSON.stringify(value) : String(value);
      const interpreter = analysis.interpreter ?? {};
      const identity = el("dl", {class: "run-analysis__identity"}, [
        el("dt", {}, "Input fingerprint"), el("dd", {}, display(analysis.input_fingerprint)),
        el("dt", {}, "Interpreter role"), el("dd", {}, display(interpreter.role)),
        el("dt", {}, "Model"), el("dd", {}, display(interpreter.model)),
        el("dt", {}, "Prompt"), el("dd", {}, display(interpreter.prompt))
      ]);
      const populations = Object.entries(analysis.coverage?.populations ?? {}).map(
        ([name, coverage]) => {
          const states = Object.entries(coverage.measure_states ?? {}).map(([field, counts]) =>
            field + ": " + Object.entries(counts ?? {}).map(([state, count]) =>
              state + " " + display(count)).join(", "));
          return el("li", {}, name + " · rows " + display(coverage.rows) + " · untimed " +
            display(coverage.untimed_rows) + (states.length ? " · " + states.join("; ") : ""));
        });
      const cohort = analysis.cohort ?? {};
      const hasCohort = cohort.change_id != null && String(cohort.change_id).length > 0;
      const cohortContent = hasCohort ? el("dl", {class: "run-analysis__cohort"}, [
        el("dt", {}, "Change ID"), el("dd", {}, display(cohort.change_id)),
        el("dt", {}, "Effective at"), el("dd", {}, display(cohort.effective_at)),
        el("dt", {}, "Scope"), el("dd", {}, display(cohort.scope)),
        el("dt", {}, "Expected effect"), el("dd", {}, display(cohort.expected_effect)),
        el("dt", {}, "Before / after / excluded"), el("dd", {},
          [cohort.before_count, cohort.after_count, cohort.excluded_count]
            .map(display).join(" / ")),
        el("dt", {}, "Marked pre-cutover baseline"), el("dd", {},
          cohort.marked_pre_cutover_observation ? "yes" : "no"),
        el("dt", {}, "Preregistration"), el("dd", {}, display(cohort.preregistration))
      ]) : el("div", {}, [
        el("p", {}, "No rollout cohort applied."),
        el("p", {}, display(cohort.reason)),
        el("p", {}, "Baseline and preregistration assessment is unavailable " +
          "without a rollout cohort.")
      ]);

      function evidenceMeasure(label, measure, fallbackUnit) {
        if (!measure || typeof measure !== "object") {
          return el("p", {}, label + " · " + display(measure));
        }
        const value = measure.value == null ? "value unavailable" : display(measure.value);
        const unit = measure.unit ?? fallbackUnit ?? "unit not reported";
        const observed = measure.observed_sessions ?? measure.sample_size;
        const eligible = measure.eligible_sessions ?? measure.eligible_count;
        const unknown = measure.unknown_sessions ?? measure.unknown_runs ??
          measure.state_counts?.unknown;
        const sampleText = measure.earlier_samples != null || measure.later_samples != null ?
          "Observed before/after samples " + display(measure.earlier_samples) + "/" +
            display(measure.later_samples) :
          "Observed samples " + display(observed);
        const states = Object.entries(measure.state_counts ?? {}).map(([state, count]) =>
          state + " " + display(count)).join(", ");
        return el("div", {class: "run-analysis__measure"}, [
          el("p", {}, [el("strong", {}, label), " · ", availabilityCell(measure), " · ", value,
            " ", unit, measure.reason ? " · " + measure.reason : ""]),
          el("p", {}, sampleText + " · eligible " + display(eligible) + " · unknown " +
            display(unknown)),
          states ? el("p", {}, "Availability counts · " + states) : null,
          measure.guardrail ? el("p", {}, "Guardrail · " + measure.guardrail) : null
        ]);
      }

      function evidenceSummary(measurement) {
        if (!measurement || typeof measurement !== "object") {
          return el("p", {}, display(measurement));
        }
        if (measurement.before && measurement.after && measurement.delta && measurement.metric) {
          return el("div", {class: "run-analysis__comparison"}, [
            el("p", {}, "Measure · " + display(measurement.metric)),
            Array.isArray(measurement.group) ? el("p", {}, "Group · " +
              measurement.group.map(display).join(" · ")) : null,
            evidenceMeasure("Before", measurement.before, measurement.delta.unit),
            evidenceMeasure("After", measurement.after, measurement.delta.unit),
            evidenceMeasure("Delta", measurement.delta, measurement.delta.unit)
          ]);
        }
        if ("state" in measurement || "value" in measurement) {
          return evidenceMeasure("Measurement", measurement, measurement.unit);
        }
        return el("dl", {class: "run-analysis__fields"}, Object.entries(measurement).flatMap(
          ([key, value]) => [el("dt", {}, key), el("dd", {}, display(value))]
        ));
      }

      const children = [
        el("h2", {}, "Run analysis"),
        identity,
        el("h3", {}, "Frozen coverage"),
        el("p", {}, display(analysis.coverage?.limitation)),
        el("p", {}, "This analysis covers the frozen retained report-index population. " +
          "Current chart filters do not change its evidence."),
        populations.length ? el("ul", {class: "run-analysis__populations"}, populations) :
          el("p", {}, "No retained indexed populations."),
        el("h3", {}, "Rollout marker"),
        cohortContent
      ];

      if (viewId !== null) {
        const findings = (analysis.findings ?? []).filter(finding => finding.view_id === viewId);
        children.push(el("h3", {}, "Interpretation"));
        children.push(el("p", {}, "Findings below apply to this built view and the frozen " +
          "population shown above; interactive filters do not alter them."));
        if (!findings.length) {
          children.push(el("p", {class: "view-empty"},
            "No interpretation was recorded for this view."));
        }
        findings.forEach(finding => {
          const evidenceItems = (finding.evidence_ids ?? []).map(id => {
            const evidence = analysis.evidence?.[id];
            if (!evidence) return el("li", {}, "Missing frozen evidence: " + id);
            return el("li", {}, [
              evidenceSummary(evidence.measurement),
              el("details", {class: "run-analysis__evidence"}, [
                el("summary", {}, id + " · " + display(evidence.source)),
                el("pre", {}, JSON.stringify(evidence.measurement, null, 2))
              ])
            ]);
          });
          children.push(el("div", {class: "run-analysis__finding"}, [
            el("p", {}, finding.text),
            evidenceItems.length ? el("ul", {}, evidenceItems) :
              el("p", {}, "No evidence references in this finding.")
          ]));
        });
      }
      return el("section", {class: "card run-analysis"}, children);
    }

    function render() {
      route = DeckCore.decodeRoute(location.hash, model.landing);
      renderNav();
      const view = model.views.find(item => item.id === route.view);
      let chips, renderer = null, notice = null;
      if (view && view.status === "built") {
        renderer = renderers.get(view.id);
        if (!renderer) throw new Error("built deck view is not registered: " + view.id);
        chips = model.chips[view.id];
      } else if (view) {
        chips = model.chips[model.landing];
        notice = view.question + " arrives in #" + view.issue;
      } else {
        chips = model.chips[model.landing];
        notice = "No view named " + route.view;
      }

      const selected = renderer && view.id !== model.landing ?
        DeckCore.selectPrepared(model.prepared, view.id, chips, route) : null;
      const primary = selected?.rows;
      const result = selected ? {rows: primary, untimed: 0} :
        DeckCore.filterRows(model, model.tables.sessions, chips, route);
      const populationModel = selected ? {...model, history: selected.viewHistory ?? model.history,
        facets: model.facets.map(facet => view.id === "role" && facet.id === "level" ?
          {...facet, label: "Spawning skill level"} : facet)} : model;
      const sentence = DeckCore.populationSentence(populationModel, chips, route, result);
      if (selected) {
        const labels = {skill: "skill", recipe: "recipe", step: "step", model: "model"};
        const identityFilters = Object.entries(labels).flatMap(([key, label]) =>
          (route.params[key] ?? []).map(value => label + " " + value));
        if (selected.selection.session != null) {
          identityFilters.push("session " + selected.selection.session);
        }
        if (identityFilters.length) sentence.detail += " · " + identityFilters.join(" · ");
        const noun = ({context: "context record", errors: "symptom group", trend: "daily observation",
          gaps: "gap", parity: "coverage cell"})[view.id] ??
          (view.id === "role" ? "role/provider row" : "skill cell");
        sentence.headline = DeckCore.formatCount(result.rows.length) + " " + noun +
          (result.rows.length === 1 ? "" : "s");
      }
      renderCohort(chips, result, sentence, populationModel.facets);

      const content = document.getElementById("deck-view");
      content.replaceChildren();
      if (renderer) {
        const ctx = {model, route, rows: result.rows, chips,
          href: target => DeckCore.hrefFor(route, target, cohortKeys),
          el, entityLink, sortableTable, availabilityCell, barChart, svgElement};
        if (view.id !== model.landing) {
          ctx.prepared = model.prepared;
          ctx.metrics = selected.metrics;
          ctx.relationships = selected.relationships;
          ctx.definitions = selected.definitions;
          ctx.viewHistory = selected.viewHistory;
          ctx.selection = selected.selection;
          if (view.id === "spend" || view.id === "efficiency") {
            ctx.skillMetrics = selected.skillRows;
            ctx.roleMetrics = selected.roleRows;
          }
        }
        content.appendChild(renderer(ctx));
      } else {
        const noticeCard = el("section", {class: "card"}, [el("p", {}, notice)]);
        if (!view) {
          noticeCard.appendChild(el("a", {
            href: DeckCore.hrefFor(route, {view: model.landing}, cohortKeys)
          }, "Return to the cohort view"));
        }
        content.appendChild(noticeCard);
      }
      if (model.analysis) {
        content.appendChild(renderAnalysisCard(
          model.analysis, view && view.status === "built" ? view.id : null
        ));
      }

      const first = model.history.first_ms;
      const last = model.history.last_ms;
      const history = first === null || last === null ? "—" :
        DeckCore.formatDate(first) + " → " + DeckCore.formatDate(last);
      const footer = document.getElementById("deck-foot");
      const footerPrefix = "report index v" + model.index_schema_version + " · " +
        DeckCore.formatCount(model.tables.sessions.length) + " session rows · history " + history;
      if (model.analysis) {
        footer.textContent = footerPrefix + " · corpus/window anchor " +
          new Date(model.generated_at_ms).toISOString() + " · analysis created " +
          new Date(model.analysis.generated_at_ms).toISOString();
      } else {
        const generated = new Date(model.generated_at_ms).toISOString().slice(0, 16);
        footer.textContent = footerPrefix + " · generated " + generated + " UTC";
      }
    }

    window.addEventListener("hashchange", render);
    render();
  }

  return Object.freeze({registerView, registeredViews, boot});
})();

if (typeof document !== "undefined")
  document.addEventListener("DOMContentLoaded", () =>
    DeckShell.boot(JSON.parse(document.getElementById("deck-data").textContent)));
