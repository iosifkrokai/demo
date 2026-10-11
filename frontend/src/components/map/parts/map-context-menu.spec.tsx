import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MapContextMenu } from './map-context-menu';

const mockAddWaypointAtIndex = vi.fn();
const mockUseDirectionsStore = vi.fn();

vi.mock('@/stores/directions-store', () => ({
  useDirectionsStore: (selector: (state: unknown) => unknown) =>
    mockUseDirectionsStore(selector),
}));

const defaultProps = {
  onAddWaypoint: vi.fn(),
  popupLocation: { lng: 10.5, lat: 50.5 },
};

describe('MapContextMenu', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockUseDirectionsStore.mockImplementation((selector) => {
      const state = {
        waypoints: [{}, {}, {}],
        addWaypointAtIndex: mockAddWaypointAtIndex,
      };
      return selector(state);
    });
  });

  describe('directions tab', () => {
    it('should render without crashing', () => {
      expect(() => render(<MapContextMenu {...defaultProps} />)).not.toThrow();
    });

    it('should render "Маршрут отсюда" button', () => {
      render(<MapContextMenu {...defaultProps} />);

      expect(
        screen.getByRole('button', { name: 'Маршрут отсюда' })
      ).toBeInTheDocument();
    });

    it('should render "Добавить как промежуточную" button', () => {
      render(<MapContextMenu {...defaultProps} />);

      expect(
        screen.getByRole('button', { name: 'Добавить как промежуточную' })
      ).toBeInTheDocument();
    });

    it('should render "Маршрут сюда" button', () => {
      render(<MapContextMenu {...defaultProps} />);

      expect(
        screen.getByRole('button', { name: 'Маршрут сюда' })
      ).toBeInTheDocument();
    });

    it('should call onAddWaypoint(0) when "Маршрут отсюда" is clicked', async () => {
      const user = userEvent.setup();
      const onAddWaypoint = vi.fn();

      render(
        <MapContextMenu {...defaultProps} onAddWaypoint={onAddWaypoint} />
      );

      await user.click(screen.getByRole('button', { name: 'Маршрут отсюда' }));

      expect(onAddWaypoint).toHaveBeenCalledWith(0);
    });

    it('should call onAddWaypoint with last index when "Маршрут сюда" is clicked', async () => {
      const user = userEvent.setup();
      const onAddWaypoint = vi.fn();

      render(
        <MapContextMenu {...defaultProps} onAddWaypoint={onAddWaypoint} />
      );

      await user.click(screen.getByRole('button', { name: 'Маршрут сюда' }));

      expect(onAddWaypoint).toHaveBeenCalledWith(2);
    });

    it('should add waypoint and call onAddWaypoint when "Добавить как промежуточную" is clicked', async () => {
      const user = userEvent.setup();
      const onAddWaypoint = vi.fn();
      const popupLocation = { lng: 10.5, lat: 50.5 };

      render(
        <MapContextMenu
          {...defaultProps}
          onAddWaypoint={onAddWaypoint}
          popupLocation={popupLocation}
        />
      );

      await user.click(
        screen.getByRole('button', { name: 'Добавить как промежуточную' })
      );

      expect(mockAddWaypointAtIndex).toHaveBeenCalledWith({
        index: 2,
        placeholder: popupLocation,
      });
      expect(onAddWaypoint).toHaveBeenCalledWith(2);
    });
  });
});
