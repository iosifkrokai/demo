import { render, screen, cleanup } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi, afterEach } from 'vitest';

import { PanelToggle } from './panel-toggle';

afterEach(cleanup);

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

    const handle = screen.getByTestId('panel-toggle');
    expect(handle.className).toContain('--panel-width');
    expect(handle.className).toContain('top-1/2');
  });

  it('на телефоне не рисуется вовсе: у шторки нет своего левого края', () => {
    render(
      <PanelToggle
        open={false}
        onToggle={() => {}}
        label="панель"
        className="left-[min(var(--panel-width,0px),calc(100vw-1.75rem))]"
      />
    );

    const handle = screen.getByTestId('panel-toggle');
    expect(handle.className).toContain('hidden');
    expect(handle.className).toContain('md:flex');
  });
});
