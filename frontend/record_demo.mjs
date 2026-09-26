// Запись демо-прогона в видео (Playwright, Chromium headless).
// Запуск: cd /workspaces/demo/frontend && node /workspaces/demo/cache/record_demo.mjs
import { chromium } from 'playwright';

const OUT = '/workspaces/demo/cache/video';
const URL = process.env.DEMO_URL || 'http://localhost/';
const QUERY = 'Хочу посмотреть замки и костёлы Гродно';
const REFINE = 'добавь по пути кофейню и туалет';

const log = (...a) => console.log(new Date().toISOString().slice(11, 19), ...a);

const browser = await chromium.launch({
  // Установленная сборка chromium (1243) новее ожидаемой playwright 1.57 (1200),
  // версия движка совпадает — просто указываем бинарь, без докачки.
  executablePath:
    process.env.PW_CHROME ||
    '/home/codespace/.cache/ms-playwright/chromium_headless_shell-1243/chrome-headless-shell-linux64/chrome-headless-shell',
  args: ['--no-sandbox', '--disable-dev-shm-usage', '--disable-gpu'],
});
const context = await browser.newContext({
  viewport: { width: 1440, height: 900 },
  locale: 'ru-RU',
  recordVideo: { dir: OUT, size: { width: 1440, height: 900 } },
});
const page = await context.newPage();
const errors = [];
page.on('pageerror', (e) => errors.push('pageerror: ' + e.message));
page.on('console', (m) => m.type() === 'error' && errors.push('console: ' + m.text().slice(0, 160)));

const text = () => page.evaluate(() => document.body.innerText.replace(/\n+/g, ' | '));

try {
  await page.goto(URL, { waitUntil: 'load' });
  await page.waitForTimeout(3500);                       // титул/карта
  log('старт:', (await text()).slice(0, 90));

  const ask = page.locator('textarea').first();
  await ask.click();
  await ask.type(QUERY, { delay: 40 });                  // печатаем как человек
  await page.waitForTimeout(900);

  await page.getByRole('button', { name: /Построить маршрут/ }).click();
  log('запрос отправлен');
  await page.waitForSelector('text=/\\d+\\s*точек/', { timeout: 150000 });
  await page.waitForTimeout(4000);                       // дать карте дорисовать линию
  const built = await text();
  log('маршрут:', built.slice(built.indexOf('точек') - 30, built.indexOf('точек') + 120));

  // Переключаемся в проводник (кнопка сегмента: у радиокнопок нет роли button с этим именем)
  await page.locator('button:has-text("Проводник")').first().click();
  await page.waitForTimeout(2500);
  const guide = await text();
  log('проводник:', guide.slice(guide.indexOf('следующая остановка'), guide.indexOf('следующая остановка') + 110));

  // Пролистываем список остановок, чтобы он попал в кадр
  const list = page.locator('div:has-text("сбросить прогресс")').last();
  await list.hover().catch(() => {});
  await page.mouse.wheel(0, 420);
  await page.waitForTimeout(1800);
  await page.mouse.wheel(0, -420);
  await page.waitForTimeout(800);

  // Отмечаем первую остановку пройденной
  const done = page.locator('button:has-text("пройдена"), button:has-text("Пройти"), button:has-text("отметить")').first();
  if (await done.count()) {
    await done.click();
    await page.waitForTimeout(2200);
    log('после отметки:', (await text()).match(/пройдено \d+ из \d+/)?.[0]);
  } else {
    log('кнопки ручной отметки не нашлось — показываем список');
  }

  // Уточнение (лучшая попытка: если поле недоступно — просто продолжаем)
  try {
    await page.getByRole('button', { name: 'Планирование', exact: true }).click();
    await page.waitForTimeout(1500);
    const ask2 = page.locator('textarea').first();
    await ask2.click();
    await ask2.type(REFINE, { delay: 35 });
    await page.waitForTimeout(600);
    await page.getByRole('button', { name: /Построить маршрут|Уточнить/ }).first().click();
    await page.waitForTimeout(45000);
    const after = await text();
    log('уточнение:', after.match(/\d+\s*точек[^\n]{0,40}/)?.[0] ?? 'не дождались');
  } catch (e) {
    log('уточнение пропущено:', e.message.slice(0, 80));
  }

  await page.waitForTimeout(3000);
} catch (e) {
  log('ОШИБКА СЦЕНАРИЯ:', e.message);
  await page.screenshot({ path: OUT + '/error.png' }).catch(() => {});
} finally {
  const p = await page.video()?.path();
  await context.close();
  await browser.close();
  log('видео:', p);
  log('ошибок страницы:', errors.length, errors.slice(0, 3));
}
