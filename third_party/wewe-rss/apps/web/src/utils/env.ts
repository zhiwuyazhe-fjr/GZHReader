export const isProd = import.meta.env.PROD;

declare global {
  interface Window {
    __WEWE_RSS_SERVER_ORIGIN_URL__?: string;
    __WEWE_RSS_ENABLED_AUTH_CODE__?: boolean;
  }
}

const rawServerOrigin = isProd
  ? window.__WEWE_RSS_SERVER_ORIGIN_URL__
  : import.meta.env.VITE_SERVER_ORIGIN_URL;

export const serverOriginUrl =
  typeof rawServerOrigin === 'string' && rawServerOrigin.trim()
    ? rawServerOrigin
    : window.location.origin;

export const appVersion = __APP_VERSION__;

const rawEnabledAuthCode = window.__WEWE_RSS_ENABLED_AUTH_CODE__ as unknown;

export const enabledAuthCode = (() => {
  if (rawEnabledAuthCode === false || rawEnabledAuthCode === 'false') {
    return false;
  }
  if (rawEnabledAuthCode === true || rawEnabledAuthCode === 'true') {
    return true;
  }
  return false;
})();
