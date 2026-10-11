import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

/** The design tokens are a contract (DESIGN.md). Light theme is the only theme. */
const css = readFileSync(
  path.resolve(path.dirname(fileURLToPath(import.meta.url)), 'index.css'),
  'utf8'
);

const token = (name: string): string => {
  const scope = css.slice(
    css.indexOf(':root {'),
    css.indexOf('}', css.indexOf(':root {'))
  );
  const match = scope.match(new RegExp(`${name}:\\s*(#[0-9a-fA-F]{6})`));
  if (!match?.[1]) throw new Error(`${name} not found in :root`);
  return match[1];
};

const channel = (c: number) => {
  const s = c / 255;
  return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
};

const luminance = (hex: string) => {
  const h = hex.replace('#', '');
  const [r, g, b] = [0, 2, 4].map((i) =>
    channel(parseInt(h.slice(i, i + 2), 16))
  );
  return 0.2126 * r! + 0.7152 * g! + 0.0722 * b!;
};

const contrast = (a: string, b: string) => {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi! + 0.05) / (lo! + 0.05);
};

describe('design tokens', () => {
  it('keeps the CTA (primary-foreground on primary) at AA 4.5:1 or better', () => {
    const ratio = contrast(token('--primary-foreground'), token('--primary'));
    expect(ratio).toBeGreaterThanOrEqual(4.5);
  });

  it('keeps body text on the page background readable', () => {
    expect(
      contrast(token('--foreground'), token('--background'))
    ).toBeGreaterThanOrEqual(4.5);
  });

  it('keeps secondary text on the page background at least at AA large', () => {
    expect(
      contrast(token('--muted-foreground'), token('--background'))
    ).toBeGreaterThanOrEqual(4.5);
  });
});
