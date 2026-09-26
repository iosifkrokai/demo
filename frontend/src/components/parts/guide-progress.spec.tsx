import { describe, it, expect, afterEach } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';

import { GuideProgress } from './guide-progress';

afterEach(cleanup);

describe('GuideProgress', () => {
  it('counts stops when there is no route line', () => {
    render(<GuideProgress done={1} total={4} minutesLeft={50} />);

    expect(screen.getByText(/пройдено 1 из 4/i)).toBeInTheDocument();
    expect(screen.getByTestId('guide-minutes-left')).toHaveTextContent(
      'осталось осмотра ~50 мин'
    );
    expect(screen.getByRole('progressbar')).toHaveAttribute(
      'aria-valuenow',
      '1'
    );
    expect(screen.queryByTestId('guide-line-progress')).toBeNull();
  });

  it('tracks the metres walked once the route line is known', () => {
    render(
      <GuideProgress
        done={1}
        total={4}
        minutesLeft={50}
        metresDone={1500}
        metresTotal={6000}
        remainingMinutes={95}
      />
    );

    // Done part vs remaining, in words, straight off the line.
    expect(screen.getByTestId('guide-line-progress')).toHaveTextContent(
      'по линии пройдено 1.5 км'
    );
    expect(screen.getByTestId('guide-line-progress')).toHaveTextContent(
      'осталось 4.5 км'
    );
    // The bar follows the line (25 %), not the stop count (25 % here too).
    expect(screen.getByRole('progressbar')).toHaveAttribute(
      'aria-valuetext',
      'пройдено 25% линии'
    );
    expect(screen.getByTestId('guide-remaining')).toHaveTextContent(
      'с дорогой осталось ~1 ч 35 мин'
    );
    // The stop-count number stays the headline.
    expect(screen.getByText(/пройдено 1 из 4/i)).toBeInTheDocument();
  });

  it('never reports a backwards or over-100 % bar', () => {
    render(
      <GuideProgress
        done={0}
        total={2}
        minutesLeft={0}
        metresDone={9000}
        metresTotal={6000}
      />
    );

    expect(screen.getByRole('progressbar')).toHaveAttribute(
      'aria-valuetext',
      'пройдено 100% линии'
    );
  });
});
