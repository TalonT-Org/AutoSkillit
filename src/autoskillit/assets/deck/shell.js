globalThis.DeckShell = (() => {
  "use strict";

  const renderers = new Map();

  function el(tag, attrs = {}, children = null) {
    const node = document.createElement(tag);
    for (const [name, value] of Object.entries(attrs)) {
      if (value === null || value === undefined || value === false) continue;
      node.setAttribute(name, value === true ? "" : String(value));
    }
    if (Array.isArray(children)) {
      for (const child of children) {
        if (child !== null && child !== undefined) node.appendChild(child);
      }
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
      const NS = "http://www.w3.org/2000/svg";
      const labelWidth = 200, valueWidth = 80, rowHeight = 18, gap = 6;
      const layout = DeckCore.barLayout(items, {width, labelWidth, valueWidth, rowHeight, gap});
      const height = layout.length ? layout[layout.length - 1].y + rowHeight : rowHeight;
      const svg = document.createElementNS(NS, "svg");
      svg.setAttribute("role", "list");
      svg.setAttribute("aria-label", label);
      svg.setAttribute("viewBox", "0 0 " + width + " " + height);

      layout.forEach(bar => {
        const group = document.createElementNS(NS, "g");
        group.setAttribute("role", "listitem");

        const labelText = document.createElementNS(NS, "text");
        labelText.setAttribute("class", "bar-label");
        labelText.setAttribute("x", "0");
        labelText.setAttribute("y", String(bar.y + 13));
        labelText.textContent = bar.label;
        if (bar.href) {
          const link = document.createElementNS(NS, "a");
          link.setAttribute("href", bar.href);
          link.appendChild(labelText);
          group.appendChild(link);
        } else {
          group.appendChild(labelText);
        }

        const rect = document.createElementNS(NS, "rect");
        rect.setAttribute("class", bar.w === null ? "bar bar--absent" : "bar");
        rect.setAttribute("aria-hidden", "true");
        rect.setAttribute("x", String(bar.x));
        rect.setAttribute("y", String(bar.y));
        rect.setAttribute("width", String(bar.w === null ? width - labelWidth - valueWidth : bar.w));
        rect.setAttribute("height", String(bar.h));
        group.appendChild(rect);

        const valueText = document.createElementNS(NS, "text");
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

    function renderCohort(chips, result, sentence) {
      const cohort = document.getElementById("deck-cohort");
      cohort.replaceChildren();
      cohort.appendChild(el("div", {class: "cohort-sentence"}, [
        el("b", {}, sentence.headline), el("span", {}, sentence.detail)
      ]));
      cohort.appendChild(el("ul", {class: "cohort-notes"}, sentence.notes.map(note =>
        el("li", {}, note))));

      for (const facet of model.facets) {
        const facetChips = facet.kind === "window" ? chips.window : chips[facet.id];
        const selectedKeys = facet.kind === "window" ?
          [DeckCore.windowSelection(chips.window, route.params.window ?? null).chip.key] :
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

      const resetParams = Object.fromEntries(cohortKeys.map(key => [key, null]));
      cohort.appendChild(el("a", {
        class: "cohort-reset",
        href: DeckCore.hrefFor(route, {
          view: route.view, entity: route.entity, params: resetParams
        }, cohortKeys)
      }, "Reset cohort"));
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

      const table = model.tables.sessions;
      const result = DeckCore.filterRows(model, table, chips, route);
      const sentence = DeckCore.populationSentence(model, chips, route, result);
      renderCohort(chips, result, sentence);

      const content = document.getElementById("deck-view");
      content.replaceChildren();
      if (renderer) {
        const ctx = {model, route, rows: result.rows, chips,
          href: target => DeckCore.hrefFor(route, target, cohortKeys),
          el, entityLink, sortableTable, availabilityCell, barChart};
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

      const first = model.history.first_ms;
      const last = model.history.last_ms;
      const history = first === null || last === null ? "—" :
        DeckCore.formatDate(first) + " → " + DeckCore.formatDate(last);
      const generated = new Date(model.generated_at_ms).toISOString().slice(0, 16);
      document.getElementById("deck-foot").textContent = "report index v" +
        model.index_schema_version + " · " + DeckCore.formatCount(table.length) +
        " session rows · history " + history + " · generated " + generated + " UTC";
    }

    window.addEventListener("hashchange", render);
    render();
  }

  return Object.freeze({registerView, registeredViews, boot});
})();

if (typeof document !== "undefined")
  document.addEventListener("DOMContentLoaded", () =>
    DeckShell.boot(JSON.parse(document.getElementById("deck-data").textContent)));
