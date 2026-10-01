import { MapProvider } from 'react-map-gl/maplibre';
import { MapComponent } from './components/map';
import { MobileShell } from './components/mobile/mobile-shell';
import { useIsMobile } from './components/mobile/use-is-mobile';
import { Sidebar } from './components/sidebar';
import { Toaster } from '@/components/ui/sonner';

export const App = () => {
  // The ONE viewport branch in the app. Everything below belongs to exactly one
  // of the two shells — the desktop column or the mobile sheet — and neither
  // reaches into the other (docs/specs/004-mobile-first-frontend).
  const isMobile = useIsMobile();

  return (
    <MapProvider>
      <MapComponent />
      {isMobile ? <MobileShell /> : <Sidebar />}
      <Toaster position="bottom-center" duration={5000} richColors />
    </MapProvider>
  );
};
