import { StrictMode } from 'react';
import ReactDOM from 'react-dom/client';
import './index.css';
import './i18n';
import { RouterProvider } from '@tanstack/react-router';
import * as TanStackQueryProvider from './lib/tanstack-query/root-provider';
import { router } from './routes';
import { register } from './lib/service-worker-registry';
import { installGeoSim } from './lib/geo-sim';

installGeoSim();
register();

const TanStackQueryProviderContext = TanStackQueryProvider.getContext();

const rootElement = document.getElementById('valhalla-app-root');

const clientId = import.meta.env.VITE_CLIENT_ID;
if (!clientId || clientId === 'unknown-web-app') {
  console.warn(
    `VITE_CLIENT_ID is not configured (current value: ${clientId ? `"${clientId}"` : 'unset'}). Valhalla requests will be sent with a generic X-Client-Id — set VITE_CLIENT_ID in .env to identify this deployment.`
  );
}

if (rootElement && !rootElement.innerHTML) {
  const root = ReactDOM.createRoot(rootElement);
  root.render(
    <StrictMode>
      <TanStackQueryProvider.Provider {...TanStackQueryProviderContext}>
        <RouterProvider router={router} />
      </TanStackQueryProvider.Provider>
    </StrictMode>
  );
}
