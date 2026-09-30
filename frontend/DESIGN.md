# DESIGN.md — Grodno guide UI (Airbnb-like)

The UI is Tailwind v4 + shadcn/ui (`src/components/ui/*`, style `new-york`). We do NOT fork the
shadcn primitives: **all** of the look comes from the design tokens in `src/index.css` plus Tailwind
utility classes used in our own components. Keep that rule.

Token variables are already wired: `--background --foreground --card --popover --primary --secondary
--muted --accent --destructive --border --input --ring --radius` (+ `--sidebar-*`). Change the VALUES
in `src/index.css`; components keep using `bg-card`, `text-muted-foreground`, `rounded-xl`, etc.

## Direction: warm, light, generous — "Airbnb", not "developer console"

- White surfaces, warm neutral greys (never blue-grey), one confident accent.
- Big radii, 1px hairline borders, soft layered shadows, lots of breathing room.
- Cards are white and float; the map is the hero, the panel is a clean sheet on top of it.

## Tokens (`src/index.css`, `:root`)

| Token                    | Value     | Notes                                                |
| ------------------------ | --------- | ---------------------------------------------------- |
| `--background`           | `#ffffff` | page / panel                                         |
| `--foreground`           | `#222222` | primary text                                         |
| `--card`                 | `#ffffff` | cards                                                |
| `--muted`                | `#f7f7f7` | quiet fills, chip tracks, hover rows                 |
| `--muted-foreground`     | `#717171` | secondary text, labels                               |
| `--primary`              | `#ff385c` | the accent (buttons, active states, route line)      |
| `--primary-foreground`   | `#ffffff` | text on accent                                       |
| `--secondary`            | `#f7f7f7` | secondary buttons                                    |
| `--secondary-foreground` | `#222222` |                                                      |
| `--border`               | `#ebebeb` | hairlines                                            |
| `--input`                | `#ebebeb` |                                                      |
| `--ring`                 | `#ff385c` | focus ring                                           |
| `--radius`               | `1rem`    | 16px base; `rounded-xl` → 20px, `rounded-2xl` → 24px |

Shadows (Tailwind's scale is fine; these are the intended ones):

- card at rest: `shadow-[0_1px_2px_rgba(0,0,0,0.06)]` + `border border-border`
- floating / popup: `shadow-[0_8px_28px_rgba(0,0,0,0.12)]`
- bottom sheet: `shadow-[0_-8px_28px_rgba(0,0,0,0.12)]`

## Type

One size per role, exposed as `--text-*` theme tokens in `src/index.css`. Use the class, never an
arbitrary value (`text-[13px]`) and never a Tailwind default (`text-sm`, `text-xs`): those were the
main source of drift, and the audit found nine competing sizes across the panel.

| Class        | Size | Role                                                           |
| ------------ | ---- | -------------------------------------------------------------- |
| `text-badge` | 11px | counters, numeric badges. **Floor — nothing renders smaller.** |
| `text-meta`  | 12px | meta lines, section labels, helper copy                        |
| `text-label` | 13px | card titles, chips, control labels                             |
| `text-body`  | 15px | body copy, inputs, buttons (the base size)                     |
| `text-title` | 16px | panel/section titles                                           |
| `text-stat`  | 19px | numbers that matter (km, minutes, stop count) in stat tiles    |

All sizes carry `leading-[1.45]` except `text-title` (1.35) and `text-stat` (1.2). Body keeps
`tracking-[-0.01em]`. Weights are unchanged: titles `font-semibold`, chips `font-medium`.

## Radius and elevation

Four radius steps, no others:

| Class          | Use                                                         |
| -------------- | ----------------------------------------------------------- |
| `rounded-full` | chips, pills, icon buttons, progress bars, avatars          |
| `rounded-lg`   | small inner surfaces (toggles, swatches, inline code)       |
| `rounded-xl`   | primary buttons, list rows, inputs                          |
| `rounded-2xl`  | cards, panels, popups; `rounded-t-3xl` for the mobile sheet |

Shadows are only the three tokens — `shadow-card`, `shadow-float`, `shadow-sheet`. An arbitrary
`shadow-[...]` in a component is a review failure; if a new elevation is genuinely needed, add a
token to `:root` first.

## Components (patterns to reuse, not reinvite)

- **Primary button**: `h-12 rounded-xl bg-primary text-primary-foreground font-semibold
hover:brightness-[0.97] active:scale-[0.99] transition`, disabled `opacity-40`.
- **Secondary / icon button**: `h-9 w-9 rounded-full hover:bg-muted transition` with a lucide icon.
- **Chip**: `h-8 rounded-full border border-border px-3 text-[13px] hover:bg-muted`, selected =
  `border-foreground bg-foreground text-background` (or accent) — used for query hints, time presets,
  category filters.
- **Segmented control** (tabs, transport, mode): grey track `bg-muted rounded-full p-1`, active item
  `bg-card shadow-sm rounded-full text-foreground`, inactive `text-muted-foreground`.
- **Card**: `rounded-2xl border border-border bg-card p-4 shadow-[0_1px_2px_rgba(0,0,0,0.06)]`.
- **Stat tile**: card with a 18–20px number + 12px muted label, three in a row (`grid-cols-3 gap-2`).
- **Stop row** (timeline): 52px tall, `rounded-xl hover:bg-muted`, leading 24px circle with the stop
  number (`bg-muted text-badge font-semibold`), a category icon/emoji, name `text-label`, meta on the
  right; hover reveals icon buttons (remove, pin). Drag handle on the left, `cursor-grab`.
- **Timeline guide line**: 1px `bg-border` between the numbered circles.
- **Empty state**: centred muted icon + one line of what will happen here, plus the hint chips
  (`замки`, `костёлы`, `монастыри`, `где поесть`). Never a bare blank panel. The ask field's
  placeholder is the question itself (`Что хотите посмотреть?`) — no trailing ellipsis, which reads
  as a truncated string.
- **Loading**: skeleton rows (`animate-pulse rounded-xl bg-muted h-12`) — not a spinner in the middle.

## Guide (Проводник): transport and visit time

- **The guide speaks the transport's language.** Turn instructions and the ETA speed already come from
  the route itself (Valhalla costing + route summary), so they follow the plan; the guide's own voice
  must match them. `guideModeFor(costing)` in `parts/guide-mode.ts` is the single source:
  - `pedestrian`, or any costing nobody stated → foot: «идти ~14 мин», footprints, «пройдено»,
    no vehicle note on arrival;
  - `bicycle` → bike: «ехать ~14 мин», bike icon, «проехано»;
  - `auto`/`car`/`truck`/`bus`/motor\* → car: «ехать», car icon, «проехано», plus the arrival line
    «припаркуйтесь у остановки».
    Never hardcode «идти»/«пройдено» in a guide component — read it from the mode object.
- **Visit time is the tourist's number, not ours.** The dataset estimate (`visitMinutes` from the
  taxonomy) is presented as approximate: «≈ 40 мин», and opening it says «обычно здесь оставляют ≈ 40
  мин». One tap gives −/+ in 10-minute steps (5…480) and a reset back to the estimate. Their number is
  stored per route (`utils/visit-time.ts`, localStorage `grodno-guide-visit-minutes`) and every total
  that depends on visit length («осталось осмотра», «с дорогой осталось») is computed from it. With
  neither an estimate nor their number the row shows nothing — never an invented duration.
- Visit time is its own control, never nested inside the row's tap target: a button inside a button is
  invalid HTML and swallows the tap that marks the stop.

## Motion

- 150–200ms, `ease-out`. Appearance: `opacity 0→1` + `translateY(6px)→0`. No bounce, no scale >1.02.
- Route/marker changes: fade+slide, never a layout jump. `tw-animate-css` is available.
- Respect `prefers-reduced-motion: reduce` → no translate/scale, opacity only.

## Layout

- Panel: 360–400px wide on desktop, `bg-background`, `border-r border-border`, internal padding 16px,
  `gap-3` between blocks; a sticky footer holds the main action.
- Mobile (`< 768px`): the panel becomes a **bottom sheet** — `rounded-t-3xl`, drag handle, snap to ~45%
  and ~90% of viewport height, map visible above it, upward shadow. Same content, single column.
- The map must never be covered by the sheet's header: the sheet's collapsed state shows the header row
  (mode switch + query input) and the main CTA.

## Do not

- No new UI libraries (no MUI, no antd, no framer-motion dependency unless already present — use CSS
  transitions + `tw-animate-css`).
- No gradients-for-gradients-sake, no neon, no drop shadows on text.
- Don't restyle `src/components/ui/*` behaviour; adjust via tokens and wrapper classes.
- Keep tests green: `npx vitest run`, `npx tsc -p tsconfig.json --noEmit`, `npx eslint <files>`.

## Implementation notes (tokens pass)

Recorded while implementing the `:root` table above. No change of direction — these are places where
the spec as written could not be applied literally.

1. **`rounded-2xl → 24px` needed a new theme key.** `@theme inline` only overrode `--radius-sm…xl`, so
   `rounded-2xl` kept Tailwind's default `--radius-2xl: 1rem` (16px) and would have collapsed onto the
   base radius. Added `--radius-2xl: calc(var(--radius) + 8px)` to `@theme inline`. Consequence:
   `rounded-2xl` (24px) and `rounded-t-3xl` (24px, Tailwind default) are now the same size, so the
   bottom sheet's corners are no larger than a card's. If that matters, bump `--radius-3xl` too.
2. **`prefers-reduced-motion` is implemented by collapsing durations, not `transform: none`.** A blanket
   `transform: none` under reduced motion also flattens maplibre's marker/overlay positioning, which
   positions elements with transforms. So the rule zeroes animation/transition durations and drops the
   translate from the `.animate-appear` keyframes; opacity still animates.
3. **`.maplibregl-popup-content` needs `!important`.** `maplibre-gl.css` ships its own
   `.maplibregl-popup-content` later in the bundle, so the floating shadow and the card background
   have to be `!important` to win the cascade.
4. **Tokens not in the table above still had to be filled in**, because `@theme inline` references
   them by name: `--card-foreground`, `--popover*`, `--accent*`, `--destructive`, `--sidebar-*` and
   `--chart-1…5`. The neutral/surface ones follow the table; `--destructive` is `#c13515` (distinct
   from the accent, so an error never reads as "active"). `--chart-1…5` were left at their existing
   values — they are data-viz colours, outside this direction.
5. **The three shadows are exposed as tokens + utilities** so there is one source of truth:
   `--shadow-card` / `--shadow-float` / `--shadow-sheet` in `:root` (and `.dark`), and the
   `.shadow-card` / `.shadow-float` / `.shadow-sheet` utilities in `@layer utilities`.
