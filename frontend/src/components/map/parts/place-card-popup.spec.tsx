import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { PlaceCardPopup } from './place-card-popup';
import type { PlaceDetails } from '@/stores/directions-store';

vi.mock('@/hooks/use-auth', () => ({
  useAuth: () => ({
    user: null,
    authenticated: false,
    isAdmin: false,
    isLoading: false,
    refetch: () => {},
  }),
  describeAccountError: () => '',
}));
vi.mock('@/hooks/use-visited', () => ({
  useVisitedIds: () => new Set<number>(),
  useToggleVisited: () => ({ mutate: () => {}, isPending: false }),
}));

vi.mock('react-map-gl/maplibre', () => ({
  Popup: ({
    children,
    longitude,
    latitude,
  }: {
    children: React.ReactNode;
    longitude: number;
    latitude: number;
  }) => (
    <div
      data-testid="popup"
      data-longitude={longitude}
      data-latitude={latitude}
    >
      {children}
    </div>
  ),
}));

const details = (overrides: Partial<PlaceDetails> = {}): PlaceDetails => ({
  name: 'Костел Франциска Ассизского',
  category: 'place_of_worship',
  blurb: 'Барочный храм на площади Советской.',
  funFact: 'В башне костёла до сих пор идут часы работы 1750 года.',
  funFacts: [],
  links: [],
  visitMinutes: 20,
  ...overrides,
});

const defaultProps = {
  lng: 23.828,
  lat: 53.688,
  onClose: vi.fn(),
};

describe('PlaceCardPopup', () => {
  it('should render without crashing', () => {
    expect(() =>
      render(<PlaceCardPopup {...defaultProps} details={details()} />)
    ).not.toThrow();
  });

  it('should render Popup at marker coordinates', () => {
    render(<PlaceCardPopup {...defaultProps} details={details()} />);

    const popup = screen.getByTestId('popup');
    expect(popup).toHaveAttribute('data-longitude', '23.828');
    expect(popup).toHaveAttribute('data-latitude', '53.688');
  });

  it('should show name, blurb and fun fact', () => {
    render(<PlaceCardPopup {...defaultProps} details={details()} />);

    expect(screen.getByText('Костел Франциска Ассизского')).toBeInTheDocument();
    expect(
      screen.getByText('Барочный храм на площади Советской.')
    ).toBeInTheDocument();
    expect(
      screen.getByText('В башне костёла до сих пор идут часы работы 1750 года.')
    ).toBeInTheDocument();
  });

  it('should show category and suggested visit time', () => {
    render(<PlaceCardPopup {...defaultProps} details={details()} />);

    expect(screen.getByText('place_of_worship')).toBeInTheDocument();
    expect(screen.getByText('~20 мин')).toBeInTheDocument();
  });

  it('should omit empty fields without breaking layout', () => {
    render(
      <PlaceCardPopup
        {...defaultProps}
        details={details({
          category: null,
          blurb: null,
          funFact: null,
          visitMinutes: null,
        })}
      />
    );

    expect(screen.getByText('Костел Франциска Ассизского')).toBeInTheDocument();
    expect(screen.queryByText('~20 мин')).not.toBeInTheDocument();
  });

  it('should call onClose when the close button is clicked', () => {
    const onClose = vi.fn();
    render(
      <PlaceCardPopup {...defaultProps} onClose={onClose} details={details()} />
    );

    fireEvent.click(screen.getByLabelText('Закрыть'));

    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
