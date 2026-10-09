import { beforeEach, describe, expect, it } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import {
  PANEL_WIDTH_DEFAULT,
  PANEL_WIDTH_MAX,
  PANEL_WIDTH_MIN,
  PANEL_WIDTH_STORAGE_KEY,
  PanelResizeHandle,
  clampPanelWidth,
  usePanelWidth,
} from './panel-resize';

const Harness = () => {
  const panel = usePanelWidth();
  return (
    <>
      <output data-testid="width">{panel.width}</output>
      <PanelResizeHandle
        width={panel.width}
        resizing={panel.resizing}
        props={panel.handleProps}
        label="изменять ширину панели"
      />
    </>
  );
};

const widthNow = () => Number(screen.getByTestId('width').textContent);
const handle = () => screen.getByTestId('panel-resize-handle');

/** A drag of `dx` pixels on the panel's right edge. */
const drag = async (dx: number) => {
  fireEvent.pointerDown(handle(), { clientX: 400, pointerId: 1 });
  fireEvent.pointerMove(handle(), { clientX: 400 + dx, pointerId: 1 });
  fireEvent.pointerUp(handle(), { clientX: 400 + dx, pointerId: 1 });
};

beforeEach(() => {
  localStorage.clear();
  // A desktop viewport: the ceiling is a share of it, not a fixed number.
  window.innerWidth = 1440;
});

describe('ширина панели', () => {
  it('не отдаёт панели всю ширину экрана', () => {
    // 60% of 900 is 540, which is under the hard ceiling of 720.
    expect(clampPanelWidth(700, 900)).toBe(540);
    // On a large screen the ceiling is the hard maximum, and it never goes wider.
    expect(clampPanelWidth(5000, 1440)).toBe(PANEL_WIDTH_MAX);
    expect(clampPanelWidth(700, 1440)).toBe(700);
    expect(clampPanelWidth(100, 1440)).toBe(PANEL_WIDTH_MIN);
    // Garbage instead of a number is no reason to give the panel zero width.
    expect(clampPanelWidth(Number.NaN, 1440)).toBe(PANEL_WIDTH_DEFAULT);
  });

  it('по умолчанию шире, чем было (было жёстко 380)', () => {
    render(<Harness />);

    expect(widthNow()).toBe(PANEL_WIDTH_DEFAULT);
    expect(PANEL_WIDTH_DEFAULT).toBeGreaterThan(380);
  });

  it('тянется за правый край и запоминает ширину', async () => {
    render(<Harness />);

    await drag(120);

    expect(widthNow()).toBe(PANEL_WIDTH_DEFAULT + 120);
    expect(localStorage.getItem(PANEL_WIDTH_STORAGE_KEY)).toBe(
      String(PANEL_WIDTH_DEFAULT + 120)
    );
  });

  it('сохранённую ширину восстанавливает под новый экран', () => {
    localStorage.setItem(PANEL_WIDTH_STORAGE_KEY, '700');
    window.innerWidth = 900;

    render(<Harness />);

    // 700 is wider than this screen allows: the panel must not eat the map.
    expect(widthNow()).toBe(540);
  });

  it('сжимается до доли экрана при сужении окна', () => {
    render(<Harness />);
    expect(widthNow()).toBe(PANEL_WIDTH_DEFAULT);

    // The same tourist narrowed the window: the panel must not eat the map.
    window.innerWidth = 500; // ceiling = max(340, min(720, 300)) = 340
    fireEvent(window, new Event('resize'));

    expect(widthNow()).toBe(PANEL_WIDTH_MIN);
  });

  it('управляется с клавиатуры, а Shift двигает крупнее', async () => {
    const user = userEvent.setup();
    render(<Harness />);

    handle().focus();
    await user.keyboard('{ArrowRight}');
    expect(widthNow()).toBe(PANEL_WIDTH_DEFAULT + 16);

    await user.keyboard('{Shift>}{ArrowRight}{/Shift}');
    expect(widthNow()).toBe(PANEL_WIDTH_DEFAULT + 16 + 64);

    await user.keyboard('{ArrowLeft}');
    expect(widthNow()).toBe(PANEL_WIDTH_DEFAULT + 64);

    await user.keyboard('{Home}');
    expect(widthNow()).toBe(PANEL_WIDTH_MIN);
    await user.keyboard('{End}');
    expect(widthNow()).toBe(PANEL_WIDTH_MAX);
  });

  it('двойной клик возвращает ширину по умолчанию', async () => {
    render(<Harness />);
    await drag(200);
    expect(widthNow()).not.toBe(PANEL_WIDTH_DEFAULT);

    fireEvent.doubleClick(handle());

    expect(widthNow()).toBe(PANEL_WIDTH_DEFAULT);
    expect(localStorage.getItem(PANEL_WIDTH_STORAGE_KEY)).toBe(
      String(PANEL_WIDTH_DEFAULT)
    );
  });

  it('рассказывает скринридеру, что это разделитель и где он сейчас', async () => {
    render(<Harness />);

    expect(handle()).toHaveAttribute('role', 'separator');
    expect(handle()).toHaveAttribute('aria-orientation', 'vertical');
    expect(handle()).toHaveAttribute(
      'aria-valuenow',
      String(PANEL_WIDTH_DEFAULT)
    );
    expect(handle()).toHaveAttribute('aria-valuemin', String(PANEL_WIDTH_MIN));
    expect(handle()).toHaveAttribute('aria-valuemax', String(PANEL_WIDTH_MAX));

    await drag(60);
    expect(handle()).toHaveAttribute(
      'aria-valuenow',
      String(PANEL_WIDTH_DEFAULT + 60)
    );
  });

  it('растянутая до предела панель не сжимается по инерции', async () => {
    render(<Harness />);

    await drag(5000);
    expect(widthNow()).toBe(PANEL_WIDTH_MAX);

    await drag(-5000);
    expect(widthNow()).toBe(PANEL_WIDTH_MIN);
  });
});
