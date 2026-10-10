import { describe, it, expect, afterEach } from 'vitest';
import { render, screen, fireEvent, cleanup } from '@testing-library/react';
import { SheetDragHandle, useSheetSnap } from './sheet-snap';

afterEach(cleanup);

/** The hook plus the handle, i.e. the sheet's only mobile chrome. */
const Harness = () => {
  const { snap, handleProps } = useSheetSnap();
  return (
    <>
      <span data-testid="snap">{snap}</span>
      <SheetDragHandle snap={snap} handleProps={handleProps} />
    </>
  );
};

const handle = () => screen.getByRole('button', { name: /панель/ });

describe('useSheetSnap', () => {
  it('opens at the peek snap point and expands on a tap', () => {
    render(<Harness />);

    expect(screen.getByTestId('snap')).toHaveTextContent('peek');
    expect(handle()).toHaveAttribute('aria-expanded', 'false');

    fireEvent.click(handle());
    expect(screen.getByTestId('snap')).toHaveTextContent('full');
    expect(
      screen.getByRole('button', { name: 'свернуть панель' })
    ).toHaveAttribute('aria-expanded', 'true');
  });

  it('a drag up expands without the tap that ends it toggling back', () => {
    render(<Harness />);

    const grip = handle();
    fireEvent.pointerDown(grip, { clientY: 300 });
    fireEvent.pointerMove(grip, { clientY: 250 });
    fireEvent.pointerUp(grip, { clientY: 250 });
    expect(screen.getByTestId('snap')).toHaveTextContent('full');

    fireEvent.click(grip);
    expect(screen.getByTestId('snap')).toHaveTextContent('full');
  });

  it('a drag down collapses the sheet again', () => {
    render(<Harness />);

    const grip = handle();
    fireEvent.pointerDown(grip, { clientY: 200 });
    fireEvent.pointerMove(grip, { clientY: 280 });
    fireEvent.pointerUp(grip, { clientY: 280 });
    expect(screen.getByTestId('snap')).toHaveTextContent('peek');
  });
});
