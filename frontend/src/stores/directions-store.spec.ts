import { beforeEach, describe, expect, it } from 'vitest';
import {
  ME_WAYPOINT_ID,
  type PlaceDetails,
  useDirectionsStore,
  type Waypoint,
} from './directions-store';

const stop = (id: string, placeId: number, name: string): Waypoint => ({
  id,
  userInput: name,
  geocodeResults: [
    {
      title: name,
      displaylnglat: [23.83, 53.67],
      sourcelnglat: [23.83, 53.67],
      key: Number(id),
      addressindex: 0,
      selected: true,
    },
  ],
  placeId,
});

const details = (name: string): PlaceDetails => ({
  name,
  category: 'кафе',
  blurb: null,
  funFact: null,
  funFacts: [],
  links: [],
  visitMinutes: 40,
});

const withRoute = (waypoints: Waypoint[]) =>
  useDirectionsStore.setState({
    waypoints,
    placeDetails: { 10: details('Кафе Немо'), 20: details('Туалет') },
    excludedPlaceIds: [],
    refinementLog: [],
    routeSnapshots: [],
    successful: true,
    results: { data: null, show: { '0': true } },
  });

describe('directions-store — refinement context', () => {
  beforeEach(() => {
    withRoute([
      stop(ME_WAYPOINT_ID, -1, 'моё местоположение'),
      stop('1', 10, 'Кафе Немо'),
      stop('2', 20, 'Туалет'),
    ]);
    useDirectionsStore.setState({
      waypoints: useDirectionsStore
        .getState()
        .waypoints.map((wp) => ({ ...wp, pinned: false })),
    });
  });

  it('remembers a hand-deleted stop so a refinement cannot bring it back', () => {
    useDirectionsStore.getState().excludeStops({ placeIds: [20] });

    const state = useDirectionsStore.getState();
    expect(state.waypoints.map((wp) => wp.placeId)).toEqual([-1, 10]);
    expect(state.excludedPlaceIds).toEqual([20]);
  });

  it('drops the exclusion when the user brings the stop back', () => {
    useDirectionsStore.getState().excludeStops({ placeIds: [20] });
    useDirectionsStore.getState().includeStop({ placeId: 20 });

    expect(useDirectionsStore.getState().excludedPlaceIds).toEqual([]);
  });

  it('keeps the "my location" start and never treats it as an excluded stop', () => {
    useDirectionsStore.getState().excludeStops({ placeIds: [10] });

    const state = useDirectionsStore.getState();
    expect(state.waypoints[0]?.id).toBe(ME_WAYPOINT_ID);
    expect(state.excludedPlaceIds).toEqual([10]);
  });

  it('undoes a refinement and restores stops, details and exclusions', () => {
    const store = useDirectionsStore.getState();
    store.snapshotRoute();
    store.excludeStops({ placeIds: [20] });
    store.pushRefinement({
      instruction: 'убери туалет',
      added: [],
      removed: ['Туалет'],
    });

    expect(useDirectionsStore.getState().waypoints).toHaveLength(2);

    useDirectionsStore.getState().undoRefinement();

    const state = useDirectionsStore.getState();
    expect(state.waypoints.map((wp) => wp.placeId)).toEqual([-1, 10, 20]);
    expect(state.excludedPlaceIds).toEqual([]);
    expect(state.refinementLog).toEqual([]);
    expect(state.routeSnapshots).toEqual([]);
  });

  it('undo is a no-op without a snapshot', () => {
    const before = useDirectionsStore.getState().waypoints;
    useDirectionsStore.getState().undoRefinement();

    expect(useDirectionsStore.getState().waypoints).toEqual(before);
  });

  it('keeps the refinement log in order with the newest last', () => {
    const store = useDirectionsStore.getState();
    store.pushRefinement({
      instruction: 'добавь кофейню',
      added: ['Кафе Немо'],
      removed: [],
    });
    store.pushRefinement({
      instruction: 'и туалет',
      added: ['Туалет'],
      removed: [],
    });

    const log = useDirectionsStore.getState().refinementLog;
    expect(log.map((e) => e.instruction)).toEqual([
      'добавь кофейню',
      'и туалет',
    ]);
    expect(log[1]?.added).toEqual(['Туалет']);
    expect(log[0]?.id).toBeTruthy();
  });

  it('reset clears stops, details, exclusions, log and undo history', () => {
    const store = useDirectionsStore.getState();
    store.snapshotRoute();
    store.excludeStops({ placeIds: [10] });
    store.pushRefinement({
      instruction: 'убери кафе',
      added: [],
      removed: ['Кафе Немо'],
    });

    useDirectionsStore.getState().resetRoute();

    const state = useDirectionsStore.getState();
    expect(state.waypoints).toHaveLength(2);
    expect(state.waypoints.every((wp) => wp.geocodeResults.length === 0)).toBe(
      true
    );
    expect(state.placeDetails).toEqual({});
    expect(state.excludedPlaceIds).toEqual([]);
    expect(state.refinementLog).toEqual([]);
    expect(state.routeSnapshots).toEqual([]);
    expect(state.successful).toBe(false);
  });
});

describe('история: что было пройдено', () => {
  const planned = (routeKey: string, query = 'два замка пешком') => ({
    query,
    timeBudget: 120,
    routeKey,
    places: [
      {
        id: 1,
        name: 'Старый замок',
        category: 'замок',
        lat: 53.677,
        lon: 23.83,
      },
    ],
  });

  const history = () => useDirectionsStore.getState().routeHistory;

  beforeEach(() => {
    useDirectionsStore.getState().clearHistory();
  });

  it('отмечает пройденным тот маршрут, который шли, и только его', () => {
    const store = useDirectionsStore.getState();
    store.addToHistory(planned('a@1,2'));
    store.addToHistory(planned('b@3,4', 'коложский парк'));

    const walkedKey = history()[1]!.routeKey!;
    useDirectionsStore.getState().markWalked({
      routeKey: walkedKey,
      visited: 1,
      total: 2,
    });

    const [other, walked] = history();
    expect(walked!.walk).toMatchObject({
      visited: 1,
      total: 2,
      completed: false,
    });
    expect(other!.walk).toBeUndefined();
  });

  it('считает маршрут пройденным только когда посещены все точки', () => {
    const store = useDirectionsStore.getState();
    store.addToHistory(planned('a@1,2'));
    const routeKey = history()[0]!.routeKey!;

    useDirectionsStore.getState().markWalked({ routeKey, visited: 1, total: 2 });
    expect(history()[0]!.walk?.completed).toBe(false);

    useDirectionsStore.getState().markWalked({ routeKey, visited: 2, total: 2 });
    expect(history()[0]!.walk).toMatchObject({ visited: 2, completed: true });
  });

  it('ничего не выдумывает для маршрута, которого нет в истории', () => {
    const store = useDirectionsStore.getState();
    store.addToHistory(planned('a@1,2'));

    useDirectionsStore.getState().markWalked({
      routeKey: 'чужая@9,9',
      visited: 2,
      total: 2,
    });

    expect(history()).toHaveLength(1);
    expect(history()[0]!.walk).toBeUndefined();
  });

  it('сохраняет пройденное, когда тот же маршрут строят заново', () => {
    const store = useDirectionsStore.getState();
    store.addToHistory(planned('a@1,2'));
    const routeKey = history()[0]!.routeKey!;
    useDirectionsStore.getState().markWalked({ routeKey, visited: 2, total: 2 });

    useDirectionsStore.getState().addToHistory(planned('a@1,2'));

    expect(history()).toHaveLength(1);
    expect(history()[0]!.walk).toMatchObject({ visited: 2, completed: true });
  });

  it('начинает прогулку с нуля, если новый маршрут другой', () => {
    const store = useDirectionsStore.getState();
    store.addToHistory(planned('a@1,2'));
    useDirectionsStore.getState().markWalked({
      routeKey: 'a@1,2',
      visited: 2,
      total: 2,
    });

    useDirectionsStore.getState().addToHistory(planned('b@3,4'));

    expect(history()).toHaveLength(1);
    expect(history()[0]!.walk).toBeUndefined();
  });
});
