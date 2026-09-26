import { afterEach, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Segmented } from './segmented';

afterEach(cleanup);

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
