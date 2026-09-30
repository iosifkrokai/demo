import { MapPin } from 'lucide-react';
import { cn } from '@/lib/utils';

/**
 * The agent's category taxonomy (backend/agent/constants.py → CATEGORIES) as
 * emoji: a stop's face says what it is before its name does. Unknown or
 * hand-placed points fall back to a muted pin.
 */
const CATEGORY_EMOJI: Record<string, string> = {
  замок: '🏰',
  костёл: '⛪',
  церковь: '⛪',
  храм: '⛪',
  монастырь: '⛪',
  дворец: '🏛',
  усадьба: '🏛',
  архитектура: '🏛',
  музей: '🖼',
  парк: '🌳',
  кладбище: '🪦',
  памятник: '🗿',
  кафе: '☕',
  ресторан: '🍽',
  туалет: '🚻',
  гостиница: '🏨',
  инфраструктура: '🚏',
  'остановка транспорта': '🚏',
};

export const categoryEmoji = (category?: string | null): string | null => {
  const key = category?.trim().toLowerCase();
  if (!key) return null;
  return CATEGORY_EMOJI[key] ?? null;
};

interface PlaceIconProps {
  category?: string | null;
  className?: string;
}

/** Purely decorative: the category itself is the row's tooltip. */
export const PlaceIcon = ({ category, className }: PlaceIconProps) => {
  const emoji = categoryEmoji(category);
  if (!emoji) {
    return (
      <MapPin
        aria-hidden="true"
        className={cn('h-3.5 w-3.5 shrink-0 text-muted-foreground', className)}
      />
    );
  }
  return (
    <span
      aria-hidden="true"
      title={category ?? undefined}
      className={cn('shrink-0 text-body leading-none', className)}
    >
      {emoji}
    </span>
  );
};
