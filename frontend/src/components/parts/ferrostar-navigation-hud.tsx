import { useCallback, useState } from 'react';
import type { ReactNode } from 'react';
import { createPortal } from 'react-dom';
import {
  BookOpenText,
  ListChecks,
  MapPinCheck,
  Volume2,
  VolumeX,
  XIcon,
} from 'lucide-react';
import { useTranslation } from 'react-i18next';
import type { PlaceDetails } from '@/stores/directions-store';
import { PlaceCardBody } from '@/components/map/parts/place-card-body';
import { GuideProgress } from './guide-progress';
import { GuideRouteDone } from './guide-route-done';
import { GuideStopList } from './guide-stop-list';

interface StopListItem {
  id: string;
  name: string;
  category?: string | null;
  visitMinutes?: number | null;
  visitOverride?: number | null;
  estimateMinutes?: number | null;
}

interface SuggestionItem {
  id: string;
  name: string;
  detail: string;
}

interface FerrostarNavigationHudProps {
  maneuverFallback: ReactNode;
  alerts?: ReactNode;
  announcement: string;
  /** Leaves navigation for good — the guide is done, the planner is back. */
  onExit: () => void;
  onAdvance: () => void;
  onVoiceToggle: () => void;
  onDetailsToggle: () => void;
  voiceMuted: boolean;
  advanceDisabled: boolean;
  detailsToggleLabel: string;
  detailsOpen: boolean;
  totalStops: number;
  progressDone: number;
  progressMinutesLeft: number;
  progressMetresDone?: number | null;
  progressMetresTotal?: number | null;
  progressRemainingMinutes?: number | null;
  stopListItems: StopListItem[];
  stopListVisited: string[];
  stopListNextId: string | null;
  stopListNextDistance: number | null;
  onStopListToggle: (id: string) => void;
  onStopListVisitMinutesChange?: (id: string, minutes: number | null) => void;
  suggestions?: SuggestionItem[];
  onAddSuggestion?: (id: string) => void;
  onSkipSuggestion?: (id: string) => void;
  nextStopName: string | null;
  nextStopId: string | null;
  nextPlaceDetails: PlaceDetails | null;
}

/**
 * Navigator HUD — portaled to document.body.
 *
 * Layout (390 px mobile, sheet = 26 dvh ≈ 219 px from bottom):
 *   TOP:    maneuver banner → geo pill → (alerts stack here)
 *   BOTTOM: one column anchored above the sheet, holding — top to bottom —
 *           the route-details panel (only while open), trip progress and the
 *           button row.
 *
 * The bottom blocks are ONE flex column, not four separately-fixed boxes each
 * with its own `bottom:`. Four independent anchors is what made them overlap:
 * the details panel and the next-stop card were both pinned to the same offset
 * and simply drew on top of each other, and the progress strip sat under the
 * button row. In one column each block takes its own height, so the stack grows
 * upward from the sheet and nothing can collide.
 *
 * Alerts (z-62) render above the stack (z-61).
 */
export const FerrostarNavigationHud = ({
  maneuverFallback,
  alerts,
  announcement,
  onExit,
  onAdvance,
  onVoiceToggle,
  onDetailsToggle,
  voiceMuted,
  advanceDisabled,
  detailsToggleLabel,
  detailsOpen,
  totalStops,
  progressDone,
  progressMinutesLeft,
  progressMetresDone,
  progressMetresTotal,
  progressRemainingMinutes,
  stopListItems,
  stopListVisited,
  stopListNextId,
  stopListNextDistance,
  onStopListToggle,
  onStopListVisitMinutesChange,
  suggestions = [],
  onAddSuggestion,
  onSkipSuggestion,
  nextStopName,
  nextStopId,
  nextPlaceDetails,
}: FerrostarNavigationHudProps) => {
  const { t } = useTranslation();
  /**
   * Which stop's place info is open, kept as an id rather than a boolean: a new
   * stop then closes it by construction (`placeDetailsFor === nextStopId` goes
   * false on its own), instead of an effect that has to write state back after
   * the render that changed the stop.
   */
  const [placeDetailsFor, setPlaceDetailsFor] = useState<string | null>(null);
  const placeDetailsOpen = nextStopId != null && placeDetailsFor === nextStopId;

  /**
   * Skipping a suggestion is reported to the panel, which owns the «skipped» set
   * for the whole guide — the HUD keeps no second copy that could disagree with
   * it after a rerender.
   */
  const dismissSuggestion = (id: string) => {
    if (onSkipSuggestion) onSkipSuggestion(id);
  };

  console.log('[FerrostarNavigationHud] rendering');
  if (typeof document === 'undefined') return null;

  return createPortal(
    <section
      data-testid="guide-panel"
      data-mode="moving"
      aria-label={t('guide.title')}
      className="fixed inset-0 z-[100]"
    >
      {/* ── Top column: maneuver banner → alerts.
          One flex column pinned to the top, for the same reason the bottom is one
          column: each of these was separately `fixed` at a guessed `top:` offset
          (+5.25rem, +8rem), and those guesses drifted out of step with the real
          banner height. In one column each block sits below the one above it
          and grows downward, whatever the banner turns out to be.

          `md:right-[4.5rem]` leaves the right gutter to the floating cluster
          (выход / звук) further down: the stack used to run under it, and an
          alert under those buttons was unreachable. */}
      <div
        data-testid="guide-top-stack"
        className="fixed inset-x-3 top-[max(env(safe-area-inset-top),0.75rem)] z-[62] flex max-h-[calc(100dvh-var(--sheet-h,0px)-16rem)] flex-col gap-2 md:left-[calc(var(--panel-width,0px)+0.75rem)] md:right-[4.5rem]"
      >
        <div className="flex shrink-0 items-start gap-2">
          <div
            data-testid="guide-maneuver"
            className="pointer-events-auto min-w-0 max-w-[540px] flex-1 overflow-hidden rounded-2xl border border-border bg-card shadow-float"
          >
            {maneuverFallback}
          </div>
        </div>

        {alerts && (
          <div className="pointer-events-auto mx-auto flex w-[min(88vw,540px)] shrink-0 flex-col gap-2 overflow-y-auto md:mx-0 md:w-[540px]">
            {alerts}
          </div>
        )}
      </div>

      {/* ── Bottom column: details panel → progress → buttons.
          One flex column anchored to the sheet, so the blocks stack instead of
          overlapping, and the whole thing grows upward as details open.

           The height budget is explicit, because a phone does not have it to
           spare. The stack is capped above the sheet; if the route details are
           open, their list scrolls rather than pushing the navigation controls
           off the screen.

           `md:max-w-[560px]` is what makes this a navigator on a monitor rather
           than a phone row stretched across it: unbounded, the two ends of the
           button row sat 992px apart — «маршрут · 0 из 2» pinned to the far left
           and «я на месте» to the far right of a 1440px display, with nothing
           between them but empty map. */}
      <div
        data-testid="guide-bottom-stack"
        className="fixed inset-x-3 z-[61] flex max-h-[calc(100dvh-var(--sheet-h,0px)-15rem)] flex-col gap-2 md:left-[calc(var(--panel-width,0px)+1rem)] md:right-3 md:max-w-[560px]"
        style={{
          bottom:
            'calc(var(--sheet-h,0px) + env(safe-area-inset-bottom) + 0.5rem)',
        }}
      >
        {/* Details panel — the stop list, on demand. */}
        {detailsOpen && (
          <div
            data-testid="guide-details-panel"
            className="pointer-events-auto flex min-h-[9rem] flex-1 flex-col overflow-hidden rounded-2xl border border-border bg-background/95 shadow-float backdrop-blur"
          >
            <div className="slim-scroll flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto p-3">
              <div className="flex items-center justify-between">
                <span className="text-meta font-medium text-muted-foreground">
                  {t('guide.activityTitle')}
                </span>
                <button
                  type="button"
                  onClick={onDetailsToggle}
                  className="flex h-8 items-center gap-1 rounded-full px-3 text-meta text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
                  aria-label={t('guide.activityHide')}
                >
                  <span aria-hidden="true">✕</span>
                </button>
              </div>

              {nextPlaceDetails && nextStopName && (
                <div className="rounded-xl border border-border bg-card p-2">
                  <div className="flex items-center gap-1.5 px-1 text-meta font-medium text-muted-foreground">
                    <BookOpenText className="size-3.5" aria-hidden="true" />
                    <span className="min-w-0 flex-1 truncate">
                      {nextPlaceDetails.name || nextStopName}
                    </span>
                  </div>
                  {nextPlaceDetails.category && (
                    <p className="mt-1.5 px-1 text-meta capitalize text-muted-foreground">
                      {nextPlaceDetails.category}
                    </p>
                  )}
                  {(nextPlaceDetails.blurb || nextPlaceDetails.funFact) && (
                    <p className="mt-1 px-1 text-meta leading-snug text-muted-foreground">
                      {nextPlaceDetails.blurb ?? nextPlaceDetails.funFact}
                    </p>
                  )}
                  <button
                    type="button"
                    data-testid="guide-place-details-toggle"
                    aria-expanded={placeDetailsOpen}
                    onClick={() =>
                      setPlaceDetailsFor(placeDetailsOpen ? null : nextStopId)
                    }
                    className="mt-1 flex min-h-10 items-center gap-1.5 px-1 text-label font-medium text-primary hover:underline"
                  >
                    <BookOpenText className="size-4" aria-hidden="true" />
                    {placeDetailsOpen
                      ? t('guide.hidePlaceInfo')
                      : t('guide.readPlaceInfo')}
                  </button>
                  {placeDetailsOpen && (
                    <div className="slim-scroll mt-1 max-h-[34dvh] overflow-y-auto">
                      <PlaceCardBody
                        details={nextPlaceDetails}
                        onClose={() => setPlaceDetailsFor(null)}
                        mobile
                      />
                    </div>
                  )}
                </div>
              )}

              {suggestions.length > 0 && (
                <div
                  data-testid="guide-suggestions"
                  className="rounded-xl border border-border bg-card p-2"
                >
                  <div className="px-1 text-meta font-medium text-muted-foreground">
                    {t('guide.suggestionsTitle')}
                  </div>
                  {suggestions.map((s) => (
                    <div
                      key={s.id}
                      className="mt-2 flex items-center gap-2 px-1"
                    >
                      <div className="min-w-0 flex-1">
                        <div className="truncate text-body">{s.name}</div>
                        <div className="text-meta text-muted-foreground">
                          {s.detail}
                        </div>
                      </div>
                      <button
                        type="button"
                        data-testid={`guide-suggestion-add-${s.id}`}
                        onClick={() => onAddSuggestion?.(s.id)}
                        className="h-8 shrink-0 rounded-full border border-border px-3 text-meta transition-colors hover:bg-muted"
                      >
                        {t('guide.suggestionAdd')}
                      </button>
                      <button
                        type="button"
                        data-testid={`guide-suggestion-skip-${s.id}`}
                        onClick={() => dismissSuggestion(s.id)}
                        className="h-8 shrink-0 rounded-full px-2 text-meta text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
                      >
                        {t('guide.suggestionSkip')}
                      </button>
                    </div>
                  ))}
                </div>
              )}

              <div className="border-t border-border pt-3">
                <GuideStopList
                  stops={stopListItems}
                  visited={stopListVisited}
                  nextId={stopListNextId}
                  nextDistance={stopListNextDistance}
                  onToggle={onStopListToggle}
                  onVisitMinutesChange={onStopListVisitMinutesChange}
                />
              </div>
            </div>
          </div>
        )}

        {!nextStopName && (
          <div className="pointer-events-auto shrink-0">
            <GuideRouteDone total={totalStops} />
          </div>
        )}

        {/* Trip progress strip. Our own, always: the Ferrostar web component was
            a bare `13:49 | 29m 57s | 2 км` with no labels and its own sans-serif,
            so the one number the tourist watches while walking was the one thing
            on screen that said nothing. */}
        <div className="pointer-events-auto shrink-0">
          <div
            data-testid="guide-trip-progress"
            className="rounded-2xl border border-border bg-background/95 px-3 py-2 shadow-float backdrop-blur"
          >
            <GuideProgress
              compact
              done={progressDone}
              total={totalStops}
              minutesLeft={progressMinutesLeft}
              metresDone={progressMetresDone}
              metresTotal={progressMetresTotal}
              remainingMinutes={progressRemainingMinutes}
            />
          </div>
        </div>

        {/* Details toggle + advance. */}
        <div className="pointer-events-auto flex shrink-0 items-center justify-between gap-2">
          <button
            type="button"
            data-testid="guide-route-details-toggle"
            onClick={onDetailsToggle}
            aria-expanded={detailsOpen}
            className="flex h-11 items-center gap-2 rounded-full border border-border bg-background/95 px-4 text-label font-semibold text-foreground shadow-float backdrop-blur transition hover:bg-muted"
          >
            <ListChecks className="size-4 text-primary" aria-hidden="true" />
            {detailsToggleLabel}
          </button>

          <button
            type="button"
            data-testid="guide-advance"
            onClick={onAdvance}
            disabled={advanceDisabled}
            className="flex h-11 items-center justify-center gap-2 rounded-full bg-primary px-5 text-label font-semibold text-primary-foreground shadow-float transition hover:brightness-95 active:scale-[0.98] disabled:opacity-40"
            aria-label={
              nextStopName
                ? t('guide.markNextStop', { name: nextStopName })
                : t('guide.advance')
            }
            title={
              nextStopName
                ? t('guide.markNextStop', { name: nextStopName })
                : t('guide.advance')
            }
          >
            <MapPinCheck className="size-5" aria-hidden="true" />
            {t('guide.advance')}
          </button>
        </div>
      </div>

      {/* ── Right-side floating controls (top-right cluster)

          «выйти» lives here, not in the sidebar's header: the sidebar is hidden
          while walking (it would be an empty 420px column with nothing in it),
          so a control that only existed there would take the way out of
          navigation with it. ── */}
      <div className="fixed right-4 top-[calc(max(env(safe-area-inset-top),0.75rem)+7rem)] z-[60] hidden flex-col gap-2 md:right-5 md:flex">
        <button
          type="button"
          data-testid="guide-exit-hud"
          onClick={onExit}
          className="pointer-events-auto hidden size-11 items-center justify-center rounded-full border border-border bg-background/95 text-foreground shadow-float transition hover:bg-muted md:flex"
          aria-label={t('guide.exit')}
          title={t('guide.exit')}
        >
          <XIcon className="size-5" aria-hidden="true" />
        </button>
        <button
          type="button"
          data-testid="guide-voice-toggle-hud"
          aria-label={
            voiceMuted ? t('guide.enableSound') : t('guide.disableSound')
          }
          title={voiceMuted ? t('guide.enableSound') : t('guide.disableSound')}
          onClick={onVoiceToggle}
          className="pointer-events-auto flex size-11 items-center justify-center rounded-full border border-border bg-background/95 text-foreground shadow-float transition hover:bg-muted"
        >
          {voiceMuted ? (
            <VolumeX className="size-5" aria-hidden="true" />
          ) : (
            <Volume2 className="size-5" aria-hidden="true" />
          )}
        </button>
      </div>

      <div aria-live="polite" role="status" className="sr-only">
        {announcement}
      </div>
    </section>,
    document.body
  );
};
