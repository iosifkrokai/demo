import { Bike, Car, Footprints, type LucideIcon } from 'lucide-react';

/**
 * How the guide talks about movement, per transport.
 *
 * The turn instructions themselves come from Valhalla with the plan's own
 * costing (a car route already gets driving instructions), and the speed behind
 * the ETA comes from the route summary — so both follow the transport. What did
 * NOT follow it was the guide's own voice: it said «идти», drew footprints and
 * counted «пройдено», which is wrong on a bicycle and nonsense in a car.
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

const FOOT: GuideTravelMode = {
  id: 'foot',
  verb: 'идти',
  doneWord: 'пройдено',
  label: 'пешком',
  imperative: 'Идите',
  icon: Footprints,
};

/**
 * Walking is the product's default: what a route without a stated transport
 * honestly means, and the wording the guide falls back to.
 */
export const DEFAULT_TRAVEL_MODE = FOOT;

const BIKE: GuideTravelMode = {
  id: 'bike',
  verb: 'ехать',
  doneWord: 'проехано',
  label: 'на велосипеде',
  imperative: 'Поезжайте',
  icon: Bike,
};

const CAR: GuideTravelMode = {
  id: 'car',
  verb: 'ехать',
  doneWord: 'проехано',
  label: 'на машине',
  imperative: 'Поезжайте',
  icon: Car,
  arrivalHint: 'припаркуйтесь у остановки',
};

/**
 * Valhalla costing name → the guide's travel mode.
 *
 * Unknown or absent costing stays pedestrian: walking is the product's default
 * and the honest reading of a route whose transport nobody stated.
 */
export const guideModeFor = (costing?: string | null): GuideTravelMode => {
  switch ((costing ?? '').toLowerCase()) {
    case 'bicycle':
      return BIKE;
    case 'auto':
    case 'car':
    case 'truck':
    case 'bus':
    case 'motor_scooter':
    case 'motorcycle':
      return CAR;
    default:
      return FOOT;
  }
};
