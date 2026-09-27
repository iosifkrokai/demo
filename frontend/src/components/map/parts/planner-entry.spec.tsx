import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { PlannerEntry, PLANNER_ENTRY_LABEL } from './planner-entry';

afterEach(cleanup);

/**
 * The planner's front door. It used to be an icon-only button whose only name
 * was the English tooltip «Directions», so a phone had no readable way into the
 * planner at all. These tests pin the fix: a visible Russian label, a real tap
 * target, and no English string anywhere on it.
 */
describe('PlannerEntry', () => {
  it('shows the Russian label as visible text, not only as a tooltip', () => {
    render(<PlannerEntry onClick={() => {}} />);
    const button = screen.getByTestId('tab-directions-button');

    expect(button).toHaveTextContent(PLANNER_ENTRY_LABEL);
    expect(PLANNER_ENTRY_LABEL).toBe('Планировать маршрут');
  });

  it('never exposes the old English "Directions" string', () => {
    render(<PlannerEntry onClick={() => {}} />);
    const button = screen.getByTestId('tab-directions-button');

    expect(button.getAttribute('aria-label')).not.toMatch(/directions/i);
    expect(button.getAttribute('title')).not.toMatch(/directions/i);
    expect(button.textContent).not.toMatch(/directions/i);
  });

  it('is at least 44px tall everywhere (DESIGN.md touch minimum)', () => {
    render(<PlannerEntry onClick={() => {}} />);
    const button = screen.getByTestId('tab-directions-button');

    expect(button.className).toMatch(/\bh-11\b/);
    expect(button.className).toMatch(/\bmin-h-11\b/);
  });

  it('opens the planner on click', async () => {
    const onClick = vi.fn();
    render(<PlannerEntry onClick={onClick} />);

    await userEvent.click(screen.getByTestId('tab-directions-button'));

    expect(onClick).toHaveBeenCalledTimes(1);
  });

  it('reports its open state to assistive tech', () => {
    const { rerender } = render(
      <PlannerEntry onClick={() => {}} open={false} />
    );
    expect(screen.getByTestId('tab-directions-button')).toHaveAttribute(
      'aria-expanded',
      'false'
    );

    rerender(<PlannerEntry onClick={() => {}} open />);
    expect(screen.getByTestId('tab-directions-button')).toHaveAttribute(
      'aria-expanded',
      'true'
    );
  });
});
