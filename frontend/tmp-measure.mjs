import { chromium } from '@playwright/test';
const URL = 'http://localhost:4173/directions?profile=pedestrian';
const browser = await chromium.launch();

// iPhone 12-ish viewport, touch.
const mobile = await browser.newContext({
  viewport: { width: 390, height: 844 }, deviceScaleFactor: 3,
  isMobile: true, hasTouch: true, locale: 'ru-RU',
});
const p = await mobile.newPage();
await p.goto(URL, { waitUntil: 'load' });
await p.waitForTimeout(4000);

const openBtn = await p.locator('[data-testid="mobile-panel-open"]').count();
const chevronVisible = await p.locator('[data-testid="panel-toggle"]').isVisible().catch(() => false);
console.log('phone: "Plan a route" button on the map:', openBtn === 1);
console.log('phone: desktop edge chevron visible:', chevronVisible);

await p.locator('[data-testid="mobile-panel-open"]').click();
await p.waitForTimeout(900);
const sheet = p.locator('[data-testid="mobile-sheet"]');
console.log('phone: sheet opened at snap =', await sheet.getAttribute('data-snap'));

// Drag the grab bar: short fast flick must open fully.
const grip = p.locator('[data-testid="sheet-handle"]');
const box = await grip.boundingBox();
const cx = box.x + box.width / 2;
const cy = box.y + box.height / 2;

const flick = async (dy) => {
  await p.mouse.move(cx, cy);
  await p.mouse.down();
  for (let i = 1; i <= 4; i++) await p.mouse.move(cx, cy + (dy * i) / 4);
  await p.mouse.up();
  await p.waitForTimeout(500);
  return sheet.getAttribute('data-snap');
};
console.log('phone: short fast flick DOWN (20px) from peek ->', await flick(20), '(want peek: slow path is not a flick)');
console.log('phone: short fast flick UP (30px) from peek ->', await flick(-30), '(want full)');

await p.locator('[data-testid="sheet-handle"]').click();
await p.waitForTimeout(400);
console.log('phone: after a tap, snap =', await sheet.getAttribute('data-snap'));

// Height follows the finger: sample mid-drag.
const b2 = await grip.boundingBox();
await p.mouse.move(cx, b2.y + b2.height / 2);
await p.mouse.down();
for (let i = 1; i <= 5; i++) await p.mouse.move(cx, b2.y + b2.height / 2 - i * 30);
await p.waitForTimeout(120);
const dragging = await sheet.getAttribute('data-dragging');
const midHeight = (await sheet.boundingBox()).height;
await p.mouse.up();
await p.waitForTimeout(400);
console.log('phone: mid-drag data-dragging =', dragging, ', height px =', Math.round(midHeight));
console.log('phone: after release snap =', await sheet.getAttribute('data-snap'));

// Flick down from the strip dismisses -> the panel closes and the button returns.
for (let i = 0; i < 3; i++) {
  const b = await p.locator('[data-testid="sheet-handle"]').boundingBox();
  if (!b) break;
  await p.mouse.move(b.x + b.width / 2, b.y + b.height / 2);
  await p.mouse.down();
  for (let k = 1; k <= 4; k++) await p.mouse.move(b.x + b.width / 2, b.y + b.height / 2 + k * 35);
  await p.mouse.up();
  await p.waitForTimeout(600);
  if (!(await p.locator('[data-testid="sheet-handle"]').count())) break;
}
console.log('phone: sheet dismissed by flicking down, "Plan a route" back =',
  (await p.locator('[data-testid="mobile-panel-open"]').count()) === 1);

await p.screenshot({ path: '/tmp/shot-phone-closed.png' });
await mobile.close();

// Desktop unchanged.
const desk = await browser.newContext({ viewport: { width: 1440, height: 900 } });
const d = await desk.newPage();
await d.goto(URL, { waitUntil: 'load' });
await d.waitForTimeout(3500);
const panel = await d.locator('[role="dialog"]').boundingBox();
console.log('desktop: panel width =', Math.round(panel.width), '(want 420)');
console.log('desktop: edge chevron visible =', await d.locator('[data-testid="panel-toggle"]').isVisible());
console.log('desktop: phone-only "Plan a route" button on the map =',
  await d.locator('[data-testid="mobile-panel-open"]').count(), '(want 0)');
await d.screenshot({ path: '/tmp/shot-desktop.png' });
await desk.close();
await browser.close();
