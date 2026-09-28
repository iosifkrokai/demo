import type { Dictionary } from './ru';

/**
 * English strings.
 *
 * Typed as `Dictionary`, so a key added to the Russian source of truth is a
 * compile error here until it is translated — the UI cannot silently fall back
 * to Russian mid-screen.
 */
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
    history: 'History',
  },
  tabSubtitles: {
    plan: 'Describe what you want — I will build a route on real roads',
    itineraries: 'Ready-made routes — start from one of them',
    history: 'Routes you have already built',
  },
  ask: {
    placeholderRefine: 'What to refine? “add a coffee shop”',
    placeholder: 'What would you like to see?',
    label: 'What would you like to see',
    chips: {
      // A chip *is* the query — it goes to the agent as written.
      oldTown: 'Old town in two hours on foot',
      castlesChurches: 'Castles and churches of Grodno',
      food: 'Where to eat in the centre, on a budget',
      evening: 'An evening walk along Sovetskaya',
      withChildren: 'With children: parks and castles',
    },
    // Offered only once a route exists: instructions that change it, not fresh
    // requests. The pair must stay in step with the Russian chips.
    chipsRefine: {
      addCafe: 'add a café on the way',
      removeMuseum: 'take the museum out',
      shorter: 'make it shorter — two hours',
      onlyChurches: 'keep only churches and castles',
      withChildren: 'add something for the children',
    },
  },
  transport: {
    pedestrian: 'on foot',
    bicycle: 'by bike',
    auto: 'by car',
    any: 'any way',
  },
  budget: {
    '30': '30 min',
    '60': '1 hr',
    '120': '2 hrs',
    '240': 'half a day',
    any: 'no limit',
  },
  actions: {
    closePanel: 'close the panel',
    build: 'Plan my route',
    cancel: 'Cancel',
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
  history: {
    empty: 'Nothing yet — routes you build will show up here',
    walked: 'walked',
    walkedPartial: '{{visited}} of {{total}} done',
    restore: 'back on the map',
    clear: 'Clear history',
    remove: 'Remove from history',
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
