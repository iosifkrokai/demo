import { MapPinned } from 'lucide-react';

/**
 * Nothing to walk yet. DESIGN.md: an empty panel is never blank — it gets a
 * muted icon and one line saying where the route comes from.
 */
export const GuideEmpty = () => (
  <div
    data-testid="guide-empty"
    className="rounded-2xl border border-border bg-card p-6 text-center shadow-card"
  >
    <span className="mx-auto flex size-12 items-center justify-center rounded-full bg-muted text-muted-foreground">
      <MapPinned className="size-6" />
    </span>
    <div className="mt-3 text-body font-semibold">маршрута пока нет</div>
    <p className="mx-auto mt-1 max-w-[26ch] text-label text-muted-foreground">
      Соберите маршрут в режиме планирования — и возвращайтесь сюда, чтобы идти
      по нему остановка за остановкой.
    </p>
  </div>
);
