#!/usr/bin/env node
/**
 * Замер мобильного фронта по числам, а не на глаз — гейт спеки 004
 * (`docs/specs/004-mobile-first-frontend/plan.md` §4).
 *
 * Печатает таблицу «метрика / порог / факт / PASS|FAIL» для 390x844 и 1440x900
 * и выходит с кодом 1, если провалился хотя бы один гейт, уже вступивший в силу.
 * Гейты фаз, до которых работа ещё не дошла, печатаются как «план» (INFO) — это
 * не поблажка: порог не подгоняется под факт, он просто вступает позже.
 *
 * Запуск:  node scripts/measure-mobile.mjs [--phase=1]
 * Адрес:   MEASURE_URL=http://localhost/directions?profile=pedestrian
 */
import { chromium } from '@playwright/test';

const PHASE = Number(
  (process.argv.find((a) => a.startsWith('--phase=')) ?? '--phase=1').split(
    '='
  )[1]
);
const TARGET_URL =
  process.env.MEASURE_URL ?? 'http://localhost/directions?profile=pedestrian';

const MOBILE_VIEWPORT = {
  viewport: { width: 390, height: 844 },
  deviceScaleFactor: 3,
  isMobile: true,
  hasTouch: true,
};
const DESKTOP_VIEWPORT = {
  viewport: { width: 1440, height: 900 },
  deviceScaleFactor: 1,
};
/** Somewhere in Grodno, so route generation is possible when a phase needs it. */
const GEO = { latitude: 53.680842, longitude: 23.816545 };

/** Пороги по фазам: `phase` — с какой фазы гейт обязателен. */
const GATES = [
  {
    id: 'shell',
    phase: 1,
    label: 'мобильная панель — это шелл, а не десктопный лист',
    want: 'data-testid=mobile-sheet есть',
  },
  {
    id: 'main-action',
    phase: 1,
    label: 'главное действие видно без прокрутки',
    want: 'кнопка внутри панели и внутри экрана',
  },
  {
    id: 'peek-panel',
    phase: 1,
    label: 'панель в покое не занимает пол-экрана',
    want: '<= 50dvh',
  },
  {
    id: 'no-overflow-x',
    phase: 1,
    label: 'горизонтального переполнения нет',
    want: '0 px',
  },
  {
    id: 'scroll-window',
    phase: 2,
    label: 'окно прокрутки формы',
    want: '>= 40 px',
  },
  {
    id: 'tap-target',
    phase: 2,
    label: 'минимальная мишень',
    want: '>= 40 px по меньшей стороне',
  },
  {
    id: 'map-share-planning',
    phase: 3,
    label: 'видимая карта в планировании',
    want: '>= 60 %',
  },
  {
    id: 'map-chrome',
    phase: 5,
    label: 'площадь плавающего хрома над картой',
    want: '<= 8 % видимой карты',
  },
  {
    id: 'desktop-panel',
    phase: 1,
    label: 'десктопная панель не изменилась',
    want: '420 px, подсказки переносятся, заголовок виден',
  },
];

const measureMobile = async (context) => {
  const page = await context.newPage();
  await page.goto(TARGET_URL, { waitUntil: 'load' });
  await page.waitForTimeout(4000);

  const closedByDefault =
    (await page.locator('[data-testid="mobile-sheet"]').count()) === 0;
  // On a phone the panel starts folded by design (`DEFAULT_PANEL_OPEN`): the map
  // is the first thing the tourist sees. Open it the way they would — the button
  // on the map. (The desktop chevron on the panel's own edge is `hidden md:flex`,
  // so on a phone it is not there to click.)
  if (closedByDefault) {
    await page.locator('[data-testid="mobile-panel-open"]').click();
    await page.waitForTimeout(1200);
  }

  const measured = await page.evaluate(() => {
    const q = (s) => document.querySelector(s);
    const box = (el) => {
      if (!el) return null;
      const b = el.getBoundingClientRect();
      return {
        x: Math.round(b.left),
        x2: Math.round(b.right),
        y: Math.round(b.top),
        y2: Math.round(b.bottom),
        w: Math.round(b.width),
        h: Math.round(b.height),
      };
    };
    const shell = q('[data-testid="mobile-sheet"]');
    const sb = box(shell);
    const body = q('.slim-scroll');
    const action = box(q('[data-testid="build-route"]'));
    const buttons = [
      ...document.querySelectorAll('[data-testid="mobile-sheet"] button'),
    ]
      .map((el) => box(el))
      .filter((b) => b && b.w > 0 && b.h > 0);
    const taps = buttons.map((b) => Math.min(b.w, b.h));
    // Floating chrome over the map: everything outside the sheet that is not the
    // map canvas itself (controls, summary pill, place label).
    const mapH = sb ? sb.y : window.innerHeight;
    const chrome = [
      ...document.querySelectorAll('[data-testid="map-controls"] > *'),
      // `place-marker-label` is the marker's own caption (place-marker-label.tsx);
      // the old selector said `place-label`, which matched nothing, so the
      // marker plates were never counted against the 8 % budget at all.
      ...document.querySelectorAll(
        '[data-testid="place-marker-label"], [data-testid="mobile-place-card"], [data-testid="mobile-map-chrome"] > *'
      ),
    ]
      .map((el) => box(el))
      .filter(Boolean);
    const chromeArea = chrome.reduce((sum, b) => sum + b.w * b.h, 0);
    const overlaps = [];
    for (let i = 0; i < chrome.length; i++) {
      for (let j = i + 1; j < chrome.length; j++) {
        const a = chrome[i];
        const b = chrome[j];
        const dx = Math.min(a.x2, b.x2) - Math.max(a.x, b.x);
        const dy = Math.min(a.y2, b.y2) - Math.max(a.y, b.y);
        if (dx > 0 && dy > 0) overlaps.push(dx * dy);
      }
    }
    return {
      shellPresent: !!shell,
      panelH: sb ? sb.h : null,
      panelSharePct: sb
        ? Math.round((sb.h / window.innerHeight) * 1000) / 10
        : null,
      mapSharePct: sb
        ? Math.round((sb.y / window.innerHeight) * 1000) / 10
        : 100,
      scrollWindowPx: body ? body.clientHeight : null,
      actionVisible: !!(
        action &&
        sb &&
        action.y >= sb.y - 1 &&
        action.y2 <= sb.y2 + 1 &&
        action.y2 <= window.innerHeight
      ),
      minTapPx: taps.length ? Math.min(...taps) : null,
      chromeSharePct:
        Math.round((chromeArea / (window.innerWidth * mapH)) * 1000) / 10,
      chromeOverlapPx2: overlaps,
      sheetVar: getComputedStyle(document.documentElement)
        .getPropertyValue('--sheet-h')
        .trim(),
      overflowX:
        document.documentElement.scrollWidth -
        document.documentElement.clientWidth,
    };
  });

  await page.close();
  return { ...measured, closedByDefault };
};

const measureDesktop = async (context) => {
  const page = await context.newPage();
  await page.goto(TARGET_URL, { waitUntil: 'load' });
  await page.waitForTimeout(4000);
  const measured = await page.evaluate(() => {
    const panel = document.querySelector('[role="dialog"]');
    const b = panel?.getBoundingClientRect();
    const hints = document.querySelector(
      '[data-testid="hint-old-town"]'
    )?.parentElement;
    const title = [
      ...document.querySelectorAll('[data-slot="sheet-title"]'),
    ][0];
    const tb = title?.getBoundingClientRect();
    return {
      panelW: b ? Math.round(b.width) : null,
      panelH: b ? Math.round(b.height) : null,
      hintsWrap: hints ? getComputedStyle(hints).flexWrap : null,
      titleVisible: !!tb && tb.height > 0,
      overflowX:
        document.documentElement.scrollWidth -
        document.documentElement.clientWidth,
    };
  });
  await page.close();
  return measured;
};

const verdicts = (m, d) => ({
  shell: m.shellPresent,
  'main-action': m.actionVisible,
  'peek-panel': m.panelSharePct !== null && m.panelSharePct <= 50,
  'no-overflow-x': m.overflowX === 0 && d.overflowX === 0,
  'scroll-window': m.scrollWindowPx !== null && m.scrollWindowPx >= 40,
  'tap-target': m.minTapPx !== null && m.minTapPx >= 40,
  'map-share-planning': m.mapSharePct >= 60,
  'map-chrome': m.chromeSharePct <= 8,
  'desktop-panel': d.panelW === 420 && d.hintsWrap === 'wrap' && d.titleVisible,
});

const facts = (m, d) => ({
  shell: m.shellPresent ? 'есть' : 'нет',
  'main-action': m.actionVisible
    ? 'да'
    : `нет (кнопка ${m.actionVisible ? '' : 'обрезана'})`,
  'peek-panel': `${m.panelSharePct} % (${m.panelH} px, --sheet-h ${m.sheetVar})`,
  'no-overflow-x': `${m.overflowX} px`,
  'scroll-window': `${m.scrollWindowPx} px`,
  'tap-target': `${m.minTapPx} px`,
  'map-share-planning': `${m.mapSharePct} %`,
  'map-chrome': `${m.chromeSharePct} %`,
  'desktop-panel': `панель ${d.panelW}x${d.panelH}, wrap=${d.hintsWrap}, заголовок=${d.titleVisible}`,
});

const main = async () => {
  const browser = await chromium.launch();
  const mobile = await measureMobile(
    await browser.newContext({
      ...MOBILE_VIEWPORT,
      permissions: ['geolocation'],
      geolocation: GEO,
      locale: 'ru-RU',
    })
  );
  const desktop = await measureDesktop(
    await browser.newContext(DESKTOP_VIEWPORT)
  );
  await browser.close();

  const v = verdicts(mobile, desktop);
  const f = facts(mobile, desktop);

  console.log(`\nЗамер мобильного фронта · фаза ${PHASE} · ${TARGET_URL}`);
  console.log(
    `панель по умолчанию ${mobile.closedByDefault ? 'свёрнута (как и задумано на телефоне)' : 'открыта'}\n`
  );
  console.log(
    'метрика                       порог                          факт'
  );
  console.log('-'.repeat(96));
  let failed = 0;
  for (const gate of GATES) {
    const enforced = gate.phase <= PHASE;
    const ok = v[gate.id];
    const mark = !enforced ? 'план' : ok ? 'PASS' : 'FAIL';
    if (enforced && !ok) failed += 1;
    console.log(
      `${gate.label.padEnd(30).slice(0, 30)} ${gate.want.padEnd(30).slice(0, 30)} ${String(f[gate.id]).padEnd(26).slice(0, 26)} ${mark}`
    );
  }
  console.log('-'.repeat(96));
  console.log(
    `${failed === 0 ? 'ВСЕ ГЕЙТЫ ФАЗЫ ПРОЙДЕНЫ' : `ПРОВАЛЕНО ГЕЙТОВ: ${failed}`} (в силе с фазы ${PHASE}; остальные — цель)\n`
  );
  process.exit(failed === 0 ? 0 : 1);
};

main().catch((error) => {
  console.error(error);
  process.exit(2);
});
