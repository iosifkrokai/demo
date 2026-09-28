/**
 * Демонстрационное видео: запросы, которые действительно дают складный маршрут.
 *
 * Учтено то, на чём спотыкались прошлые записи:
 *  - браузер запускается с флагами против троттлинга таймеров неактивной вкладки,
 *    иначе проход замирает через полминуты;
 *  - пишем против собранной версии в контейнере (http://localhost), а не против
 *    дев-сервера Vite: его HMR перезагружает страницу посреди прогона;
 *  - подпись называет результат числами и именами, взятыми из ответа агента:
 *    список остановок в панели уходит ниже экрана;
 *  - проводник показывается на повторном заходе с ?sim=walk, потому что
 *    симуляция читает флаг из адреса при загрузке, а маршрут живёт в памяти.
 *
 * Запуск: APP_URL=http://localhost OUT_DIR=/workspaces/demo/ui/demo node scripts/demo-video.mjs
 */
import { chromium } from 'playwright';
import { mkdirSync, writeFileSync } from 'node:fs';

const OUT = process.env.OUT_DIR || '/workspaces/demo/ui/demo';
const BASE = process.env.APP_URL || 'http://localhost';
mkdirSync(OUT, { recursive: true });

const log = [];
const note = (line) => {
  log.push(`${new Date().toISOString().slice(11, 19)}  ${line}`);
  console.log(log.at(-1));
};

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

/** Подпись поверх страницы: без неё на видео непонятно, что происходит. */
const caption = async (text) => {
  await page.evaluate((value) => {
    let el = document.getElementById('demo-caption');
    if (!el) {
      el = document.createElement('div');
      el.id = 'demo-caption';
      el.style.cssText = [
        'position:fixed',
        'left:50%',
        'bottom:20px',
        'transform:translateX(-50%)',
        'z-index:99999',
        'background:rgba(17,24,39,.9)',
        'color:#fff',
        'padding:12px 20px',
        'border-radius:14px',
        'font:600 16px/1.4 system-ui,sans-serif',
        'max-width:76vw',
        'text-align:center',
        'white-space:pre-line',
        'box-shadow:0 10px 30px rgba(0,0,0,.4)',
      ].join(';');
      document.body.appendChild(el);
    }
    el.textContent = value;
  }, text);
  note(`подпись: ${text.replace(/\n/g, ' | ')}`);
};

const shot = (name) => page.screenshot({ path: `${OUT}/${name}.png` });

const openApp = async (search) => {
  await page.goto(`${BASE}/directions${search}`, { waitUntil: 'domcontentloaded' });
  await page.getByRole('textbox').first().waitFor({ state: 'visible', timeout: 30_000 });
};

/** Поля ответа, по которым делается вывод, — прямо из ответа агента. */
const summarise = (body) => {
  const points = body?.points ?? [];
  const summary = body?.summary ?? {};
  return {
    status: body?.status ?? '',
    count: points.length,
    km: Number(summary.length_km ?? 0),
    minutes: Math.round(Number(summary.time_seconds ?? 0) / 60),
    names: points.map((p) => p?.name).filter(Boolean),
  };
};

/**
 * Один запрос на камеру: набрать, дождаться, показать карту и подписать числа.
 */
const showcase = async (index, query, { minutes = null } = {}) => {
  const field = page.getByRole('textbox').first();
  await caption(`${index}. Запрос: «${query}»${minutes ? ` · есть ${minutes / 60} ч` : ''}`);
  await field.fill(query);
  if (minutes) {
    const chip = page.locator(`[data-testid="budget-${minutes}"]`).first();
    if (await chip.count()) {
      await chip.click();
    } else {
      const byText = page.getByRole('button', { name: new RegExp(`^${minutes / 60}\\s*(ч|hrs?)`, 'i') }).first();
      if (await byText.count()) await byText.click();
      else note(`чип бюджета ${minutes} не найден — отправляю без него`);
    }
  }
  const [response] = await Promise.all([
    page.waitForResponse(
      (r) => r.url().includes('/routes/generate') && r.request().method() === 'POST',
      { timeout: 240_000 }
    ),
    page.click('[data-testid="build-route"]'),
  ]);

  let info = { status: '', count: 0, km: 0, minutes: 0, names: [] };
  try {
    info = summarise(await response.json());
  } catch {
    note(`ответ ${response.status()} не разобран как JSON`);
  }
  // Ждём, пока панель признает маршрут готовым и карта дорисует линию.
  await page.waitForSelector('[data-testid="guide-enter"]', { state: 'visible', timeout: 240_000 });
  await page.waitForTimeout(6_000);

  await caption(
    `${index}. «${query}»\n` +
      `остановок ${info.count} · ${info.km.toFixed(2)} км · ${info.minutes} мин` +
      (info.names.length ? `\n${info.names.join(' · ')}` : '')
  );
  note(
    `${query} → статус ${info.status}, остановок ${info.count}, ${info.km.toFixed(2)} км, ${info.minutes} мин`
  );
  await shot(`0${index}-${query.slice(0, 18).replace(/\s+/g, '_')}`);
  await page.waitForTimeout(7_000);
};

try {
  // ── Часть 1: запросы ──────────────────────────────────────────────────────
  await openApp('?profile=pedestrian');
  await caption('Гид по Гродно: прошу маршрут словами — приложение строит его по дорогам');
  await page.waitForTimeout(2_500);

  // Только там, где короткий ответ честно правильный: разбор запроса сейчас
  // вытаскивает одну обязательную точку и ни одного интереса, поэтому широкая
  // просьба возвращает две остановки, а «всё главное» уходит в отказ.
  await showcase(1, 'Фарный костёл и Новый замок', { minutes: 120 });
  await showcase(2, 'Музеи Гродно', { minutes: 120 });
  await showcase(3, 'С детьми: парки и замки', { minutes: 180 });

  // ── Часть 2: проводник ────────────────────────────────────────────────────
  // Симуляция читает ?sim=walk при загрузке, поэтому заходим заново и строим
  // маршрут уже внутри этого захода.
  await openApp('?profile=pedestrian&sim=walk&sim-speed=12');
  await caption('Режим проводника: положение туриста проигрывается по маршруту — телефон не нужен');
  const field = page.getByRole('textbox').first();
  await field.fill('Фарный костёл и Новый замок');
  await page.waitForTimeout(500);
  await page.click('[data-testid="build-route"]');
  await page.waitForSelector('[data-testid="guide-enter"]', { state: 'visible', timeout: 240_000 });
  await page.waitForTimeout(3_000);
  await page.click('[data-testid="guide-enter"]');
  await page.waitForTimeout(4_000);
  await caption('Ведёт по остановкам: пройденное гаснет, до следующей — метры и минуты');
  for (let step = 0; step < 14; step += 1) {
    await page.waitForTimeout(4_000);
    if (step === 7) {
      await caption('Карта сама держит туриста в кадре и доворачивается по курсу');
    }
    if (step === 11) await shot('10-guide');
  }
  await caption('Отметки остановок ставятся на месте — и запоминаются');
  await page.waitForTimeout(5_000);

  // ── Часть 3: уточнение текстом ─────────────────────────────────────────────
  const exit = page.locator('[data-testid="guide-exit"]').first();
  if (await exit.count()) {
    await exit.click();
    await page.waitForTimeout(2_500);
  }
  await caption('Дальше маршрут можно уточнить словами, а не строить заново');
  const refineField = page.getByRole('textbox').first();
  await refineField.fill('добавь кафе по пути');
  const [refined] = await Promise.all([
    page.waitForResponse(
      (r) => r.url().includes('/routes/generate') && r.request().method() === 'POST',
      { timeout: 240_000 }
    ),
    page.click('[data-testid="build-route"]'),
  ]);
  let after = { status: '', count: 0, km: 0, minutes: 0, names: [] };
  try {
    after = summarise(await refined.json());
  } catch {
    note('ответ уточнения не разобран как JSON');
  }
  await page.waitForTimeout(5_000);
  await caption(
    `Уточнение: «добавь кафе по пути»\nстало остановок ${after.count}` +
      (after.names.length ? `\n${after.names.join(' · ')}` : '')
  );
  note(`уточнение → остановок ${after.count}`);
  await shot('11-refined');
  await page.waitForTimeout(6_000);

  await caption('Одно нажатие возвращает предыдущий маршрут');
  const undo = page.locator('button:has(svg.lucide-undo-2)').first();
  if (await undo.count()) {
    await undo.click();
    await page.waitForTimeout(5_000);
  } else {
    note('кнопка отмены не найдена');
  }
  await shot('12-undone');
  await page.waitForTimeout(4_000);

  await caption('Готово.');
  await page.waitForTimeout(3_000);
} catch (error) {
  note(`СБОЙ: ${error}`);
  await shot('99-failure');
}

const video = page.video();
const videoPath = video ? await video.path() : null;
await context.close();
await browser.close();
writeFileSync(`${OUT}/demo.log`, log.join('\n'), 'utf8');

console.log(`\nВИДЕО: ${videoPath}`);
console.log(`ЖУРНАЛ: ${OUT}/demo.log`);
