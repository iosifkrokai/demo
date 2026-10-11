import { useEffect, useRef, useState } from 'react';

import { fetchRouteProgress, type RouteStageCode } from '@/api/progress';

/** How often the panel asks the pipeline where it got to. */
export const POLL_INTERVAL_MS = 1200;

/** The pipeline's own stage while a request is in flight. */
export const useRouteProgress = (
  progressId: string | null,
  { enabled }: { enabled: boolean }
): RouteStageCode | null => {
  const [stage, setStage] = useState<RouteStageCode | null>(null);
  const runningRef = useRef(false);

  useEffect(() => {
    if (!enabled || !progressId) {
      runningRef.current = false;
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setStage(null);
      return;
    }

    runningRef.current = true;
    setStage(null);
    const controller = new AbortController();
    let timer = 0;

    const tick = async () => {
      if (!runningRef.current) return;
      const progress = await fetchRouteProgress(
        progressId,
        controller.signal
      ).catch(() => null);
      if (!runningRef.current) return;
      if (!progress) return;
      setStage(progress.stage);
      if (progress.done) {
        runningRef.current = false;
        window.clearInterval(timer);
      }
    };

    void tick();
    timer = window.setInterval(() => void tick(), POLL_INTERVAL_MS);

    return () => {
      runningRef.current = false;
      controller.abort();
      window.clearInterval(timer);
    };
  }, [enabled, progressId]);

  return stage;
};
