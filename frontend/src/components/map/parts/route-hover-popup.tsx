import { Popup } from 'react-map-gl/maplibre';
import { MoveHorizontal, Clock } from 'lucide-react';
import { useTranslation } from 'react-i18next';

import { fmtMin } from '@/components/parts/guide-format';
import { formatNumber } from '@/i18n/format';
import type { Summary } from '@/components/types';

interface RouteHoverPopupProps {
  lng: number;
  lat: number;
  summary: Summary;
}

export function RouteHoverPopup({ lng, lat, summary }: RouteHoverPopupProps) {
  const { t } = useTranslation();
  return (
    <Popup
      longitude={lng}
      latitude={lat}
      anchor="bottom"
      closeButton={false}
      closeOnClick={false}
      maxWidth="none"
    >
      <div className="min-w-[120px] px-2">
        <div className="font-bold text-muted-foreground">
          {t('map.routeSummary')}
        </div>
        <div className="flex items-center gap-1">
          <MoveHorizontal className="size-3.5" />
          <span>
            {t('guide.km', {
              value: formatNumber(summary.length, {
                maximumFractionDigits: summary.length > 1000 ? 0 : 1,
                useGrouping: false,
              }),
            })}
          </span>
        </div>
        <div className="flex items-center gap-1">
          <Clock className="size-3.5" />
          <span>{fmtMin(summary.time / 60)}</span>
        </div>
      </div>
    </Popup>
  );
}
