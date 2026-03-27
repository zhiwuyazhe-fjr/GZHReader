import { Toaster } from 'sonner';
import { Outlet } from 'react-router-dom';

import Nav from '../components/Nav';

export function BaseLayout() {
  return (
    <div className="rss-shell">
      <div className="rss-frame">
        <Nav />
        <main className="rss-page">
          <Outlet />
        </main>
      </div>
      <Toaster
        richColors
        position="top-right"
        toastOptions={{
          classNames: {
            toast: 'rss-toast',
            title: 'rss-toast-title',
            description: 'rss-toast-description',
          },
        }}
      />
    </div>
  );
}
