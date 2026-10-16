import type { TFunction } from 'i18next';

import type { Profile } from '@/stores/common-store';

/** One-tap starters. */
export interface HintChip {
  /** Stable id: the chip's text is a whole sentence, and it may be reworded. */
  id: string;
  text: string;
  /** The transport this request needs, `''` for «any»; `null` leaves the chosen one. */
  transport: '' | Profile | null;
}

/** A batch of chips as the dialogue shows them: by transport, or one plain list. */
export interface HintGroup {
  key: string;
  /** Absent for the refine row, where every chip leaves the transport alone. */
  label?: string;
  chips: HintChip[];
}

/** The chips offered before anything is planned — the requests the benchmark measures. */
export const buildHintChips = (t: TFunction): HintChip[] => [
  {
    id: 'old-town-castles',
    text: t('ask.chips.oldTownCastles'),
    transport: 'pedestrian',
  },
  {
    id: 'museums-rainy-day',
    text: t('ask.chips.museumsRainyDay'),
    transport: 'pedestrian',
  },
  {
    id: 'family-with-kids',
    text: t('ask.chips.familyWithKids'),
    transport: 'pedestrian',
  },
  {
    id: 'four-faiths',
    text: t('ask.chips.fourFaiths'),
    transport: 'pedestrian',
  },
  {
    id: 'free-old-town-walk',
    text: t('ask.chips.freeOldTownWalk'),
    transport: 'pedestrian',
  },
  {
    id: 'tyzenhauz-north',
    text: t('ask.chips.tyzenhauzNorth'),
    transport: 'pedestrian',
  },
  {
    id: 'bicycle-canal',
    text: t('ask.chips.bicycleCanal'),
    transport: 'bicycle',
  },
  {
    id: 'augustow-canal-bike',
    text: t('ask.chips.augustowCanalBike'),
    transport: 'bicycle',
  },
  { id: 'palaces-day', text: t('ask.chips.palacesDay'), transport: 'car' },
  {
    id: 'castles-lida-krevo-golshany',
    text: t('ask.chips.castlesLidaKrevoGolshany'),
    transport: 'car',
  },
  {
    id: 'mir-novogrudok',
    text: t('ask.chips.mirNovogrudok'),
    transport: 'car',
  },
  {
    id: 'west-palaces-and-churches',
    text: t('ask.chips.westPalacesAndChurches'),
    transport: 'car',
  },
  {
    id: 'synagogues-day',
    text: t('ask.chips.synagoguesDay'),
    transport: 'car',
  },
  {
    id: 'castles-half-day',
    text: t('ask.chips.castlesHalfDay'),
    transport: '',
  },
  { id: 'museums-budget', text: t('ask.chips.museumsBudget'), transport: '' },
  { id: 'family-walk', text: t('ask.chips.familyWalk'), transport: '' },
  { id: 'avoid-temples', text: t('ask.chips.avoidTemples'), transport: '' },
  { id: 'named-farnyi', text: t('ask.chips.namedFarnyi'), transport: '' },
  { id: 'free-old-town', text: t('ask.chips.freeOldTown'), transport: '' },
  {
    id: 'all-churches-catalogue',
    text: t('ask.chips.allChurchesCatalogue'),
    transport: '',
  },
  { id: 'vague-no-anchor', text: t('ask.chips.vagueNoAnchor'), transport: '' },
  {
    id: 'out-of-region-vilnius',
    text: t('ask.chips.outOfRegionVilnius'),
    transport: '',
  },
];

/** The chips offered once a route exists. */
export const buildRefineHintChips = (t: TFunction): HintChip[] => [
  {
    id: 'refine-add-cafe-toilet',
    text: t('ask.chipsRefine.addCafeToilet'),
    transport: null,
  },
  {
    id: 'refine-add-cafe',
    text: t('ask.chipsRefine.addCafe'),
    transport: null,
  },
  {
    id: 'refine-remove-museum',
    text: t('ask.chipsRefine.removeMuseum'),
    transport: null,
  },
  { id: 'refine-shorter', text: t('ask.chipsRefine.shorter'), transport: null },
  {
    id: 'refine-only-churches',
    text: t('ask.chipsRefine.onlyChurches'),
    transport: null,
  },
  {
    id: 'refine-with-children',
    text: t('ask.chipsRefine.withChildren'),
    transport: null,
  },
];

const GROUP_ORDER = ['pedestrian', 'bicycle', 'car', ''] as const;

/** The chips as the dialogue lists them: «пешком», «велосипед», «машина», «как удобно». */
export const groupHintChips = (
  chips: HintChip[],
  t: TFunction
): HintGroup[] => {
  const groups: HintGroup[] = GROUP_ORDER.map((transport) => ({
    key: transport || 'any',
    label: t(`sidebar.transport.${transport || 'any'}`),
    chips: [],
  }));
  const plain: HintGroup = { key: 'refine', chips: [] };

  for (const chip of chips) {
    if (chip.transport === null) plain.chips.push(chip);
    else
      groups
        .find((group) => group.key === (chip.transport || 'any'))
        ?.chips.push(chip);
  }

  const ordered = groups.filter((group) => group.chips.length > 0);
  if (plain.chips.length === 0) return ordered;
  return ordered.length === 0 ? [plain] : [...ordered, plain];
};
