import { Bike, Car, Footprints, type LucideIcon } from 'lucide-react';

import i18n from '@/i18n';

/** How the guide talks about movement, per transport. */
export type GuideTravelModeId = 'foot' | 'bike' | 'car';

export interface GuideTravelMode {
  id: GuideTravelModeId;
  /** «идти» / «ехать» — moving between stops. */
  verb: string;
  /** «пройдено» / «проехано» — the progress line. */
  doneWord: string;
  /** «пешком» / «на велосипеде» / «на машине». */
  label: string;
  /** «Идите» / «Поезжайте» — first word of the guide's own sentences. */
  imperative: string;
  icon: LucideIcon;
  /** Car only: what to do with the vehicle on arrival at a stop. */
  arrivalHint?: string;
}

/** A mode before its words are looked up: the id, the key prefix, the icon. */
interface ModeDescriptor {
  id: GuideTravelModeId;
  /** Prefix of the dictionary keys (`modeFoot` → `guide.modeFootVerb`, …). */
  key: string;
  icon: LucideIcon;
  arrivalHintKey?: string;
}

const FOOT: ModeDescriptor = { id: 'foot', key: 'modeFoot', icon: Footprints };
const BIKE: ModeDescriptor = { id: 'bike', key: 'modeBike', icon: Bike };
const CAR: ModeDescriptor = {
  id: 'car',
  key: 'modeCar',
  icon: Car,
  arrivalHintKey: 'modeCarHint',
};

const resolve = (mode: ModeDescriptor): GuideTravelMode => ({
  id: mode.id,
  verb: i18n.t(`guide.${mode.key}Verb`),
  doneWord: i18n.t(`guide.${mode.key}Done`),
  label: i18n.t(`guide.${mode.key}Label`),
  imperative: i18n.t(`guide.${mode.key}Imperative`),
  icon: mode.icon,
  arrivalHint: mode.arrivalHintKey
    ? i18n.t(`guide.${mode.arrivalHintKey}`)
    : undefined,
});

/** Walking: the default travel mode when a route states no transport. */
export const defaultTravelMode = (): GuideTravelMode => resolve(FOOT);

/** Valhalla costing name → the guide's travel mode. */
export const guideModeFor = (costing?: string | null): GuideTravelMode => {
  switch ((costing ?? '').toLowerCase()) {
    case 'bicycle':
      return resolve(BIKE);
    case 'auto':
    case 'car':
    case 'truck':
    case 'bus':
    case 'motor_scooter':
    case 'motorcycle':
      return resolve(CAR);
    default:
      return resolve(FOOT);
  }
};
