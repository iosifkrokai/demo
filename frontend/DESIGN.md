# DESIGN.md — Grodno guide UI (Airbnb-like)

The UI is Tailwind v4 + shadcn/ui (`src/components/ui/*`, style `new-york`). We do
NOT fork the shadcn primitives: **all** of the look comes from the design tokens in
`src/index.css` plus Tailwind utility classes in our own components. Keep that
rule. Direction: warm, light, generous — "Airbnb", not "developer console": white
surfaces, warm neutral greys (never blue-grey), one confident accent, big radii,
1px hairline borders, soft layered shadows, lots of breathing room.

Token variables are already wired: `--background --foreground --card --popover
--primary --secondary --muted --accent --destructive --border --input --ring
--radius` (+ `--sidebar-*`). Change the VALUES in `src/index.css`; components keep
using `bg-card`, `text-muted-foreground`, `rounded-xl`, etc.

## Tokens (`src/index.css`, `:root`)

| Token                    | Value     | Notes                                                |
| ------------------------ | --------- | ---------------------------------------------------- |
| `--background`           | `#ffffff` | page / panel                                         |
| `--foreground`           | `#222222` | primary text                                         |
| `--card`                 | `#ffffff` | cards                                                |
| `--muted`                | `#f7f7f7` | quiet fills, chip tracks, hover rows                 |
| `--muted-foreground`     | `#717171` | secondary text, labels                               |
| `--primary`              | `#d92d4f` | the accent (buttons, active states, route line)      |
| `--primary-foreground`   | `#ffffff` | text on accent                                       |
| `--secondary`            | `#f7f7f7` | secondary buttons                                    |
| `--secondary-foreground` | `#222222` |                                                      |
| `--border`               | `#ebebeb` | hairlines                                            |
| `--input`                | `#ebebeb` |                                                      |
| `--ring`                 | `#ff385c` | focus ring                                           |
| `--radius`               | `1rem`    | 16px base; `rounded-xl` → 20px, `rounded-2xl` → 24px |

The accent was darkened from the brand `#ff385c` (3.52:1 on white) to `#d92d4f`
(4.73:1) so white text on it passes WCAG AA; the hue is unchanged. Shadows are
tokens too — `--shadow-card`, `--shadow-float`, `--shadow-sheet`, with matching
`.shadow-*` utilities. An arbitrary `shadow-[...]` in a component is a review
failure: if a new elevation is genuinely needed, add a token to `:root` first.

## Type

One size per role, exposed as `--text-*` theme tokens in `src/index.css`. Use the
class, never an arbitrary value (`text-[13px]`) and never a Tailwind default
(`text-sm`, `text-xs`): those were the main source of drift, and the audit found
nine competing sizes across the panel.

| Class        | Size | Role                                                           |
| ------------ | ---- | -------------------------------------------------------------- |
| `text-badge` | 11px | counters, numeric badges. **Floor — nothing renders smaller.** |
| `text-meta`  | 12px | meta lines, section labels, helper copy                        |
| `text-label` | 13px | card titles, chips, control labels                             |
| `text-body`  | 15px | body copy, inputs, buttons (the base size)                     |
| `text-title` | 16px | panel/section titles                                           |
| `text-stat`  | 19px | numbers that matter (km, minutes, stop count) in stat tiles    |

All sizes carry `leading-[1.45]` except `text-title` (1.35) and `text-stat` (1.2).
Body keeps `tracking-[-0.01em]`. Weights: titles `font-semibold`, chips `font-medium`.

## Radius and elevation

Four radius steps, no others. `rounded-full` for chips, pills, icon buttons,
progress bars, avatars; `rounded-lg` for small inner surfaces (toggles, swatches,
inline code); `rounded-xl` for primary buttons, list rows, inputs; `rounded-2xl`
for cards, panels, popups, and `rounded-t-3xl` for the mobile sheet.

## Components (patterns to reuse, not reinvite)

- **Primary button**: `h-12 rounded-xl bg-primary text-primary-foreground
  font-semibold hover:brightness-[0.97] active:scale-[0.99] transition`, disabled `opacity-40`.
- **Secondary / icon button**: `h-9 w-9 rounded-full hover:bg-muted transition` with a lucide icon.
- **Chip**: `h-8 rounded-full border border-border px-3 text-[13px] hover:bg-muted`,
  selected = `border-foreground bg-foreground text-background` (or accent) — for
  query hints, time presets, category filters.
- **Segmented control** (tabs, transport, mode): grey track `bg-muted rounded-full
  p-1`, active item `bg-card shadow-sm rounded-full text-foreground`, inactive `text-muted-foreground`.
- **Card**: `rounded-2xl border border-border bg-card p-4 shadow-card`.
- **Stat tile**: card with an 18–20px number + 12px muted label, three in a row (`grid-cols-3 gap-2`).
- **Stop row** (timeline): 52px tall, `rounded-xl hover:bg-muted`, leading 24px
  circle with the stop number (`bg-muted text-badge font-semibold`), a category
  icon, name `text-label`, meta on the right; hover reveals icon buttons (remove,
  pin). Drag handle on the left, `cursor-grab`; 1px `bg-border` guide between the circles.
- **Empty state**: centred muted icon + one line of what will happen here, plus the
  hint chips (`замки`, `костёлы`, `монастыри`, `где поесть`). Never a bare blank
  panel. The ask field's placeholder is the question itself
  (`Что хотите посмотреть?`) — no trailing ellipsis.
- **Loading**: skeleton rows (`animate-pulse rounded-xl bg-muted h-12`) — not a spinner in the middle.
- **Layout**: panel 360–400px on desktop (`bg-background`, `border-r border-border`,
  16px padding, `gap-3`, sticky footer). Below 768px it becomes a bottom sheet —
  `rounded-t-3xl`, drag handle, snap to ~45% and ~90% of the viewport, map visible
  above it; the collapsed state keeps the header row and the main CTA, so the map is never covered.

## Motion

- 150–200ms, `ease-out`. Appearance: `opacity 0→1` + `translateY(6px)→0`. No bounce, no scale >1.02.
- Route/marker changes: fade+slide, never a layout jump. `tw-animate-css` is available.
- Respect `prefers-reduced-motion: reduce` → no translate/scale, opacity only.

## Do not

- No new UI libraries (no MUI, no antd, no framer-motion unless already present —
  use CSS transitions + `tw-animate-css`).
- No gradients-for-gradients-sake, no neon, no drop shadows on text.
- Don't restyle `src/components/ui/*` behaviour; adjust via tokens and wrapper classes.
- Keep tests green: `npx vitest run`, `npx tsc -p tsconfig.json --noEmit`, `npx eslint <files>`.
