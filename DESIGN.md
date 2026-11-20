# Earnings Intelligence — Design System

## Palette
- Surfaces are neutral (slate). There is **one brand colour**: indigo-600 in light mode, indigo-400 in dark.
- Semantic colours are colour-blind safe. They are **never the only signal**: up/down always carry a sign
  (+/−) and an arrow (▲/▼).

| Token | Light | Dark | Use |
|---|---|---|---|
| `--bg` | #f8fafc | #0b1020 | page |
| `--surface` | #ffffff | #111830 | cards |
| `--surface-2` | #f1f5f9 | #18213d | headers, zebra |
| `--border` | #e2e8f0 | #26304f | dividers |
| `--text` | #0f172a | #e6e9f2 | body |
| `--muted` | #475569 | #9aa4bf | secondary (≥ 4.5:1 on surface) |
| `--brand` | #4f46e5 | #818cf8 | primary actions, focus |
| `--up` | #1d4ed8 (blue) | #60a5fa | positive moves |
| `--down` | #c2410c (orange) | #fb923c | negative moves |
| `--warn` | #854d0e | #facc15 | stale/heuristic |
| `--danger` | #b91c1c | #f87171 | errors |

Blue/orange replaces green/red so the most common forms of colour blindness can still tell up from down.

## Type
- System UI stack (no web-font download).
- Scale: 12 / 13 / 14 (body) / 16 / 20 / 24 / 30.
- **All numbers use `font-variant-numeric: tabular-nums`**, so columns align.
- Headings are 600 weight, body 400. Line length is ≤ 75ch for prose.

## Spacing and density
- 4-px grid. Two density modes: comfortable (default; row 40 px) and compact (row 32 px, 13-px body).
  Toggle in Settings; stored per user.

## Motion
- 150–200 ms ease-out for state changes only.
- No motion carries meaning on its own.
- `prefers-reduced-motion` disables all transitions and the streaming pulse.

## States
| State | Pattern |
|---|---|
| Empty | A one-line explanation, then the primary action (e.g. "Add a ticker to your watchlist") |
| Loading | Skeleton rows that match the final layout. The live run uses a per-agent step list with spinner + text, never a spinner alone |
| Error | The error `message` + `request_id` (copyable) + Retry when `retryable` |
| Stale | A `warn` badge "as of <date>" when data is older than its expected refresh |
| Unavailable | A dashed underline with "n/a" and a tooltip naming what's needed (e.g. "needs FINNHUB_API_KEY") |
| Fixture | A persistent banner: "Demo data — recorded fixtures, not live" |

## Numbers and citations
- Every rendered fact is a focusable button with a dotted underline.
- Hover or focus shows a tooltip: label, value, source, as-of (America/New_York), formula for derived facts.
- Click or Enter opens the Sources panel at that citation.

## Accessibility (WCAG 2.2 AA)
- Contrast ≥ 4.5:1 for text and ≥ 3:1 for UI.
- Visible 2-px focus ring.
- Keyboard: every action is reachable, with skip-to-content, `⌘K` command palette and documented shortcuts (`?`).
- Live run progress is announced through an `aria-live="polite"` region.
- Tables use real `<table>` markup with `scope` headers.
- Target size ≥ 24×24 px. No horizontal scroll at 320–375 px; wide tables scroll inside their own container.

## Performance budgets
- Initial JS ≤ 250 KB gzip (charts are lazy-loaded on the company page).
- LCP ≤ 2.5 s on a mid-tier laptop.
- Interaction responses ≤ 100 ms.
