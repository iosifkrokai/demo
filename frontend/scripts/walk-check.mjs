/** Проверить, отмечает ли проводник остановки по ходу прохода. */
import { chromium } from 'playwright';
const BASE = process.env.APP_URL || 'http://localhost';
const browser = await chromium.launch({ headless: true, args: [
  '--disable-background-timer-throttling', '--disable-backgrounding-occluded-windows',
  '--disable-renderer-backgrounding'] });
const context = await browser.newContext({ viewport: { width: 1280, height: 900 }, locale: 'ru-RU' });
const page = await context.newPage();
await page.goto(`${BASE}/directions?profile=pedestrian&sim=walk&sim-speed=5`, { waitUntil: 'domcontentloaded' });
const f = page.getByRole('textbox').first();
await f.waitFor({ state: 'visible', timeout: 30_000 });
await f.fill('Все главные достопримечательности Гродно');
const three = page.getByRole('button', { name: /^3\s*(ч|hrs?|hours?)$/i }).first();
if (await three.count()) await three.click();
await page.click('[data-testid="build-route"]');
await page.waitForSelector('[data-testid="guide-enter"]', { state: 'visible', timeout: 240_000 });
await page.click('[data-testid="guide-enter"]');
await page.waitForSelector('[data-testid="guide-panel"]', { state: 'visible', timeout: 30_000 });
for (let i = 1; i <= 10; i += 1) {
  const row = await page.evaluate(() => {
    const g = window.__geoSim;
    const t = document.querySelector('[data-testid="guide-panel"]').innerText.replace(/\s*\n\s*/g, ' | ');
    const prog = t.match(/пройдено\s+\d+\s+из\s+\d+/i);
    const next = t.match(/следующая остановка\s*\|\s*(\d+)[^|]*\|([^|]+)/i);
    return { м: g ? Math.round(g.travelled()) : null, прогресс: prog ? prog[0] : '—',
             следующая: next ? `${next[1].trim()}. ${next[2].trim().slice(0, 30)}` : '—' };
  });
  await page.screenshot({ path: `/tmp/walkframe-${String(i).padStart(2, '0')}.png` });
  console.log(`${String(i * 5).padStart(3)} с | пройдено ${row.м} м | ${row.прогресс} | следующая: ${row.следующая}`);
  await page.waitForTimeout(5_000);
}
await context.close();
await browser.close();
