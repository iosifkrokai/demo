import { CircleCheck } from 'lucide-react';

/**
 * Shown instead of the next stop once the walk is over — the panel keeps its
 * shape so the last stop does not make everything above it jump.
 */
export const GuideRouteDone = ({ total }: { total: number }) => (
  <div className="rounded-2xl border border-border bg-card p-4 shadow-[0_1px_2px_rgba(0,0,0,0.06)]">
    <div className="flex items-center gap-3">
      <span className="flex size-9 shrink-0 items-center justify-center rounded-full bg-primary/10 text-primary">
        <CircleCheck className="h-5 w-5" />
      </span>
      <div>
        <div className="text-[16px] font-semibold leading-tight">
          маршрут пройден
        </div>
        <div className="mt-0.5 text-[12px] text-muted-foreground">
          все {total} остановок отмечены — можно начать заново
        </div>
      </div>
    </div>
  </div>
);
