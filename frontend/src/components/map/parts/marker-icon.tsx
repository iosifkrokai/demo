import { cva, type VariantProps } from 'class-variance-authority';
import { cn } from '@/lib/utils';

/** Marker colours — one meaning, one colour. */
export type MarkerColor =
  | 'start'
  | 'via'
  | 'finish'
  | 'me'
  | 'iso'
  | 'green'
  | 'grey'
  | 'red'
  | 'blue'
  | 'purple';

const START_FILL = '[&_path]:fill-[#222222]';
const VIA_FILL = '[&_path]:fill-[#717171]';
const FINISH_FILL = '[&_path]:fill-[#ff385c]';
const ME_FILL = '[&_path]:fill-[#007bff]';
const ISO_FILL = '[&_path]:fill-[#6f42c1]';

const markerIconVariants = cva('relative cursor-pointer w-[35px] h-[45px]', {
  variants: {
    color: {
      start: START_FILL,
      via: VIA_FILL,
      finish: FINISH_FILL,
      me: ME_FILL,
      iso: ISO_FILL,
      green: START_FILL,
      grey: VIA_FILL,
      red: FINISH_FILL,
      blue: ME_FILL,
      purple: ISO_FILL,
    },
  },
  defaultVariants: {
    color: 'via',
  },
});

/** The role colour for the stop at `index` of `total` numbered stops. */
export const markerColorForStop = (
  index: number,
  total: number
): MarkerColor => {
  if (index === 0) return 'start';
  if (index === total - 1 && total > 1) return 'finish';
  return 'via';
};

interface MarkerIconProps extends VariantProps<typeof markerIconVariants> {
  number?: string;
  className?: string;
}

export function MarkerIcon({ color, number, className }: MarkerIconProps) {
  return (
    <div
      className={cn(markerIconVariants({ color, className }))}
      aria-label={number ? `Точка ${number}` : 'Старт'}
    >
      <svg
        width="35"
        height="45"
        viewBox="0 0 35 45"
        className="drop-shadow-md"
      >
        <path
          d="M17.5,0 C7.8,0 0,7.8 0,17.5 C0,30.6 17.5,45 17.5,45 S35,30.6 35,17.5 C35,7.8 27.2,0 17.5,0 Z"
          stroke="#fff"
          strokeWidth="2"
        />
      </svg>
      {number && (
        <div className="absolute top-2 left-0 w-[35px] text-center text-white font-bold text-body pointer-events-none">
          {number}
        </div>
      )}
    </div>
  );
}
