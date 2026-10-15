import type { Dictionary } from './ru';

/** English strings. */
export const en: Dictionary = {
  panel: {
    resize: 'resize the panel',
  },
  app: {
    title: 'Grodno AI guide',
    description: 'Route planner for Grodno and the region',
  },
  tabs: {
    plan: 'Plan',
    planShort: 'Plan',
    itineraries: 'Ready-made routes',
    itinerariesShort: 'Routes',
    places: 'All points',
    placesShort: 'Points',
    history: 'History',
  },
  tabSubtitles: {
    plan: 'Describe what you want — I will build a route on real roads',
    itineraries: 'Ready-made routes — start from one of them',
    places: 'The whole catalogue — on the map and as a list',
    history: 'Routes you have already built',
  },
  ask: {
    placeholderRefine: 'What to refine? “add a coffee shop”',
    placeholder: 'What would you like to see?',
    label: 'What would you like to see',
    chips: {
      oldTownCastles: 'two castles and Sovetskaya street in Grodno, four hours',
      museumsRainyDay:
        'where to go in Grodno when it rains, museums under a roof, three hours',
      familyWithKids: 'Grodno with children: castles, park and zoo, half a day',
      fourFaiths:
        'a catholic church, a synagogue, a monastery and the churches of old Grodno',
      freeOldTownWalk: 'what to see in Grodno for free, three hours',
      tyzenhauzNorth:
        'Horodnica and the age of Tyzenhauz, a walk in northern Grodno',
      bicycleCanal:
        'the Augustow canal by bicycle: Sopotskin and the locks, four hours',
      augustowCanalBike: 'the Augustow canal by bicycle, four hours',
      palacesDay:
        'palaces and manors of the west of the region by car, a whole day',
      castlesLidaKrevoGolshany:
        'Lida, Krevo and Golshany castles in one day by car',
      mirNovogrudok:
        'Mir and Novogrudok in one day: two castles and Mickiewicz',
      westPalacesAndChurches:
        'palaces and churches of the west of the region by car',
      synagoguesDay: 'the synagogues of Grodno, Lida and Slonim by car',
      castlesHalfDay: 'the castles of the Grodno region in half a day',
      museumsBudget: 'Grodno museums for two hours',
      familyWalk:
        'old Grodno, two children, two hours, toilet required, cafe if along the way',
      avoidTemples:
        'a family walk in Grodno without churches or cathedrals: park and zoo',
      namedFarnyi: 'a walk in old Grodno with a stop at the Farny church',
      freeOldTown:
        'Grodno for free: only places with free admission, three hours',
      allChurchesCatalogue:
        'every catholic and orthodox church of the Grodno region',
      vagueNoAnchor: 'something interesting',
      outOfRegionVilnius: 'Vilnius: the cathedral — a walk on foot',
    },
    chipsRefine: {
      addCafeToilet: 'add a coffee shop and a toilet',
      addCafe: 'add a café on the way',
      removeMuseum: 'take the museum out',
      shorter: 'make it shorter — two hours',
      onlyChurches: 'keep only churches and castles',
      withChildren: 'add something for the children',
    },
  },
  actions: {
    closePanel: 'close the panel',
    build: 'Plan my route',
    cancel: 'Cancel',
    delete: 'Delete',
    close: 'Close',
  },
  guide: {
    enter: 'Walk this route',
    enterHint: 'The guide marks what you have passed and calls the turns',
    title: 'Guide',
    exit: 'exit',
    stops_one: '{{count}} stop',
    stops_few: '{{count}} stops',
    stops_many: '{{count}} stops',
  },
  itineraries: {
    loading: 'Loading ready-made routes…',
    loadFailed:
      'Could not load the ready-made routes. The routes themselves are fine — the agent did not answer.',
    retry: 'retry',
    emptyList: 'No ready-made routes yet.',
    intro:
      'Put together by hand from real data points: they open right away with no model request — the route can be edited as usual afterwards.',
    incompleteCount:
      'Some points are missing from the data ({{count}}) — those routes are shown incomplete.',
    empty: 'Ready-made routes did not load',
    incomplete: 'One stop of this route is missing from the data',
    open: 'show on the map',
    stops: 'stops',
    visit: 'visit',
    alongTheWay: 'along the way',
    onFoot: 'on foot',
    byCar: 'by car',
  },
  language: {
    label: 'Interface language',
    ru: 'Русский',
    en: 'English',
  },
};
