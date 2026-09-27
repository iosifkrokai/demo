import type { SVGProps } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Footprints } from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import { Segmented, type SegmentedItem } from './segmented';

afterEach(cleanup);

/** Real items from `sidebar.tsx`: shortened pills with a decorative icon. */
const VIEWS: SegmentedItem<'plan' | 'history' | 'itineraries'>[] = [
  { value: 'plan', label: 'Планирование', short: 'План' },
  { value: 'history', label: 'История' },
  { value: 'itineraries', label: 'Готовые маршруты', short: 'Готовые' },
];

/**
 * Lucide icons carry no text, so they cannot prove they stay out of the
 * accessible name. This one shouts, and a screen reader must still not hear it.
 */
const SHOUTING_ICON = ((props: SVGProps<SVGSVGElement>) => (
  <svg {...props}>
    <text>ИКОНКА</text>
  </svg>
)) as unknown as LucideIcon;

describe('Segmented', () => {
  it('keeps the full wording as the accessible name of a shortened pill', () => {
    render(
      <Segmented
        items={VIEWS}
        value="plan"
        onChange={() => undefined}
        label="раздел панели"
      />
    );

    // the pill is short, but the name a screen reader announces is not
    const plan = screen.getByRole('radio', { name: 'Планирование' });
    expect(plan).toHaveTextContent(/^План$/);
    expect(plan).toHaveAttribute('aria-label', 'Планирование');
    expect(
      screen.getByRole('radio', { name: 'Готовые маршруты' })
    ).toHaveTextContent(/^Готовые$/);

    // the shortened text must not become the name on its own
    expect(screen.queryByRole('radio', { name: 'План' })).toBeNull();
    expect(screen.queryByRole('radio', { name: 'Готовые' })).toBeNull();
  });

  it('leaves an unshortened pill without aria-label — its text is the name', () => {
    render(
      <Segmented
        items={VIEWS}
        value="history"
        onChange={() => undefined}
        label="раздел панели"
      />
    );

    const history = screen.getByRole('radio', { name: 'История' });
    expect(history).toHaveTextContent(/^История$/);
    expect(history).not.toHaveAttribute('aria-label');
  });

  it('keeps the full wording as the tooltip', () => {
    render(
      <Segmented
        items={VIEWS}
        value="plan"
        onChange={() => undefined}
        label="раздел панели"
      />
    );

    expect(screen.getByTitle('Планирование')).toHaveTextContent(/^План$/);
  });

  it('calls onChange exactly once with the picked value', async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(
      <Segmented
        items={VIEWS}
        value="plan"
        onChange={onChange}
        label="раздел панели"
      />
    );

    await user.click(screen.getByRole('radio', { name: 'История' }));
    expect(onChange).toHaveBeenCalledTimes(1);
    expect(onChange).toHaveBeenCalledWith('history');
  });

  it('marks the selected item in the radiogroup', () => {
    render(
      <Segmented
        items={VIEWS}
        value="itineraries"
        onChange={() => undefined}
        label="раздел панели"
      />
    );

    expect(
      screen.getByRole('radiogroup', { name: 'раздел панели' })
    ).toBeInTheDocument();
    const checked = screen
      .getAllByRole('radio')
      .filter((radio) => radio.getAttribute('aria-checked') === 'true');
    expect(checked).toHaveLength(1);
    expect(checked[0]).toHaveAccessibleName('Готовые маршруты');
    expect(screen.getByRole('radio', { name: 'Планирование' })).toHaveAttribute(
      'aria-checked',
      'false'
    );
  });

  it('hides the icon from assistive tech, with or without a short label', () => {
    const { container } = render(
      <Segmented
        items={[
          { value: 'walk', label: 'пешком', icon: SHOUTING_ICON },
          {
            value: 'bike',
            label: 'велосипед',
            short: 'велосипед',
            icon: Footprints,
          },
        ]}
        value="walk"
        onChange={() => undefined}
        label="на чём"
      />
    );

    // the icon is decoration: present in the DOM, absent from every name
    expect(container.querySelectorAll('svg')).toHaveLength(2);
    container.querySelectorAll('svg').forEach((icon) => {
      expect(icon).toHaveAttribute('aria-hidden', 'true');
    });
    expect(screen.getByRole('radio', { name: 'пешком' })).toHaveTextContent(
      /^ИКОНКАпешком$/
    );
    expect(
      screen.getByRole('radio', { name: 'велосипед' })
    ).toBeInTheDocument();
  });

  it('does not change anything while disabled', async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(
      <Segmented
        items={VIEWS}
        value="plan"
        onChange={onChange}
        label="раздел панели"
        disabled
      />
    );

    const history = screen.getByRole('radio', { name: 'История' });
    expect(history).toBeDisabled();
    await user.click(history);
    expect(onChange).not.toHaveBeenCalled();
    expect(history).toHaveAttribute('aria-checked', 'false');
  });

  it('exposes the testId hook the panel tabs are queried by', () => {
    render(
      <Segmented
        items={VIEWS}
        value="plan"
        onChange={() => undefined}
        label="раздел панели"
        testId={(value) => `mode-${value}`}
      />
    );

    expect(screen.getByTestId('mode-plan')).toHaveAccessibleName(
      'Планирование'
    );
    expect(screen.getByTestId('mode-itineraries')).toHaveAccessibleName(
      'Готовые маршруты'
    );
  });
});

it('moves selection and focus with arrow keys, wrapping at the end', async () => {
  const user = userEvent.setup();
  const onChange = vi.fn();
  const { rerender } = render(
    <Segmented
      items={[
        { value: 'walk', label: 'пешком' },
        { value: 'bike', label: 'велосипед' },
      ]}
      value="walk"
      onChange={onChange}
      label="на чём"
    />
  );
  const walk = screen.getByRole('radio', { name: 'пешком' });
  walk.focus();
  await user.keyboard('{ArrowRight}');
  expect(onChange).toHaveBeenCalledWith('bike');
  expect(screen.getByRole('radio', { name: 'велосипед' })).toHaveFocus();
  rerender(
    <Segmented
      items={[
        { value: 'walk', label: 'пешком' },
        { value: 'bike', label: 'велосипед' },
      ]}
      value="bike"
      onChange={onChange}
      label="на чём"
    />
  );
  await user.keyboard('{ArrowRight}');
  expect(onChange).toHaveBeenLastCalledWith('walk');
  expect(walk).toHaveFocus();
});

it('keeps only the selected radio in the tab order', () => {
  render(
    <Segmented
      items={[
        { value: 'walk', label: 'пешком' },
        { value: 'bike', label: 'велосипед' },
      ]}
      value="bike"
      onChange={() => undefined}
      label="на чём"
    />
  );
  expect(screen.getByRole('radio', { name: 'пешком' })).toHaveAttribute(
    'tabindex',
    '-1'
  );
  expect(screen.getByRole('radio', { name: 'велосипед' })).toHaveAttribute(
    'tabindex',
    '0'
  );
});

it('jumps to the first and last option with Home and End', async () => {
  const user = userEvent.setup();
  const onChange = vi.fn();
  render(
    <Segmented
      items={[
        { value: 'a', label: 'один' },
        { value: 'b', label: 'два' },
        { value: 'c', label: 'три' },
      ]}
      value="b"
      onChange={onChange}
      label="порядок"
    />
  );
  screen.getByRole('radio', { name: 'два' }).focus();
  await user.keyboard('{End}');
  expect(onChange).toHaveBeenLastCalledWith('c');
  expect(screen.getByRole('radio', { name: 'три' })).toHaveFocus();
  await user.keyboard('{Home}');
  expect(onChange).toHaveBeenLastCalledWith('a');
  expect(screen.getByRole('radio', { name: 'один' })).toHaveFocus();
});
