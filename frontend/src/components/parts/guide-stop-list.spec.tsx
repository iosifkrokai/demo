import { describe, it, expect, afterEach, vi } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { GuideStopList, type GuideStopItem } from './guide-stop-list';

afterEach(cleanup);

const STOPS: GuideStopItem[] = [
  {
    id: '1',
    name: 'Монастырь бригиток',
    category: 'монастырь',
    visitMinutes: 30,
  },
  { id: '2', name: 'Кафе Немо', category: 'кафе', visitMinutes: 40 },
];

const noop = () => undefined;

describe('GuideStopList', () => {
  it('shows every row when it is not collapsible', () => {
    render(
      <GuideStopList
        stops={STOPS}
        visited={['1']}
        nextId="2"
        nextDistance={240}
        onToggle={noop}
      />
    );

    expect(screen.getByTestId('guide-stop-1')).toHaveAttribute(
      'aria-pressed',
      'true'
    );
    expect(screen.getByTestId('guide-stop-2')).toHaveAttribute(
      'aria-current',
      'step'
    );
    expect(screen.getByTestId('guide-stop-2')).toHaveTextContent('240 м');
    expect(
      screen.queryByTestId('guide-stop-list-toggle')
    ).not.toBeInTheDocument();
  });

  it('folds the rows behind a counted toggle while moving', async () => {
    const user = userEvent.setup();
    render(
      <GuideStopList
        stops={STOPS}
        visited={['1']}
        nextId="2"
        nextDistance={null}
        onToggle={noop}
        collapsible
        defaultOpen={false}
      />
    );

    const toggle = screen.getByTestId('guide-stop-list-toggle');
    expect(toggle).toHaveTextContent('остановки · 1 из 2');
    expect(toggle).toHaveAttribute('aria-expanded', 'false');
    expect(screen.queryByTestId('guide-stop-1')).toBeNull();

    await user.click(toggle);
    expect(toggle).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByTestId('guide-stop-1')).toBeInTheDocument();
  });

  it('reports a row tap so the walk can be advanced by hand', async () => {
    const onToggle = vi.fn();
    const user = userEvent.setup();
    render(
      <GuideStopList
        stops={STOPS}
        visited={[]}
        nextId="1"
        nextDistance={null}
        onToggle={onToggle}
      />
    );

    await user.click(screen.getByTestId('guide-stop-1'));
    expect(onToggle).toHaveBeenCalledWith('1');
  });
});
