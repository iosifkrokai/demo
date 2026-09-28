/**
 * Записать возможности правки маршрута.
 *
 * Сценарий идёт по реальным действиям интерфейса и подписывает каждый шаг
 * поверх страницы: без подписи на видео не понять, что произошло.
 *
 * Запуск: APP_URL=http://localhost node scripts/route-edit-video.mjs
 */
import { chromium } from 'playwright';
import { mkdirSync, writeFileSync } from 'node:fs';

const OUT = process.env.OUT_DIR || '/workspaces/demo/ui/edit';
const BASE = process.env.APP_URL || 'http://localhost';
const QUERY = 'Все главные достопримечательности Гродно';

mkdirSync(OUT, { recursive: true });

const browser = await chromium.launch({
  headless: true,
  args: [
    '--disable-background-timer-throttling',
    '--disable-backgrounding-occluded-windows',
    '--disable-renderer-backgrounding',
  ],
});
const context = await browser.newContext({
  viewport: { width: 1280, height: 800 },
  recordVideo: { dir: OUT, size: { width: 1280, height: 800 } },
  locale: 'ru-RU',
});
const page = await context.newPage();
const log = [];
const note = (line) => {
  log.push(`${new Date().toISOString().slice(11, 19)}  ${line}`);
  console.log(log.at(-1));
};

/** Подпись поверх страницы: что именно сейчас показывается. */
const caption = async (text) => {
  await page.evaluate((value) => {
    let el = document.getElementById('demo-caption');
    if (!el) {
      el = document.createElement('div');
      el.id = 'demo-caption';
      el.style.cssText = [
        'position:fixed',
        'left:50%',
        'bottom:22px',
        'transform:translateX(-50%)',
        'z-index:99999',
        'background:rgba(17,24,39,.88)',
        'color:#fff',
        'padding:10px 18px',
        'border-radius:14px',
        'font:600 15px/1.35 system-ui,sans-serif',
        'max-width:78vw',
        'text-align:center',
        'box-shadow:0 8px 24px rgba(0,0,0,.35)',
      ].join(';');
      document.body.appendChild(el);
    }
    el.textContent = value;
  }, text);
  note(`подпись: ${text}`);
};

const shot = (name) => page.screenshot({ path: `${OUT}/${name}.png` });

await page.goto(`${BASE}/directions?profile=pedestrian`, { waitUntil: 'domcontentloaded' });
const field = page.getByRole('textbox').first();
await field.waitFor({ state: 'visible', timeout: 30_000 });

// ── 1. строим маршрут ───────────────────────────────────────────────────────
await caption('1. Собираем маршрут запросом');
await field.fill(QUERY);
const threeHours = page.getByRole('button', { name: /^3\s*(ч|hrs?|hours?)$/i }).first();
if (await threeHours.count()) await threeHours.click();
await page.click('[data-testid="build-route"]');
await page.waitForSelector('[data-testid="guide-enter"]', { state: 'visible', timeout: 240_000 });
await page.waitForTimeout(4_000);
const stopsText = async () =>
  page.evaluate(() => {
    const t = document.querySelector('[data-testid="guide-enter"]')
      ? document.body.innerText
      : '';
    const m = t.match(/останов\w*\s*\|?\s*(\d+)|(\d+)\s+остановок/i);
    return m ? (m[1] ?? m[2]) : '?';
  });
note(`маршрут построен (остановок по подписи кнопки: ${await stopsText()})`);
await shot('01-route');

// ── 2. перетаскивание остановки ────────────────────────────────────────────
await caption('2. Остановку можно перетащить по карте — маршрут перестроится');
const markers = page.locator('.maplibregl-marker');
const markerCount = await markers.count();
note(`маркеров на карте: ${markerCount}`);
if (markerCount > 1) {
  const target = markers.nth(1);
  const box = await target.boundingBox();
  if (box) {
    await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
    await page.mouse.down();
    await page.mouse.move(box.x + box.width / 2 + 150, box.y + box.height / 2 + 120, {
      steps: 25,
    });
    await page.mouse.up();
    note('маркер перетащен на ~190 м');
    await page.waitForTimeout(8_000); // пересчёт маршрута
    await shot('02-dragged');
  }
}

// ── 3. добавление остановки по названию ────────────────────────────────────
await caption('3. Добавляем остановку по названию');
const addField = page.getByLabel('добавить точку в маршрут').first();
if (await addField.count()) {
  await addField.fill('Коложская церковь');
  await page.getByLabel('найти и добавить точку').first().click();
  await page.waitForTimeout(9_000);
  await shot('03-added-by-name');
  note('остановка добавлена по названию');
} else {
  note('поле добавления точки не найдено');
}

// ── 4. добавление остановки прямо на карте ─────────────────────────────────
await caption('4. Или ткнуть точку прямо на карте');
const emptyStop = page.getByText(/пустая точка/i).first();
if (await emptyStop.count()) {
  await emptyStop.click();
  await page.mouse.click(900, 500);
  await page.waitForTimeout(9_000);
  await shot('04-added-on-map');
  note('остановка добавлена на карте');
} else {
  note('кнопка пустой остановки не найдена');
}

// ── 5. смена транспорта ────────────────────────────────────────────────────
await caption('5. Меняем способ передвижения — маршрут пересчитывается заново');
const car = page.locator('[data-testid="transport-car"]').first();
if (await car.count()) {
  await car.click();
  await page.waitForTimeout(10_000);
  await shot('05-car');
  note('транспорт переключён на машину');
} else {
  note('переключатель транспорта не найден');
}

// ── 6. проводник: время на остановке и отметки руками ──────────────────────
await caption('6. В проводнике — время на остановку и отметки руками');
await page.waitForSelector('[data-testid="guide-enter"]', { state: 'visible', timeout: 60_000 });
await page.click('[data-testid="guide-enter"]');
await page.waitForSelector('[data-testid="guide-panel"]', { state: 'visible', timeout: 30_000 });
await page.waitForTimeout(2_000);
const chip = page.locator('[data-testid="visit-time-chip"]').first();
if (await chip.count()) {
  await chip.click();
  await page.waitForTimeout(800);
  const stepper = await page.evaluate(() =>
    [...document.querySelectorAll('button')]
      .map((b) => (b.getAttribute('aria-label') || b.textContent || '').replace(/\s+/g, ' ').trim())
      .filter((label) => /прибав|убав|плюс|минус|\+|-/.test(label))
      .slice(0, 8)
  );
  note(`шагомер времени: ${JSON.stringify(stepper)}`);
  const plus = page.getByLabel(/прибав/i).first();
  if (await plus.count()) {
    await plus.click();
    await plus.click();
    note('время осмотра увеличено на две ступени');
  }
}
await page.waitForTimeout(2_500);
await shot('06-guide-time');
const rows = page.locator('[data-testid="guide-panel"] button, [data-testid="guide-panel"] [role="button"]');
note(`интерактивных элементов в проводнике: ${await rows.count()}`);
await caption('7. Остановку можно отметить пройденной вручную — тапом по строке');
if (await rows.count()) {
  await rows.nth(Math.min(6, (await rows.count()) - 1)).click().catch(() => {});
  await page.waitForTimeout(2_000);
  await shot('07-marked');
}

const video = page.video();
const videoPath = await (async () => {
  try {
    return video ? await video.path() : null;
  } finally {
    await context.close();
    await browser.close();
    writeFileSync(`${OUT}/edit.log`, log.join('\n'), 'utf8');
  }
})();

console.log(`\nВИДЕО: ${videoPath}`);
console.log(`ЖУРНАЛ: ${OUT}/edit.log`);
