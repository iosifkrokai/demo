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

  it('should render MapProvider as wrapper', async () => {
    renderApp();
    expect(await screen.findByTestId('map-provider')).toBeInTheDocument();
  });

  it('should render MapComponent', async () => {
    renderApp();
    expect(await screen.findByTestId('map-component')).toBeInTheDocument();
  });

  it('should render Sidebar', async () => {
    renderApp();
    expect(await screen.findByTestId('sidebar')).toBeInTheDocument();
  });

  it('should render Toaster with correct props', async () => {
    renderApp();
    const toaster = await screen.findByTestId('toaster');
    expect(toaster).toBeInTheDocument();
    expect(toaster).toHaveAttribute('data-position', 'bottom-center');
    expect(toaster).toHaveAttribute('data-duration', '5000');
  });

  it('should render all components inside MapProvider', async () => {
    renderApp();
    const mapProvider = await screen.findByTestId('map-provider');
    expect(mapProvider).toContainElement(
      await screen.findByTestId('map-component')
    );
    expect(mapProvider).toContainElement(await screen.findByTestId('sidebar'));
    expect(mapProvider).toContainElement(await screen.findByTestId('toaster'));
  });
});
