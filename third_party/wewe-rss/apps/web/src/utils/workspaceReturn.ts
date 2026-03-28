const workspaceCookieKey = 'gzhreader_workspace_url';
const workspaceFallbackPort = '8765';

const readCookie = (name: string) => {
  const prefix = `${name}=`;
  const rawValue =
    document.cookie
      .split(';')
      .map((part) => part.trim())
      .find((part) => part.startsWith(prefix))
      ?.slice(prefix.length) || '';

  try {
    return decodeURIComponent(rawValue);
  } catch {
    return rawValue;
  }
};

export const writeWorkspaceReturn = (value: string) => {
  document.cookie = `${workspaceCookieKey}=${encodeURIComponent(
    value,
  )}; path=/; max-age=${60 * 60 * 24 * 365}; samesite=lax`;
};

export const readWorkspaceReturn = () => {
  const query = new URLSearchParams(window.location.search);
  const fromQuery = query.get('return_to');
  if (fromQuery) {
    return fromQuery;
  }

  const fromCookie = readCookie(workspaceCookieKey);
  if (fromCookie) {
    return fromCookie;
  }

  return `${window.location.protocol}//${window.location.hostname}:${workspaceFallbackPort}/`;
};

export const syncWorkspaceReturn = () => {
  const query = new URLSearchParams(window.location.search);
  const fromQuery = query.get('return_to');
  if (fromQuery) {
    writeWorkspaceReturn(fromQuery);
  }
};

export const returnToWorkspace = () => {
  const targetUrl = readWorkspaceReturn();
  writeWorkspaceReturn(targetUrl);

  if (window.opener && !window.opener.closed) {
    try {
      window.opener.postMessage(
        {
          type: 'gzhreader:return-workspace',
          url: targetUrl,
        },
        '*',
      );
      window.close();
      window.setTimeout(() => {
        if (!window.closed) {
          window.location.href = targetUrl;
        }
      }, 180);
      return;
    } catch {
      // Fall back to in-tab navigation below.
    }
  }

  window.location.href = targetUrl;
};
