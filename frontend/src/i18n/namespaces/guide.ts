/**
 * One locale area lives in one file: `ru` and `en` side by side.
 *
 * The point is ownership. A single shared dictionary meant two people editing
 * the same two files for unrelated screens; an area file plus the components of
 * that area is a self-contained piece of work. `src/i18n/index.ts` merges these
 * into the resources i18next gets, and a test asserts `ru`/`en` stay in step —
 * a missing translation is a failing test, not a raw key on screen.
 */
export interface LocaleArea<T = Record<string, unknown>> {
  ru: T;
  en: T;
}

/** Keys of the navigator («Проводник»): stop states, progress, turn phrasing. */
export const guideArea = {
  ru: {
    guide: {
      nextStop: 'следующая остановка',
      arrived: 'вы на месте',
      passed: 'пройдено',
      alongRoute: 'по маршруту',
      alongLine: 'по линии',
      noSignal: 'сигнала нет — идите по линии маршрута',
      start: 'начать маршрут',
      advance: 'я на месте',
      finish: 'завершить',
      again: 'пройти заново',
      stop: 'остановка',
      visitFor: 'на',
      soundOn: 'звук вкл',
      soundOff: 'звук выкл',
      voiceDistance: 'Через {{distance}} метров {{instruction}}',
      voiceArrived: 'Вы прибыли',
    },
  },
  en: {
    guide: {
      nextStop: 'next stop',
      arrived: 'you are here',
      passed: 'done',
      alongRoute: 'along the route',
      alongLine: 'along the line',
      noSignal: 'no signal — follow the route line',
      start: 'start the route',
      advance: 'I am here',
      finish: 'finish',
      again: 'walk it again',
      stop: 'stop',
      visitFor: 'for',
      soundOn: 'sound on',
      soundOff: 'sound off',
      voiceDistance: 'In {{distance}} meters {{instruction}}',
      voiceArrived: 'You have arrived',
    },
  },
} satisfies LocaleArea<{ guide: Record<string, string> }>;
