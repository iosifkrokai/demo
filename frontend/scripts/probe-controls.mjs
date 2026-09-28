import { chromium } from 'playwright';
const BASE = process.env.APP_URL || 'http://localhost';
const b = await chromium.launch({ headless: true, args: ['--disable-background-timer-throttling','--disable-backgrounding-occluded-windows','--disable-renderer-backgrounding'] });
const ctx = await b.newContext({ viewport: { width: 1280, height: 800 }, locale: 'ru-RU' });
const p = await ctx.newPage();
await p.goto(`${BASE}/directions?profile=pedestrian`, { waitUntil: 'domcontentloaded' });
const f = p.getByRole('textbox').first();
await f.waitFor({ state: 'visible', timeout: 30000 });
await f.fill('Все главные достопримечательности Гродно');
const three = p.getByRole('button', { name: /^3\s*(ч|hrs?|hours?)$/i }).first();
if (await three.count()) await three.click();
await p.click('[data-testid="build-route"]');
await p.waitForSelector('[data-testid="guide-enter"]', { state: 'visible', timeout: 240000 });
const dump = async (label) => {
  const list = await p.evaluate(() =>
    [...document.querySelectorAll('button, [role="button"], input')].map((el) => {
      const t = (el.getAttribute('aria-label') || el.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 46);
      return `${el.tagName.toLowerCase()}${el.getAttribute('data-testid') ? '[' + el.getAttribute('data-testid') + ']' : ''}: ${t}`;
    }).filter((s) => /точк|останов|карт|машин|пешк|мин|час|отмет|пройд/i.test(s))
  );
  console.log(`\n=== ${label} (${list.length}) ===`);
  console.log(list.slice(0, 26).join('\n'));
};
await dump('панель после построения');
await p.click('[data-testid="guide-enter"]');
await p.waitForSelector('[data-testid="guide-panel"]', { state: 'visible', timeout: 30000 });
await dump('проводник');
await ctx.close(); await b.close();
