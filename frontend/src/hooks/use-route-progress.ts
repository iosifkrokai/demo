import { useEffect, useRef, useState } from 'react';

import { fetchRouteProgress, type RouteStageCode } from '@/api/progress';

/** How often the panel asks the pipeline where it got to. */
export const POLL_INTERVAL_MS = 1200;

/**
 * The pipeline's own stage while a request is in flight.
 *
 * Returns `null` whenever the server cannot say — no id, an unknown id, a
 * request that failed, a tick that came back empty. The panel has a truthful
 * sentence for that case («отправил запрос — жду план»), so silence here costs
 * honesty nothing; inventing a stage would cost everything.
 *
 * Polling stops as soon as the pipeline says it is done, and on unmount: a
 * finished request must not keep a timer alive in the background.
 */
export const useRouteProgress = (
  progressId: string | null,
  { enabled }: { enabled: boolean }
): RouteStageCode | null => {
  const [stage, setStage] = useState<RouteStageCode | null>(null);
  // Read inside the interval without restarting it on every render.
  const runningRef = useRef(false);

  useEffect(() => {
    if (!enabled || !progressId) {
      setStage(null);
      return;
    }

    runningRef.current = true;
    setStage(null);
    const controller = new AbortController();
    // Declared before the first tick so a fast answer can never touch it in its
    // temporal dead zone.
    let timer = 0;

    const tick = async () => {
      if (!runningRef.current) return;
      const progress = await fetchRouteProgress(progressId, controller.signal).catch(
        () => null
      );
      if (!runningRef.current) return;
      // A null answer is «сервер не знает» — the panel keeps its own sentence.
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
