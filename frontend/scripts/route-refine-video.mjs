/**
 * Записать уточнение маршрута текстом через агента.
 *
 * Панель отправляет такой ход как уточнение (`context.instruction` +
 * `base_points` + `excluded_ids`), а не как новый запрос, поэтому показывает
 * цепочку уточнений и умеет отменить последнее. Сценарий идёт по этому пути и
 * подписывает каждый шаг.
 *
 * Запуск: APP_URL=http://localhost node scripts/route-refine-video.mjs
 */
import { chromium } from 'playwright';
import { mkdirSync, writeFileSync } from 'node:fs';

const OUT = process.env.OUT_DIR || '/workspaces/demo/ui/refine';
const BASE = process.env.APP_URL || 'http://localhost';

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

/** Что панель говорит про уточнения и остановки. */
const readPanel = () =>
  page.evaluate(() => {
    const text = document.body.innerText.replace(/\s*\n\s*/g, ' | ');
    const refined = text.match(/уточнение[^|]*/i);
    const excluded = text.match(/убрано вручную:\s*\d+/i);
    const stops = text.match(/останов\w*\s*\|\s*(\d+)|(\d+)\s+остановок/i);
    return {
      уточнение: refined ? refined[0].slice(0, 90) : null,
      убрано: excluded ? excluded[0] : null,
      остановки: stops ? (stops[1] ?? stops[2]) : null,
    };
  });

await page.goto(`${BASE}/directions?profile=pedestrian`, { waitUntil: 'domcontentloaded' });
const field = page.getByRole('textbox').first();
await field.waitFor({ state: 'visible', timeout: 30_000 });

/** Уточняющий ход: тот же запрос, но маршрут уже есть — панель шлёт refinement. */
const refine = async (instruction) => {
  await field.fill(instruction);
  await caption(`Агент читает уточнение: «${instruction}»`);
  // Ждём сам ответ агента, а не состояние кнопки: панель очищает поле после
  // отправки, кнопка из-за этого остаётся неактивной и ничего не значит.
  const [response] = await Promise.all([
    page.waitForResponse(
      (r) =>
        r.url().includes('/routes/generate') && r.request().method() === 'POST',
      { timeout: 240_000 }
    ),
    page.click('[data-testid="build-route"]'),
  ]);
  try {
    const body = await response.json();
    const names = (body?.points ?? [])
      .map((point) => point?.name)
      .filter(Boolean);
    const difference = body?.refinement ?? body?.context_result ?? null;
    note(
      `агент ответил ${response.status()} (${body?.status}), остановок ${names.length}` +
        (difference ? `, дельта: ${JSON.stringify(difference).slice(0, 200)}` : '')
    );
    // Остановки берём из самого ответа: экран их не показывает целиком, а подпись
    // должна называть, что стало с маршрутом.
    await caption(
      `Уточнение: «${instruction}»\nСтало остановок ${names.length}: ${names.join(' · ')}`
    );
  } catch {
    note(`агент ответил ${response.status()}`);
  }
  await page.waitForTimeout(4_000);
  note(`уточнение «${instruction}» → ${JSON.stringify(await readPanel())}`);
};

// ── 1. первый маршрут ───────────────────────────────────────────────────────
await caption('1. Собираем маршрут — дальше его уточняем текстом, не собираем заново');
await field.fill('Все главные достопримечательности Гродно');
const threeHours = page.getByRole('button', { name: /^3\s*(ч|hrs?|hours?)$/i }).first();
if (await threeHours.count()) await threeHours.click();
await page.click('[data-testid="build-route"]');
await page.waitForSelector('[data-testid="guide-enter"]', { state: 'visible', timeout: 240_000 });
await page.waitForTimeout(3_000);
note(`первый маршрут: ${JSON.stringify(await readPanel())}`);
await shot('01-built');

// ── 2. добавление остановки текстом ─────────────────────────────────────────
await caption('2. «добавь Коложскую церковь» — уточнение, а не новый маршрут');
await refine('добавь Коложскую церковь');
await shot('02-added');

// ── 3. исключение текстом ───────────────────────────────────────────────────
await caption('3. «убери музей» — остановка уходит, и это видно в цепочке');
await refine('убери музей');
await shot('03-removed');

// ── 4. смена бюджета текстом ────────────────────────────────────────────────
await caption('4. «сделай маршрут на два часа» — бюджет меняется тем же разговором');
await refine('сделай маршрут на два часа');
await shot('04-budget');

// ── 5. отмена уточнения ─────────────────────────────────────────────────────
await caption('5. Одно нажатие возвращает предыдущий маршрут');
const undo = page.locator('button:has(svg.lucide-undo-2)').first();
if (await undo.count()) {
  await undo.click();
  await page.waitForTimeout(4_000);
  note(`после отмены: ${JSON.stringify(await readPanel())}`);
  await shot('05-undone');
} else {
  note('кнопка отмены не найдена');
}

const video = page.video();
const videoPath = await (async () => {
  try {
    return video ? await video.path() : null;
  } finally {
    await context.close();
    await browser.close();
    writeFileSync(`${OUT}/refine.log`, log.join('\n'), 'utf8');
  }
})();

console.log(`\nВИДЕО: ${videoPath}`);
console.log(`ЖУРНАЛ: ${OUT}/refine.log`);
