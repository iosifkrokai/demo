/**
 * Записать проход по маршруту в режиме проводника: симуляция GPS проигрывает
 * положение вдоль остановок, Playwright пишет экран в webm.
 *
 * Запуск: node /tmp/walk-video.mjs  (из frontend/, чтобы нашёлся playwright)
 */
import { chromium } from 'playwright';
import { mkdirSync, writeFileSync } from 'node:fs';

const OUT = process.env.OUT_DIR || '/tmp/walk-video';
// По умолчанию — собранное приложение в контейнере; APP_URL=http://localhost:5173
// позволяет записать то же с дев-сервера, не трогая открытое у пользователя окно.
const BASE = process.env.APP_URL || 'http://localhost';
const QUERY = 'Все главные достопримечательности Гродно';
// Десятикратная скорость: проход виден, но укладывается в полторы минуты.
const SPEED = 10;
const WALK_MS = 240_000;

mkdirSync(OUT, { recursive: true });

// Без этих флагов Chromium душит таймеры неактивной вкладки до одного в минуту:
// проход замирает через полминуты, и на видео «симуляция» стоит на месте.
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

// Панель и карта видны целиком; положение проигрывается, а не читается с телефона.
await page.goto(`${BASE}/directions?profile=pedestrian&sim=walk&sim-speed=${SPEED}`, {
  waitUntil: 'domcontentloaded',
});
note(`открыто: ${page.url()}`);

const field = page.getByRole('textbox').first();
await field.waitFor({ state: 'visible', timeout: 30_000 });
await field.fill(QUERY);

// Бюджет времени — часть запроса: без него тот же текст даёт мусорный маршрут
// (11 остановок на 12,5 км, с казармами и могилами), с тремя часами — нормальные
// пять достопримечательностей.
const threeHours = page.getByRole('button', { name: /^3\s*(ч|hrs?|hours?)$/i }).first();
if (await threeHours.count()) {
  await threeHours.click();
  note('выбран бюджет времени: 3 часа');
}
note(`запрос вписан: ${QUERY}`);

await page.click('[data-testid="build-route"]');
note('нажато «построить маршрут»');

// Маршрут из кеша приходит за секунды, без кеша — за десятки: ждём с запасом.
await page.waitForSelector('[data-testid="guide-enter"]', { state: 'visible', timeout: 240_000 });
const stops = await page.locator('[data-testid^="stop-"]').allInnerTexts();
note(`маршрут готов, остановок в списке: ${stops.length}`);
for (const [i, s] of stops.entries()) note(`  ${i + 1}. ${s.split('\n')[0]}`);

// Проводник включён — дальше панель должна идти по маршруту сама.
await page.click('[data-testid="guide-enter"]');
await page.waitForSelector('[data-testid="guide-panel"]', { state: 'visible', timeout: 30_000 });
const badge = await page.locator('[data-testid="guide-simulated"]').count();
note(`проводник открыт, пометка о симуляции: ${badge ? 'есть' : 'НЕТ'}`);

const snapshot = async (label) => {
  const panel = await page.locator('[data-testid="guide-panel"]').innerText();
  const one = panel.replace(/\s*\n\s*/g, ' | ').slice(0, 220);
  note(`${label}: ${one}`);
};

const started = Date.now();
let step = 0;
let finishedAt = null;
while (Date.now() - started < WALK_MS) {
  await page.waitForTimeout(10_000);
  step += 1;
  const progress = await page.evaluate(() => {
    const t = document.querySelector('[data-testid="guide-panel"]')?.innerText ?? '';
    const m = t.match(/пройдено\s+(\d+)\s+из\s+(\d+)/i);
    return m ? { walked: Number(m[1]), total: Number(m[2]) } : null;
  });
  if (progress && progress.total > 0 && progress.walked === progress.total) {
    finishedAt = Math.round((Date.now() - started) / 1000);
    await page.screenshot({ path: `${OUT}/step-${String(step).padStart(2, '0')}-finish.png` });
    break;
  }
  const sim = await page.evaluate(() => {
    const g = window.__geoSim;
    return g ? { путь: g.pathLength(), пройдено_м: Math.round(g.travelled()) } : null;
  });
  note(`${Math.round((Date.now() - started) / 1000)} с · симуляция: ${JSON.stringify(sim)}`);
  await snapshot(`${Math.round((Date.now() - started) / 1000)} с`);
  await page.screenshot({ path: `${OUT}/step-${String(step).padStart(2, '0')}.png` });
}

note(finishedAt === null ? 'проход не завершился за отведённое время' : `маршрут пройден за ${finishedAt} с`);

const video = page.video();
const videoPath = await finish(video);

async function finish(handle) {
  // Видео пишется, пока жив контекст: закрываем в finally, иначе падение
  // сценария оставляет недописанный webm.
  try {
    return handle ? await handle.path() : null;
  } finally {
    await context.close();
    await browser.close();
    writeFileSync(`${OUT}/panel.log`, log.join('\n'), 'utf8');
  }
}

console.log(`\nВИДЕО: ${videoPath}`);
console.log(`ЖУРНАЛ: ${OUT}/panel.log`);
