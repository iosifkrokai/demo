import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { App } from './app';

vi.mock('react-map-gl/maplibre', () => ({
  MapProvider: ({ children }: { children: React.ReactNode }) => (
    <div data-testid="map-provider">{children}</div>
  ),
}));

vi.mock('./components/map', () => ({
  MapComponent: () => <div data-testid="map-component">MapComponent</div>,
}));

vi.mock('./components/sidebar', () => ({
  Sidebar: () => <div data-testid="sidebar">Sidebar</div>,
}));

vi.mock('@/components/ui/sonner', () => ({
  Toaster: ({ position, duration }: { position: string; duration: number }) => (
    <div
      data-testid="toaster"
      data-position={position}
      data-duration={duration}
    >
      Toaster
    </div>
  ),
}));

const renderApp = () =>
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <App />
    </QueryClientProvider>
  );

describe('App', () => {
  it('should render without crashing', () => {
    expect(() => renderApp()).not.toThrow();
  });

  it('should render MapProvider as wrapper', () => {
    renderApp();
    expect(screen.getByTestId('map-provider')).toBeInTheDocument();
  });

  it('should render MapComponent', () => {
    renderApp();
    expect(screen.getByTestId('map-component')).toBeInTheDocument();
  });

  it('should render Sidebar', () => {
    renderApp();
    expect(screen.getByTestId('sidebar')).toBeInTheDocument();
  });

  it('should render Toaster with correct props', () => {
    renderApp();
    const toaster = screen.getByTestId('toaster');
    expect(toaster).toBeInTheDocument();
    expect(toaster).toHaveAttribute('data-position', 'bottom-center');
    expect(toaster).toHaveAttribute('data-duration', '5000');
  });

  it('should render all components inside MapProvider', () => {
    renderApp();
    const mapProvider = screen.getByTestId('map-provider');
    expect(mapProvider).toContainElement(screen.getByTestId('map-component'));
    expect(mapProvider).toContainElement(screen.getByTestId('sidebar'));
    expect(mapProvider).toContainElement(screen.getByTestId('toaster'));
  });
});
