/** Keep authentication in the same-origin top window, including embedded tools. */
export function authWindow(): Window {
  try {
    if (window.top && window.top.location.origin === window.location.origin) return window.top;
  } catch { /* A cross-origin host cannot share this application's login route. */ }
  return window;
}

export function safeReturnPath(value: string | null | undefined): string {
  if (!value || !value.startsWith('/') || value.startsWith('//')) return '/';
  try {
    const decoded = decodeURIComponent(value);
    if (decoded.startsWith('//') || /[\\\u0000-\u001f\u007f-\u009f]/.test(value + decoded)) return '/';
    const url = new URL(value, window.location.origin);
    const pathname = decodeURIComponent(url.pathname);
    if (url.origin !== window.location.origin || pathname.startsWith('//') || /^\/login(?:\/|$)/i.test(pathname)) return '/';
    return url.pathname + url.search + url.hash;
  } catch { return '/'; }
}

export function currentReturnPath(): string {
  const { pathname, search, hash } = authWindow().location;
  return safeReturnPath(pathname + search + hash);
}

export function loginUrl(): string {
  return '/login?next=' + encodeURIComponent(currentReturnPath());
}

export function redirectToLogin(): void {
  localStorage.removeItem('token');
  localStorage.removeItem('user');
  const target = authWindow();
  if (!/^\/login\/?$/i.test(target.location.pathname)) target.location.replace(loginUrl());
}
