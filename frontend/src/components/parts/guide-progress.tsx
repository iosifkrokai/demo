import { useTranslation } from 'react-i18next';

import { cn } from '@/lib/utils';

import { fmtDist, fmtMin } from './guide-format';
import { guideModeFor, type GuideTravelMode } from './guide-mode';

interface GuideProgressProps {
  done: number;
  total: number;
  /** Visit minutes still ahead — estimates and the tourist's own numbers. */
  minutesLeft: number;
  /** Metres of the route line walked so far (null when there is no line). */
  metresDone?: number | null;
  /** Total length of the route line in metres (null when there is no line). */
  metresTotal?: number | null;
  /** Travel time + visits still ahead, on top of the visit minutes. */
  remainingMinutes?: number | null;
  /** «пройдено» on foot, «проехано» by bike or car. */
  mode?: GuideTravelMode;
  /** The navigator's shape: «пройдено 1 из 3 · 787 м» + the bar, on one row. */
  compact?: boolean;
}

/** «пройдено 3 из 7» + a slim bar, per DESIGN.md: the number that matters stays big, everything around it stays quiet. */
export const GuideProgress = ({
  done,
  total,
  minutesLeft,
  metresDone,
  metresTotal,
  remainingMinutes,
  mode,
  compact = false,
}: GuideProgressProps) => {
  const { t } = useTranslation();
  const travel = mode ?? guideModeFor(null);
  const stopPercent = total === 0 ? 0 : Math.round((done / total) * 100);
  const hasLine = metresTotal != null && metresTotal > 0 && metresDone != null;
  const linePercent = hasLine
    ? Math.max(0, Math.min(100, Math.round((metresDone / metresTotal) * 100)))
    : null;
  const percent = linePercent ?? stopPercent;
  const remaining = hasLine ? Math.max(0, metresTotal - metresDone) : null;
  const doneWord = travel.doneWord;
  const lineLabel =
    travel.id === 'foot' ? t('guide.alongLine') : t('guide.alongRoute');

  return (
    <div>
      <div className="flex items-baseline justify-between gap-2">
        <span className="text-body font-semibold">
          {doneWord} {t('guide.progressOf', { done, total })}
        </span>
        {minutesLeft > 0 && (
          <span
            data-testid="guide-minutes-left"
            className="text-meta text-muted-foreground"
          >
            {t('guide.visitsLeft', { time: fmtMin(minutesLeft) })}
          </span>
        )}
      </div>
      {!compact &&
        hasLine &&
        remainingMinutes != null &&
        remainingMinutes > 0 && (
          <div
            data-testid="guide-remaining"
            className="mt-1 text-meta text-muted-foreground"
          >
            {t('guide.withTravelLeft', { time: fmtMin(remainingMinutes) })}
          </div>
        )}
      <div
        role="progressbar"
        aria-valuenow={done}
        aria-valuemin={0}
        aria-valuemax={total}
        aria-valuetext={
          linePercent != null
            ? t('guide.progressLine', { doneWord, percent: linePercent })
            : t('guide.progressOf', { done, total })
        }
        aria-label={t('guide.progressLabel')}
        data-testid="guide-progress-bar"
        className={cn(
          'h-1.5 w-full overflow-hidden rounded-full bg-muted',
          compact ? 'mt-1' : 'mt-1.5'
        )}
      >
        <div
          className="h-full rounded-full bg-primary transition-[width] duration-200 ease-out motion-reduce:transition-none"
          style={{ width: `${percent}%` }}
        />
      </div>
      {hasLine && remaining != null && (
        <div
          data-testid="guide-line-progress"
          className={cn(
            'flex items-baseline justify-between gap-2 text-meta text-muted-foreground',
            compact ? 'mt-1' : 'mt-1.5'
          )}
        >
          <span>
            {lineLabel} {doneWord} {fmtDist(metresDone)}
          </span>
          <span>
            {t('guide.lineRemaining', { distance: fmtDist(remaining) })}
          </span>
        </div>
      )}
    </div>
  );
};
