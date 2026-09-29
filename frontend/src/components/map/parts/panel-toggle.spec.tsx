import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import { PanelToggle } from './panel-toggle';

describe('PanelToggle', () => {
  it('называет себя вслух и говорит, открыта ли панель', () => {
    render(
      <PanelToggle
        open={false}
        onToggle={() => {}}
        label="открыть или закрыть панель маршрута"
      />
    );

    const handle = screen.getByRole('button', {
      name: 'открыть или закрыть панель маршрута',
    });
    expect(handle).toHaveAttribute('aria-expanded', 'false');
    expect(handle).toHaveAttribute(
      'title',
      'открыть или закрыть панель маршрута'
    );
  });

  it('показывает, куда двинется панель: закрыта — вправо, открыта — влево', () => {
    const { rerender } = render(
      <PanelToggle open={false} onToggle={() => {}} label="панель" />
    );
    // The chevron points the way the panel moves, so the icon alone says what
    // the next click does. lucide marks the two glyphs by name.
    expect(document.querySelector('.lucide-chevron-right')).toBeInTheDocument();
    expect(
      document.querySelector('.lucide-chevron-left')
    ).not.toBeInTheDocument();

    rerender(<PanelToggle open onToggle={() => {}} label="панель" />);

    expect(document.querySelector('.lucide-chevron-left')).toBeInTheDocument();
    expect(
      document.querySelector('.lucide-chevron-right')
    ).not.toBeInTheDocument();
  });

  it('сообщает о нажатии одним колбэком — ручка не знает, что делает панель', async () => {
    const onToggle = vi.fn();
    const user = userEvent.setup();
    render(<PanelToggle open={false} onToggle={onToggle} label="панель" />);

    await user.click(screen.getByTestId('panel-toggle'));

    expect(onToggle).toHaveBeenCalledTimes(1);
  });

  it('прилипает к краю панели: позицию задаёт тот, кто её знает', () => {
    render(
      <PanelToggle
        open={false}
        onToggle={() => {}}
        label="панель"
        className="left-[min(var(--panel-width,0px),calc(100vw-1.75rem))]"
      />
    );

    // Position comes from the caller — the panel's own edge, clamped to the
    // viewport — so the handle cannot drift away from the panel it belongs to.
    const handle = screen.getByTestId('panel-toggle');
    expect(handle.className).toContain('--panel-width');
    expect(handle.className).toContain('top-1/2');
  });
});
