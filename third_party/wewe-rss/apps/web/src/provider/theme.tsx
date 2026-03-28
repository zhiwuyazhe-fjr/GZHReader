import { NextUIProvider } from '@nextui-org/react';
import { ThemeProvider as NextThemesProvider } from 'next-themes';
import { useTheme } from 'next-themes';
import { useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { readThemePreference, themeStorageKey } from '@web/utils/themeSync';
import { syncWorkspaceReturn } from '@web/utils/workspaceReturn';

function SharedThemeBridge() {
  const { setTheme } = useTheme();

  useEffect(() => {
    const syncTheme = () => {
      setTheme(readThemePreference());
      syncWorkspaceReturn();
    };

    syncTheme();
    const timer = window.setInterval(() => {
      if (!document.hidden) {
        syncTheme();
      }
    }, 1200);

    window.addEventListener('focus', syncTheme);
    document.addEventListener('visibilitychange', syncTheme);

    return () => {
      window.clearInterval(timer);
      window.removeEventListener('focus', syncTheme);
      document.removeEventListener('visibilitychange', syncTheme);
    };
  }, [setTheme]);

  return null;
}

function ThemeProvider({ children }: { children: React.ReactNode }) {
  const navigate = useNavigate();

  return (
    <NextUIProvider navigate={navigate}>
      <NextThemesProvider
        attribute="data-theme"
        defaultTheme={readThemePreference()}
        enableSystem
        storageKey={themeStorageKey}
        disableTransitionOnChange
      >
        <SharedThemeBridge />
        {children}
      </NextThemesProvider>
    </NextUIProvider>
  );
}

export default ThemeProvider;
