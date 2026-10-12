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

/** A colour painted at `alpha` over the white page, as a browser composites it. */
const mixOverWhite = (hex: string, alpha: number): string => {
  const h = hex.replace('#', '');
  const ch = [0, 2, 4].map((i) =>
    Math.round(alpha * parseInt(h.slice(i, i + 2), 16) + (1 - alpha) * 255)
  );
  return `#${ch.map((v) => v.toString(16).padStart(2, '0')).join('')}`;
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

  it('keeps the status tones (warning/success/info) readable as text on white', () => {
    for (const name of ['--warning', '--success', '--info']) {
      expect(
        contrast(token(name), token('--background'))
      ).toBeGreaterThanOrEqual(4.5);
    }
  });

  it('keeps white on the primary hover fill (bg-primary/90) at AA', () => {
    // The shared Button lightens its fill on hover: `hover:bg-primary/90`. The
    // hover state is text too, so it must clear AA, not only the resting fill.
    expect(
      contrast(
        token('--primary-foreground'),
        mixOverWhite(token('--primary'), 0.9)
      )
    ).toBeGreaterThanOrEqual(4.5);
  });

  it('keeps warning/info readable on their own 10% and 15% tints', () => {
    // A degraded verdict and the over-budget chip render `text-warning` on
    // `bg-warning/15`; the guide renders `text-info` on `bg-info/10`. All at
    // 12px, so they need the full 4.5:1, not just the on-white case above.
    for (const name of ['--warning', '--info']) {
      for (const alpha of [0.1, 0.15]) {
        expect(
          contrast(token(name), mixOverWhite(token(name), alpha))
        ).toBeGreaterThanOrEqual(4.5);
      }
    }
  });
});
