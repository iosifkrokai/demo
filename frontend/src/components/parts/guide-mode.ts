import { Bike, Car, Footprints, type LucideIcon } from 'lucide-react';

import i18n from '@/i18n';

/**
 * How the guide talks about movement, per transport.
 *
 * The turn instructions themselves come from Valhalla with the plan's own
 * costing (a car route already gets driving instructions), and the speed behind
 * the ETA comes from the route summary — so both follow the transport. What did
 * NOT follow it was the guide's own voice: it said «идти», drew footprints and
 * counted «пройдено», which is wrong on a bicycle and nonsense in a car.
 *
 * The wording lives in the `guide` area of the dictionary; this module only
 * maps a costing name to the words that belong to it, resolved through the same
 * i18n instance the components use, so a mode reads correctly in either
 * language without every caller repeating the lookup.
 */
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

/**
 * Walking is the product's default: what a route without a stated transport
 * honestly means, and the wording the guide falls back to.
 */
export const defaultTravelMode = (): GuideTravelMode => resolve(FOOT);

/**
 * Valhalla costing name → the guide's travel mode.
 *
 * Unknown or absent costing stays pedestrian: walking is the product's default
 * and the honest reading of a route whose transport nobody stated.
 */
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
