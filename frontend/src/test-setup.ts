import '@testing-library/jest-dom/vitest';

import i18n from './i18n';

await i18n.changeLanguage('ru');

global.ResizeObserver = class ResizeObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
};

Element.prototype.scrollIntoView = () => {};
