import { describe, it, expect, afterEach, vi } from 'vitest';
import { render, screen, cleanup, fireEvent } from '@testing-library/react';

import { VISIT_MAX, VISIT_MIN } from '@/utils/visit-time';

import { VisitTimeEditor } from './visit-time-editor';

afterEach(cleanup);

describe('VisitTimeEditor', () => {
  it('shows the dataset estimate as approximate', () => {
    render(<VisitTimeEditor estimate={40} value={null} onChange={() => {}} />);

    expect(screen.getByTestId('visit-time-chip')).toHaveTextContent('≈ 40 мин');
  });

  it('shows the tourist’s own number without the «≈»', () => {
    render(<VisitTimeEditor estimate={40} value={90} onChange={() => {}} />);

    expect(screen.getByTestId('visit-time-chip')).toHaveTextContent('90 мин');
    expect(screen.getByTestId('visit-time-chip')).not.toHaveTextContent('≈');
  });

  it('says nothing when there is neither an estimate nor a choice', () => {
    const { container } = render(
      <VisitTimeEditor estimate={null} value={null} onChange={() => {}} />
    );

    expect(container).toBeEmptyDOMElement();
  });

  it('changes by ten minutes per tap, from the estimate when nothing was chosen', () => {
    const onChange = vi.fn();
    render(<VisitTimeEditor estimate={40} value={null} onChange={onChange} />);

    fireEvent.click(screen.getByTestId('visit-time-chip'));
    fireEvent.click(screen.getByTestId('visit-time-plus'));

    expect(onChange).toHaveBeenCalledWith(50);
  });

  it('never goes below the floor — the step clamps instead of going negative', () => {
    const onChange = vi.fn();
    render(
      <VisitTimeEditor estimate={40} value={VISIT_MIN} onChange={onChange} />
    );

    fireEvent.click(screen.getByTestId('visit-time-chip'));
    fireEvent.click(screen.getByTestId('visit-time-minus'));

    expect(onChange).toHaveBeenCalledWith(VISIT_MIN);
  });

  it('drops the override when the step lands back on the estimate', () => {
    const onChange = vi.fn();
    render(<VisitTimeEditor estimate={50} value={60} onChange={onChange} />);

    fireEvent.click(screen.getByTestId('visit-time-chip'));
    fireEvent.click(screen.getByTestId('visit-time-minus'));

    expect(onChange).toHaveBeenCalledWith(null);
  });

  it('hands the estimate back with the reset button', () => {
    const onChange = vi.fn();
    render(<VisitTimeEditor estimate={40} value={90} onChange={onChange} />);

    fireEvent.click(screen.getByTestId('visit-time-chip'));
    fireEvent.click(screen.getByTestId('visit-time-reset'));

    expect(onChange).toHaveBeenCalledWith(null);
    expect(VISIT_MAX).toBeGreaterThan(VISIT_MIN);
  });
});
