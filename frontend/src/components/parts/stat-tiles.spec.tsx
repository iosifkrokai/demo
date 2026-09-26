import { describe, it, expect, afterEach } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';

import { StatTile, StatTiles } from './stat-tiles';

afterEach(cleanup);

describe('StatTile', () => {
  it('renders a bare label verbatim', () => {
    render(<StatTile value="1,3" label="км" />);

    expect(screen.getByText('1,3')).toBeInTheDocument();
    expect(screen.getByText('км')).toBeInTheDocument();
  });

  it('inflects a counted noun from count + unit', () => {
    render(<StatTile value={3} count={3} unit="points" />);

    expect(screen.getByText('точки')).toBeInTheDocument();
  });

  it('picks the singular and the many form too', () => {
    const { unmount } = render(<StatTile value={1} count={1} unit="points" />);
    expect(screen.getByText('точка')).toBeInTheDocument();
    unmount();

    render(<StatTile value={7} count={7} unit="points" />);
    expect(screen.getByText('точек')).toBeInTheDocument();
  });

  it('keeps 11–14 on the many form', () => {
    render(<StatTile value={12} count={12} unit="points" />);

    expect(screen.getByText('точек')).toBeInTheDocument();
  });

  it('inflects stops and minutes', () => {
    const { unmount } = render(<StatTile value={3} count={3} unit="stops" />);
    expect(screen.getByText('остановки')).toBeInTheDocument();
    unmount();

    render(<StatTile value={40} count={40} unit="minutes" />);
    expect(screen.getByText('минут')).toBeInTheDocument();
  });

  it('falls back to the plain label when count is unknown', () => {
    render(<StatTile value="—" unit="points" />);

    expect(screen.getByText('—')).toBeInTheDocument();
    expect(screen.queryByText('точек')).toBeNull();
  });
});

describe('StatTiles', () => {
  it('lays three tiles out in a row', () => {
    const { container } = render(
      <StatTiles>
        <StatTile value={3} count={3} unit="points" />
        <StatTile value="1,3" label="км" />
        <StatTile value={15} label="мин в пути" />
      </StatTiles>
    );

    expect(container.firstChild).toHaveClass('grid-cols-3');
    expect(screen.getByText('точки')).toBeInTheDocument();
    expect(screen.getByText('1,3')).toBeInTheDocument();
    expect(screen.getByText('мин в пути')).toBeInTheDocument();
  });
});
