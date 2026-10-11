/** Loading state: skeleton rows, never a spinner, so the panel keeps its shape. */

export const StopsSkeleton = ({ rows = 3 }: { rows?: number }) => (
  <div
    data-testid="stops-skeleton"
    aria-hidden="true"
    className="flex flex-col gap-1"
  >
    {Array.from({ length: rows }, (_, i) => (
      <div
        key={`stop-${i}`}
        className="h-[52px] animate-pulse rounded-xl bg-muted"
      />
    ))}
  </div>
);

export const SummarySkeleton = () => (
  <div
    data-testid="summary-skeleton"
    aria-hidden="true"
    className="grid grid-cols-3 gap-2"
  >
    {['точек', 'км', 'мин в пути'].map((label) => (
      <div key={label} className="h-[62px] animate-pulse rounded-xl bg-muted" />
    ))}
  </div>
);
