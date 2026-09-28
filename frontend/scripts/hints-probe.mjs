/**
 * Проверка: набор подсказок меняется, когда маршрут уже построен.
 *
 * Читает подписи чипов до постройки маршрута и после неё — глазами, из DOM
 * работающего приложения, а не из тестов.
 */
import { chromium } from 'playwright';

const BASE = process.env.APP_URL || 'http://localhost';

const chips = (page) =>
  page.evaluate(() =>
    [...document.querySelectorAll('[data-testid^="hint-"]')].map(
      (el) => el.getAttribute('data-testid') + ' → ' + el.textContent.trim()
    )
  );

const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
await page.goto(`${BASE}/directions?profile=pedestrian`, { waitUntil: 'domcontentloaded' });

const field = page.getByRole('textbox').first();
await field.waitFor({ state: 'visible', timeout: 30_000 });
console.log('ДО маршрута:');
for (const line of await chips(page)) console.log('  ' + line);

await field.fill('Фарный костёл и Новый замок, два часа');
await page.click('[data-testid="build-route"]');
await page.waitForSelector('[data-testid="guide-enter"]', { state: 'visible', timeout: 180_000 });
await page.waitForTimeout(4_000);

console.log('ПОСЛЕ маршрута:');
for (const line of await chips(page)) console.log('  ' + line);

await page.screenshot({ path: '/tmp/hints-after.png' });
await browser.close();
