import { fmtMin } from './guide-format';

interface GuideProgressProps {
  done: number;
  total: number;
  /** Visit minutes still ahead — the agent's own estimate, shown as «~». */
  minutesLeft: number;
}

/**
 * «пройдено 3 из 7» + a slim bar, per DESIGN.md: the number that matters
 * stays big, everything around it stays quiet.
 */
export const GuideProgress = ({
  done,
  total,
  minutesLeft,
}: GuideProgressProps) => {
  const percent = total === 0 ? 0 : Math.round((done / total) * 100);

  return (
    <div>
      <div className="flex items-baseline justify-between gap-2">
        <span className="text-[15px] font-semibold">
          пройдено {done} из {total}
        </span>
        {minutesLeft > 0 && (
          <span
            data-testid="guide-minutes-left"
            className="text-[12px] text-muted-foreground"
          >
            осталось осмотра ~{fmtMin(minutesLeft)}
          </span>
        )}
      </div>
      <div
        role="progressbar"
        aria-valuenow={done}
        aria-valuemin={0}
        aria-valuemax={total}
        aria-label="прогресс маршрута"
        data-testid="guide-progress-bar"
        className="mt-1.5 h-1.5 w-full overflow-hidden rounded-full bg-muted"
      >
        <div
          className="h-full rounded-full bg-primary transition-[width] duration-200 ease-out motion-reduce:transition-none"
          style={{ width: `${percent}%` }}
        />
      </div>
    </div>
  );
};
