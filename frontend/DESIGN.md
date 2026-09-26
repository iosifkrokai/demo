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

| Token | Value | Notes |
|---|---|---|
| `--background` | `#ffffff` | page / panel |
| `--foreground` | `#222222` | primary text |
| `--card` | `#ffffff` | cards |
| `--muted` | `#f7f7f7` | quiet fills, chip tracks, hover rows |
| `--muted-foreground` | `#717171` | secondary text, labels |
| `--primary` | `#ff385c` | the accent (buttons, active states, route line) |
| `--primary-foreground` | `#ffffff` | text on accent |
| `--secondary` | `#f7f7f7` | secondary buttons |
| `--secondary-foreground` | `#222222` | |
| `--border` | `#ebebeb` | hairlines |
| `--input` | `#ebebeb` | |
| `--ring` | `#ff385c` | focus ring |
| `--radius` | `1rem` | 16px base; `rounded-xl` → 20px, `rounded-2xl` → 24px |

Shadows (Tailwind's scale is fine; these are the intended ones):

- card at rest: `shadow-[0_1px_2px_rgba(0,0,0,0.06)]` + `border border-border`
- floating / popup: `shadow-[0_8px_28px_rgba(0,0,0,0.12)]`
- bottom sheet: `shadow-[0_-8px_28px_rgba(0,0,0,0.12)]`

## Type

- Base 15px, `leading-[1.45]`, `tracking-[-0.01em]`.
- Section title 15–16px `font-semibold`; card title 13px `font-semibold`.
- Meta/labels 12–13px `text-muted-foreground`. Never smaller than 11px.
- Numbers that matter (km, minutes, stop count) are 18–20px `font-semibold` and sit in stat tiles.

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
  number (`bg-muted text-[11px] font-semibold`), a category icon/emoji, name `text-[14px]`, meta on the
  right; hover reveals icon buttons (remove, pin). Drag handle on the left, `cursor-grab`.
- **Timeline guide line**: 1px `bg-border` between the numbered circles.
- **Empty state**: centred muted icon + one line of what to type (`Что хотите посмотреть? …`), plus
  3 hint chips. Never a bare blank panel.
- **Loading**: skeleton rows (`animate-pulse rounded-xl bg-muted h-12`) — not a spinner in the middle.

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
