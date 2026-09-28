/** Разведка панели: вкладки, фильтры, язык, сохранение — что и как называется. */
import { chromium } from 'playwright';

const BASE = process.env.APP_URL || 'http://localhost';
const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
await page.goto(`${BASE}/directions`, { waitUntil: 'domcontentloaded' });
await page.getByRole('textbox').first().waitFor({ state: 'visible', timeout: 30_000 });

const dump = () =>
  page.evaluate(() => {
    const label = (el) =>
      (el.getAttribute('data-testid') ? `[${el.getAttribute('data-testid')}] ` : '') +
      (el.getAttribute('aria-label') || el.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 60);
    return {
      вкладки: [...document.querySelectorAll('[data-testid^="mode-"]')].map(label),
      кнопки: [...document.querySelectorAll('aside button, [role="dialog"] button')]
        .map(label)
        .filter((x) => x.length > 1)
        .slice(0, 30),
      ссылки: [...document.querySelectorAll('[role="dialog"] a, aside a')].map(label).slice(0, 10),
      всего_тестидов: new Set(
        [...document.querySelectorAll('[data-testid]')].map((el) => el.getAttribute('data-testid'))
      ).size,
    };
  });

console.log('=== стартовый экран ===');
console.log(JSON.stringify(await dump(), null, 1).slice(0, 2600));

for (const tab of ['mode-itineraries', 'mode-history']) {
  const el = page.locator(`[data-testid="${tab}"]`).first();
  if (await el.count()) {
    await el.click();
    await page.waitForTimeout(2_500);
    console.log(`=== после ${tab} ===`);
    console.log(JSON.stringify(await dump(), null, 1).slice(0, 1400));
  } else {
    console.log(`нет вкладки ${tab}`);
  }
}

await browser.close();
