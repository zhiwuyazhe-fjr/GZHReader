export type ThemePreference = 'system' | 'light' | 'dark';

export const themeStorageKey = 'gzhreader.theme';
export const themeCookieKey = 'gzhreader_theme';

export const readThemeCookie = () => {
  const prefix = `${themeCookieKey}=`;
  return (
    document.cookie
      .split(';')
      .map((part) => part.trim())
      .find((part) => part.startsWith(prefix))
      ?.slice(prefix.length) || ''
  ) as ThemePreference | '';
};

export const writeThemePreference = (value: ThemePreference) => {
  window.localStorage.setItem(themeStorageKey, value);
  document.cookie = `${themeCookieKey}=${value}; path=/; max-age=${60 * 60 * 24 * 365}; samesite=lax`;
};

export const readThemePreference = (): ThemePreference => {
  const fromCookie = readThemeCookie();
  if (fromCookie === 'system' || fromCookie === 'light' || fromCookie === 'dark') {
    return fromCookie;
  }
  const fromStorage = window.localStorage.getItem(themeStorageKey);
  if (fromStorage === 'system' || fromStorage === 'light' || fromStorage === 'dark') {
    return fromStorage;
  }
  return 'system';
};
