# ADR-0017: Hand-written SVG renders the observability deck

**Status:** Accepted  
**Date:** 2026-10-04  
**Issue:** [#4650](https://github.com/TalonT-Org/AutoSkillit/issues/4650)

## Context

The observability deck must open as one self-contained file, work without a network
connection, and print its charts as vector graphics. Research recommended Apache
ECharts for charting, so the prototype's “Where do the tokens go?” view was built both
with ECharts and with first-party SVG using the same real data.

## Decision

Render charts with hand-written SVG. Include no third-party chart library in the deck.

## Rationale

Both prototype files were checked in offline headless Chromium and printed to PDF. The
PDF content streams were inspected for vector and raster content.

| | Hand-written SVG | Apache ECharts 6.1.0, SVG renderer |
|---|---:|---:|
| Total HTML | 13,283 B | 532,044 B |
| Chart-drawing code | 44 lines | 43 lines |
| Toolchain | None | npm and `npx esbuild` |
| Light PDF | 60,760 B; vector, 0 raster | 588,351 B; vector, 0 raster |
| Theme colors | Standard CSS variables | CSS variable passthrough is undocumented; default theme hardcodes hex |
| SVG accessibility | ARIA and a named list can be authored | `AriaComponent` emitted no ARIA attributes |

ECharts contributed 518,096 B (97.4%) of its HTML. Its often-cited 177,738 B figure is
the gzip size; a `file://` page receives no transport compression. ECharts also embedded
duplicated font subsets in the PDF. The hand-written version needs no build tool and
keeps colors in the deck's CSS palettes.

The alternatives survey found ApexCharts under a proprietary revenue-gated license;
Chart.js and uPlot use canvas and therefore do not meet the vector-print requirement.
Frappe Charts supports SVG under MIT, but had no repository push in the preceding 15
months at the time of the survey.

## Re-evaluation trigger

Re-run the build comparison if a view needs zoom, brushing, or linked hover across charts
shown at once. Compare both approaches against that concrete interaction need.

## Enforcement

Deck tests enforce the asset closure, prohibit network access, and check the content
security policy. The deck assets must not acquire a third-party chart library.
