import { MapProvider } from 'react-map-gl/maplibre';
import { MapComponent } from './components/map';
import { Sidebar } from './components/sidebar';
import { Toaster } from '@/components/ui/sonner';

export const App = () => {
  return (
    <MapProvider>
      <MapComponent />
      <Sidebar />
      <Toaster position="bottom-center" duration={5000} richColors />
    </MapProvider>
  );
};
