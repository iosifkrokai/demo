import { fmtDist, fmtMin } from './guide-format';

interface GuideProgressProps {
  done: number;
  total: number;
  /** Visit minutes still ahead — the agent's own estimate, shown as «~». */
  minutesLeft: number;
  /** Metres of the route line walked so far (null when there is no line). */
  metresDone?: number | null;
  /** Total length of the route line in metres (null when there is no line). */
  metresTotal?: number | null;
  /** Walking time + visits still ahead, on top of the visit minutes. */
  remainingMinutes?: number | null;
}

/**
 * «пройдено 3 из 7» + a slim bar, per DESIGN.md: the number that matters
 * stays big, everything around it stays quiet.
 *
 * With a route line the bar tracks the metres actually walked (done part vs
 * remaining) instead of the stop count, and says so in words underneath —
 * stops are a coarse ruler, the line is the real one.
 */
export const GuideProgress = ({
  done,
  total,
  minutesLeft,
  metresDone,
  metresTotal,
  remainingMinutes,
}: GuideProgressProps) => {
  const stopPercent = total === 0 ? 0 : Math.round((done / total) * 100);
  const hasLine = metresTotal != null && metresTotal > 0 && metresDone != null;
  const linePercent = hasLine
    ? Math.max(0, Math.min(100, Math.round((metresDone / metresTotal) * 100)))
    : null;
  const percent = linePercent ?? stopPercent;
  const remaining = hasLine ? Math.max(0, metresTotal - metresDone) : null;

  return (
    <div>
      <div className="flex items-baseline justify-between gap-2">
        <span className="text-body font-semibold">
          пройдено {done} из {total}
        </span>
        {minutesLeft > 0 && (
          <span
            data-testid="guide-minutes-left"
            className="text-meta text-muted-foreground"
          >
            осталось осмотра ~{fmtMin(minutesLeft)}
          </span>
        )}
      </div>
      {hasLine && remainingMinutes != null && remainingMinutes > 0 && (
        <div
          data-testid="guide-remaining"
          className="mt-1 text-meta text-muted-foreground"
        >
          с дорогой осталось ~{fmtMin(remainingMinutes)}
        </div>
      )}
      <div
        role="progressbar"
        aria-valuenow={done}
        aria-valuemin={0}
        aria-valuemax={total}
        aria-valuetext={
          linePercent != null
            ? `пройдено ${linePercent}% линии`
            : `пройдено ${done} из ${total}`
        }
        aria-label="прогресс маршрута"
        data-testid="guide-progress-bar"
        className="mt-1.5 h-1.5 w-full overflow-hidden rounded-full bg-muted"
      >
        <div
          className="h-full rounded-full bg-primary transition-[width] duration-200 ease-out motion-reduce:transition-none"
          style={{ width: `${percent}%` }}
        />
      </div>
      {hasLine && remaining != null && (
        <div
          data-testid="guide-line-progress"
          className="mt-1.5 flex items-baseline justify-between gap-2 text-meta text-muted-foreground"
        >
          <span>по линии пройдено {fmtDist(metresDone)}</span>
          <span>осталось {fmtDist(remaining)}</span>
        </div>
      )}
    </div>
  );
};
