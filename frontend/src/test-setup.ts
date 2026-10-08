import '@testing-library/jest-dom/vitest';

import i18n from './i18n';

// The specs assert the Russian chrome, while jsdom reports navigator.language
// as en-US — pin the language here so the choice of a real tourist's browser
// cannot decide what the tests see.
await i18n.changeLanguage('ru');

global.ResizeObserver = class ResizeObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
};

// jsdom has no layout engine, so `scrollIntoView` is missing. The admin places
// list calls it to bring a map-picked row into view; a no-op is enough here.
Element.prototype.scrollIntoView = () => {};
