/**
 * Одно видео на все сценарии: от запроса словами до мобильного вида.
 *
 * Главы идут по возможностям приложения, каждая подписана. Любая глава обёрнута
 * в собственную защиту: если шаг не отработал, это честно попадает и в подпись,
 * и в журнал, а запись продолжается — иначе одна осечка убивает весь прогон.
 *
 * Учтено то, на чём спотыкались прошлые записи: флаги против троттлинга таймеров,
 * запись против собранной версии (не дев-сервера), подписи с числами из ответа
 * агента вместо невидимого списка остановок.
 *
 * Запуск: APP_URL=http://localhost OUT_DIR=/workspaces/demo/ui/all node scripts/all-use-cases-video.mjs
 */
import { chromium } from 'playwright';
import { mkdirSync, writeFileSync } from 'node:fs';

const OUT = process.env.OUT_DIR || '/workspaces/demo/ui/all';
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

/** Глава: подпись, действие, честный итог. Осечка не останавливает запись. */
let chapter = 0;
const withChapter = async (title, body) => {
  chapter += 1;
  const number = String(chapter).padStart(2, '0');
  note(`── глава ${number}: ${title}`);
  try {
    await body(number);
  } catch (error) {
    note(`глава ${number} не отработала: ${error}`);
    await caption(`${title}\nшаг не отработал — см. журнал`).catch(() => {});
    await shot(`${number}-failure`).catch(() => {});
    await page.waitForTimeout(2_500);
  }
};

const field = () => page.getByRole('textbox').first();

const plan = async (query, { minutes = null, transport = null } = {}) => {
  await field().fill(query);
  if (minutes !== null) {
    const chip = page.locator(`[data-testid="budget-${minutes}"]`).first();
    if (await chip.count()) await chip.click();
  }
  if (transport) {
    const chip = page.locator(`[data-testid="transport-${transport}"]`).first();
    if (await chip.count()) await chip.click();
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
    note(`ответ ${response.status()} не разобран`);
  }
  return info;
};

const enterGuide = async () => {
  await page.waitForSelector('[data-testid="guide-enter"]', { state: 'visible', timeout: 240_000 });
  await page.waitForTimeout(2_500);
  await page.click('[data-testid="guide-enter"]');
  await page.waitForTimeout(3_500);
};

const leaveGuide = async () => {
  const exit = page.locator('[data-testid="guide-exit"]').first();
  if (await exit.count()) {
    await exit.click();
    await page.waitForTimeout(2_000);
  }
};

const openApp = async (search = '?profile=pedestrian') => {
  await page.goto(`${BASE}/directions${search}`, { waitUntil: 'domcontentloaded' });
  await page.getByRole('textbox').first().waitFor({ state: 'visible', timeout: 30_000 });
};

// ── 1. Запрос словами ───────────────────────────────────────────────────────
await openApp();
await withChapter('Запрос своими словами', async (number) => {
  await caption('1. Прошу маршрут словами — приложение строит его по дорогам');
  const info = await plan('Фарный костёл и Новый замок', { minutes: 120 });
  await page.waitForTimeout(5_000);
  await caption(
    `1. Маршрут: остановок ${info.count} · ${info.km.toFixed(2)} км · ${info.minutes} мин\n${info.names.join(' · ')}`
  );
  note(`итог: ${info.count} остановок, ${info.km.toFixed(2)} км`);
  await shot(`${number}-plan`);
  await page.waitForTimeout(6_000);
});

// ── 2. Готовые маршруты ─────────────────────────────────────────────────────
await withChapter('Готовые маршруты', async (number) => {
  await caption('2. Есть готовые маршруты — можно начать с них, а не с чистого листа');
  await page.click('[data-testid="mode-itineraries"]');
  await page.waitForTimeout(3_000);
  await shot(`${number}-itineraries`);
  const open = page.locator('[data-testid="itinerary-open-old-town-castles"]').first();
  if (await open.count()) {
    await open.click();
    await page.waitForTimeout(5_000);
    await caption('2. Показываю готовый маршрут «Старый город и замки» на карте');
    await shot(`${number}-itinerary-open`);
  } else {
    note('готовый маршрут не найден');
  }
  await page.waitForTimeout(6_000);
});

// ── 3. Фильтры ──────────────────────────────────────────────────────────────
await withChapter('Фильтры: интересы, удобства, запреты', async (number) => {
  await openApp();
  await caption('3. Можно сузить запрос фильтрами: интересы, удобства, запреты');
  await page.click('[data-testid="mode-plan"]');
  await page.waitForTimeout(1_500);
  await page.click('[data-testid="more-filters"]');
  await page.waitForTimeout(2_500);
  await shot(`${number}-filters`);
  for (const testid of ['interest-замок', 'amenity-туалет-hard']) {
    const chip = page.locator(`[data-testid="${testid}"]`).first();
    if (await chip.count()) {
      await chip.click();
      await page.waitForTimeout(700);
    } else {
      note(`фильтр ${testid} не найден`);
    }
  }
  await page.waitForTimeout(1_500);
  const summary = page.locator('[data-testid="filters-summary"]').first();
  const said = (await summary.count()) ? (await summary.innerText()).replace(/\s+/g, ' ').slice(0, 120) : '';
  await caption(`3. Выбрано в фильтрах: ${said || 'замок и туалет по пути'}`);
  await shot(`${number}-filters-chosen`);
  await page.waitForTimeout(6_000);
});

// ── 4. Бюджет времени и транспорт ───────────────────────────────────────────
await withChapter('Своё время и транспорт', async (number) => {
  await caption('4. Говорю, сколько у меня времени, и как я еду');
  const info = await plan('Замки и костёлы Гродно', { minutes: 180, transport: 'pedestrian' });
  await page.waitForTimeout(4_000);
  await caption(
    `4. Пешком, 3 ч: остановок ${info.count} · ${info.km.toFixed(2)} км · ${info.minutes} мин\n${info.names.slice(0, 4).join(' · ')}`
  );
  await shot(`${number}-walk`);
  await page.waitForTimeout(6_000);

  const car = page.locator('[data-testid="transport-car"]').first();
  if (await car.count()) {
    await car.click();
    await page.waitForTimeout(9_000);
    await caption('4. Тот же запрос на машине — маршрут пересчитывается');
    await shot(`${number}-car`);
  }
  await page.waitForTimeout(6_000);
});

// ── 5. Точка на карте ───────────────────────────────────────────────────────
await withChapter('Точка на карте', async (number) => {
  await caption('5. Нажимаю на остановку — карточка с рассказом и любопытным фактом');
  const markers = page.locator('.maplibregl-marker');
  const count = await markers.count();
  if (count > 1) {
    await markers.nth(1).click();
    await page.waitForTimeout(3_500);
    await shot(`${number}-placecard`);
    await caption('5. Карточка места: категория, рассказ, время осмотра');
  } else {
    note(`маркеров на карте: ${count}`);
  }
  await page.waitForTimeout(6_000);
});

// ── 6. Правка руками ────────────────────────────────────────────────────────
await withChapter('Правка маршрута руками', async (number) => {
  await caption('6. Остановку можно перетащить по карте — маршрут перестроится');
  const markers = page.locator('.maplibregl-marker');
  if ((await markers.count()) > 1) {
    const target = markers.nth(1);
    const box = await target.boundingBox();
    if (box) {
      await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
      await page.mouse.down();
      await page.mouse.move(box.x + 160, box.y + 90, { steps: 20 });
      await page.mouse.up();
      await page.waitForTimeout(9_000);
      await shot(`${number}-dragged`);
      note('остановка перетащена');
    }
  }
  await caption('6. Или добавить остановку по названию');
  const name = 'Коложская церковь';
  await page.getByLabel('добавить точку в маршрут').first().fill(name).catch(async () => {
    await page.getByRole('textbox').nth(1).fill(name);
  });
  await page.waitForTimeout(600);
  const add = page.getByRole('button', { name: /найти и добавить/i }).first();
  if (await add.count()) {
    await add.click();
    await page.waitForTimeout(12_000);
    await shot(`${number}-added`);
  } else {
    note('кнопка добавления не найдена');
  }
  await page.waitForTimeout(6_000);
});

// ── 7. Услуги по пути ───────────────────────────────────────────────────────
await withChapter('Услуги по пути', async (number) => {
  await caption('7. По пути подсказываются места, куда можно зайти — но это не остановки');
  const block = page.locator('[data-testid="services-summary"]').first();
  if (await block.count()) {
    await block.scrollIntoViewIfNeeded();
    await page.waitForTimeout(1_500);
    await shot(`${number}-services`);
    const chip = block.locator('button').first();
    if (await chip.count()) {
      await chip.click();
      await page.waitForTimeout(4_000);
      await caption('7. Нажал на подсказку — видно, где эти места на карте');
      await shot(`${number}-services-map`);
    }
  } else {
    note('сводка услуг не найдена');
  }
  await page.waitForTimeout(6_000);
});

// ── 8. Проводник ────────────────────────────────────────────────────────────
await withChapter('Режим проводника', async (number) => {
  // Симуляция читает флаг из адреса при загрузке, поэтому заходим заново.
  await openApp('?profile=pedestrian&sim=walk&sim-speed=12');
  await caption('8. Проводник: положение проигрывается по маршруту — телефон не нужен');
  await plan('Фарный костёл и Новый замок', { minutes: 60 });
  await enterGuide();
  await page.waitForTimeout(3_000);
  await caption('8. Ведёт по остановкам: пройденное гаснет, до следующей — метры и минуты');
  for (let step = 0; step < 12; step += 1) {
    await page.waitForTimeout(4_000);
    if (step === 6) {
      await caption('8. Карта сама держит туриста в кадре и доворачивается по курсу');
      await shot(`${number}-guide`);
    }
  }
  await caption('8. Остановку можно отметить и руками, а время осмотра — поменять');
  const chip = page.locator('[data-testid="visit-time-chip"]').first();
  if (await chip.count()) {
    await chip.click();
    await page.waitForTimeout(2_500);
    await shot(`${number}-visit-time`);
  }
  await page.waitForTimeout(4_000);
  await leaveGuide();
});

// ── 9. Уточнение текстом ────────────────────────────────────────────────────
await withChapter('Уточнение словами', async (number) => {
  await caption('9. Маршрут можно уточнить словами, а не строить заново');
  const info = await plan('добавь кафе по пути');
  await page.waitForTimeout(5_000);
  await caption(`9. После уточнения остановок ${info.count}\n${info.names.slice(0, 5).join(' · ')}`);
  await shot(`${number}-refined`);
  await page.waitForTimeout(5_000);

  const undo = page.locator('button:has(svg.lucide-undo-2)').first();
  if (await undo.count()) {
    await undo.click();
    await page.waitForTimeout(5_000);
    await caption('9. Одно нажатие возвращает предыдущий маршрут');
    await shot(`${number}-undone`);
  } else {
    note('кнопка отмены не найдена');
  }
  await page.waitForTimeout(5_000);
});

// ── 10. Вне зоны покрытия ───────────────────────────────────────────────────
await withChapter('Вне зоны покрытия — честный отказ', async (number) => {
  await openApp();
  await caption('10. Проверяю, что будет за пределами области');
  const info = await plan('Мирский замок', { minutes: 180 });
  await page.waitForTimeout(5_000);
  await caption(
    `10. «Мирский замок» — вне зоны покрытия: статус ${info.status}, остановок ${info.count}\nГид говорит прямо, а не строит маршрут наугад`
  );
  note(`вне зоны: статус ${info.status}, остановок ${info.count}`);
  await shot(`${number}-outside`);
  await page.waitForTimeout(6_000);
});

// ── 11. Два языка ───────────────────────────────────────────────────────────
await withChapter('Русский и английский', async (number) => {
  await caption('11. Интерфейс переключается на английский целиком');
  await openApp();
  const en = page.locator('[data-testid="language-en"]').first();
  if (await en.count()) {
    await en.click();
    await page.waitForTimeout(4_000);
    await shot(`${number}-english`);
    await caption('11. English: the whole interface, including the guide');
    await page.waitForTimeout(4_000);
    const ru = page.locator('[data-testid="language-ru"]').first();
    if (await ru.count()) await ru.click();
    await page.waitForTimeout(3_000);
  } else {
    note('переключатель языка не найден');
  }
  await page.waitForTimeout(4_000);
});

// ── 12. Телефон ─────────────────────────────────────────────────────────────
await withChapter('Как это выглядит на телефоне', async (number) => {
  await caption('12. Тот же интерфейс на телефоне — панель уезжает вниз листом');
  await page.setViewportSize({ width: 390, height: 844 });
  await page.waitForTimeout(4_000);
  await shot(`${number}-phone`);
  const info = await plan('Музеи Гродно', { minutes: 120 });
  await page.waitForTimeout(4_000);
  await caption(`12. На телефоне: остановок ${info.count} · ${info.km.toFixed(2)} км`);
  await shot(`${number}-phone-route`);
  await page.waitForTimeout(6_000);
});

// ── Финал ───────────────────────────────────────────────────────────────────
await withChapter('Итог', async () => {
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.waitForTimeout(2_500);
  await caption('Всё это — на реальных данных по Гродненской области');
  await page.waitForTimeout(5_000);
});

const video = page.video();
const videoPath = video ? await video.path() : null;
await context.close();
await browser.close();
writeFileSync(`${OUT}/all.log`, log.join('\n'), 'utf8');

console.log(`\nВИДЕО: ${videoPath}`);
console.log(`ЖУРНАЛ: ${OUT}/all.log`);
